"""Serve the built upstream Dioxus UI locally with an all-local configuration overlay.

The upstream dist/ is served unchanged except for scripts/config.js, which is replaced
with local-only endpoints and OAuth disabled, so the browser makes no requests to public
auth/analytics/media services. WASM is served with the correct MIME type and the page is
cross-origin isolated (COOP/COEP). A CSP blocks external resources, including the
upstream app's hardcoded analytics. OAuth-off alone does not disable analytics.

  # manual demo (Ctrl+C to stop); prints the local URL
  python interop/videocall/serve_ui.py --dist build/videocall-frontend/dist

Endpoints default to loopback placeholders for a load-only check; pass real local ports
when wiring the backend for the media test.
"""
import argparse
import json
import shutil
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def local_endpoint(url, scheme):
    parsed = urlsplit(url)
    if (parsed.scheme != scheme or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.port is None or not 1 <= parsed.port <= 65535):
        raise ValueError("Endpoint must be a loopback origin with an explicit port")
    return f"{parsed.scheme}://{parsed.netloc}"


def local_config(meeting_port, ws_port, wt_url):
    api_url = local_endpoint(f"http://127.0.0.1:{meeting_port}", "http")
    ws_url = local_endpoint(f"ws://127.0.0.1:{ws_port}", "ws")
    wt_url = local_endpoint(wt_url, "https")
    cfg = {
        "apiBaseUrl": api_url,
        "wsUrl": ws_url,
        "webTransportHost": wt_url,
        "oauthEnabled": "false",       # no public Google OAuth
        "e2eeEnabled": "false",
        "webTransportEnabled": "true",
        "firefoxEnabled": "false",
        "usersAllowedToStream": "",
        "serverElectionPeriodMs": 2000,
        "audioBitrateKbps": 65,
        "videoBitrateKbps": 200,
        "screenBitrateKbps": 200,
        "oauthProvider": "google",
        "vadThreshold": 0.02,
    }
    return "window.__APP_CONFIG = Object.freeze(" + json.dumps(cfg) + ");\n"


def stage(dist: Path, meeting_port, ws_port, wt_url) -> Path:
    """Copy dist to a temp dir and overlay a local-only config.js. Returns the dir."""
    config = local_config(meeting_port, ws_port, wt_url)
    served = Path(tempfile.mkdtemp(prefix="legilimens-vc-ui-"))
    try:
        shutil.copytree(dist, served, dirs_exist_ok=True)
        (served / "config.js").write_text(config, encoding="utf-8")
    except Exception:
        shutil.rmtree(served)
        raise
    return served


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".wasm": "application/wasm", ".js": "text/javascript", ".mjs": "text/javascript"}

    def end_headers(self):
        # Cross-origin isolation for WASM workers / SharedArrayBuffer; no external fetches.
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        # NetEq embeds an eval-based Opus decoder; retain eval for this local fixture.
        self.send_header("Content-Security-Policy", self.server.csp)
        super().end_headers()

    def log_request(self, code="-", size="-"):
        if len(self.server.requests) < 256:
            self.server.requests.append({"path": urlsplit(self.path).path, "status": int(code)})

    def do_GET(self):
        if urlsplit(self.path).path == "/favicon.ico" and not Path(self.directory, "favicon.ico").exists():
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *args):
        pass


def serve(served: Path, port: int, meeting_port=8081, ws_port=8080, wt_url="https://127.0.0.1:4433"):
    origins = [local_endpoint(f"http://127.0.0.1:{meeting_port}", "http"),
               local_endpoint(f"ws://127.0.0.1:{ws_port}", "ws"), local_endpoint(wt_url, "https")]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(served)))
    httpd.requests = []
    # The upstream PCM player registers a locally created blob AudioWorklet module.
    httpd.csp = ("default-src 'none'; script-src 'self' blob: 'unsafe-inline' 'unsafe-eval'; "
                 "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                 "font-src 'self' data:; media-src 'self' blob:; worker-src 'self' blob:; "
                 "connect-src 'self' " + " ".join(origins) + "; "
                 "frame-src 'none'; frame-ancestors 'none'; object-src 'none'; "
                 "base-uri 'none'; form-action 'self'")
    return httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="build/videocall-frontend/dist")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--meeting-port", type=int, default=8081)
    ap.add_argument("--ws-port", type=int, default=8080)
    ap.add_argument("--wt-url", default="https://127.0.0.1:4433")
    args = ap.parse_args()
    dist = Path(args.dist).resolve()
    if not (dist / "index.html").exists():
        raise SystemExit(f"no built UI at {dist} (run interop/videocall/build_frontend.py first)")
    served = stage(dist, args.meeting_port, args.ws_port, args.wt_url)
    httpd = None
    try:
        httpd = serve(served, args.port, args.meeting_port, args.ws_port, args.wt_url)
        print(f"videocall UI (local, OAuth disabled) at: http://127.0.0.1:{httpd.server_port}/", flush=True)
        print("Ctrl+C to stop.", flush=True)
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if httpd:
            httpd.server_close()
        shutil.rmtree(served)


if __name__ == "__main__":
    main()
