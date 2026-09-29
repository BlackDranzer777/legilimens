"""Phase 1 automated check: serve the built Dioxus UI locally and verify in Chrome that
it loads, its WASM app + worker assets are served, the runtime config is local (OAuth off),
and no request reaches a non-loopback host. Cleans up its server and temp files.

    python interop/videocall/verify_frontend.py --report build/videocall-frontend/evidence/ui.json

Not a media test. A pass means the real upstream UI builds and loads locally.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_ui

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "interop/harness"))
from support import Proc, port_free
DEFAULT_PLAYWRIGHT = Path(
    r"C:/Users/divya/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="build/videocall-frontend/dist")
    ap.add_argument("--node", default="node")
    ap.add_argument("--report", default="build/videocall-frontend/evidence/ui.json")
    args = ap.parse_args()

    dist = (ROOT / args.dist).resolve() if not Path(args.dist).is_absolute() else Path(args.dist)
    if not (dist / "index.html").exists():
        raise SystemExit(f"no built UI at {dist}; run interop/videocall/build_frontend.py first")

    report_path = (ROOT / args.report).resolve() if not Path(args.report).is_absolute() else Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot = report_path.with_suffix(".png")
    report_path.unlink(missing_ok=True)

    playwright = os.environ.get("PLAYWRIGHT_MODULE")
    if not playwright and DEFAULT_PLAYWRIGHT.exists():
        playwright = DEFAULT_PLAYWRIGHT.as_uri()

    # Placeholders are loopback: Phase 1 verifies the UI loads with local endpoints, not
    # that the backend is reachable. Real ports are wired in the later media test.
    meeting_port, ws_port, wt_port = free_port(), free_port(), free_port()
    wt_url = f"https://127.0.0.1:{wt_port}"
    served = serve_ui.stage(dist, meeting_port, ws_port, wt_url)
    httpd = serve_ui.serve(served, 0, meeting_port, ws_port, wt_url)
    port = httpd.server_port
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}/"
    proc = None
    rc = 1
    try:
        env = {**os.environ, "VERIFY_CONFIG": json.dumps(
            {"url": url, "report": str(report_path), "screenshot": str(screenshot),
             "apiOrigin": f"http://127.0.0.1:{meeting_port}", "wsOrigin": f"ws://127.0.0.1:{ws_port}",
             "wtOrigin": wt_url})}
        if playwright:
            env["PLAYWRIGHT_MODULE"] = playwright
        proc = Proc([args.node, str(Path(__file__).with_name("verify-ui.mjs"))], ROOT, env=env, tree=True)
        rc = proc.p.wait(timeout=90)
        if rc:
            print("\n".join(proc.snapshot()[-8:]))
    except subprocess.TimeoutExpired:
        report_path.write_text(json.dumps({"passed": False, "error": "Browser verification exceeded 90 seconds"}))
    finally:
        if proc:
            proc.stop()
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)
        shutil.rmtree(served)

    if report_path.exists():
        rep = json.loads(report_path.read_text())
        rep["serverRequests"] = httpd.requests
        rep["cleanup"] = not t.is_alive() and not served.exists() and port_free(port, socket.SOCK_STREAM)
        rep["passed"] = bool(rep.get("passed")) and rep["cleanup"] and rc == 0
        rc = 0 if rep["passed"] else 1
        report_path.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(json.dumps({k: rep.get(k) for k in (
            "title", "configLocal", "mounted", "workersReady", "externalResponses",
            "blockedRequests", "violations", "fatalLoadErrors", "error", "cleanup", "passed")}, indent=2))
        print("evidence:", report_path)
        print("screenshot:", screenshot if screenshot.exists() else "(none)")
    else:
        rc = 1
    print("RESULT:", "UI LOADS LOCALLY" if rc == 0 else "FAILED / see evidence")
    return rc


if __name__ == "__main__":
    sys.exit(main())
