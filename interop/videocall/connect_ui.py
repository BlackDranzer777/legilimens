"""Authenticated UI -> backend -> WebTransport connectivity test for the real Dioxus UI.

Brings up the same owned local stack as run_smoke.py (NATS, PostgreSQL, meeting API,
WebTransport server) plus a Legilimens proxy, serves the REAL built Dioxus UI wired to
those ports with an exact local CORS origin, and drives it in Chrome for two disposable
authenticated sessions over actual WebTransport — direct and through Legilimens — with
missing/invalid-auth and wrong-pin negatives. Ends at authenticated WebTransport
connectivity; it does NOT exchange or verify media.

    python interop/videocall/connect_ui.py --node node

Loopback-only, disposable state/credentials, owned-resource cleanup. No public services.
"""
import argparse
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preflight import ROOT, MANIFEST, inspect_source
from run_smoke import jwt, http, wait_for, IMAGES
sys.path.insert(0, str(ROOT / "interop/harness"))
sys.path.insert(0, str(ROOT / "python"))
from support import Proc, allocate_ports, port_free
from certs import ensure_cert

DEFAULT_PLAYWRIGHT = Path(
    r"C:/Users/divya/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs")


def flip_pin(pin_b64: str) -> str:
    raw = bytearray(base64.b64decode(pin_b64))
    raw[0] ^= 0xFF
    return base64.b64encode(bytes(raw)).decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--output", type=Path, default=ROOT / "build/videocall-connect")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").unlink(missing_ok=True)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checkout = ROOT / manifest["checkout"]
    inspect_source(checkout, manifest)
    dist = ROOT / "build/videocall-frontend/dist"
    if not (dist / "index.html").exists():
        raise SystemExit("built UI missing; run interop/videocall/build_frontend.py first")

    playwright = os.environ.get("PLAYWRIGHT_MODULE")
    if not playwright and DEFAULT_PLAYWRIGHT.exists():
        playwright = DEFAULT_PLAYWRIGHT.as_uri()

    kinds = {"upstream": socket.SOCK_DGRAM, "proxy": socket.SOCK_DGRAM, "target": socket.SOCK_DGRAM,
             "meeting": socket.SOCK_STREAM, "health": socket.SOCK_STREAM,
             "api": socket.SOCK_STREAM, "ws": socket.SOCK_STREAM, "ui": socket.SOCK_STREAM,
             "unused_ws": socket.SOCK_STREAM}
    ports = allocate_ports(kinds)
    owner = "legilimens-vc-" + secrets.token_hex(6)
    secret, control, password = (secrets.token_urlsafe(32) for _ in range(3))
    ui_origin = f"http://127.0.0.1:{ports['ui']}"
    containers, processes, network_created = [], [], False
    report = {"passed": False, "commit": manifest["commit"], "ports": ports, "routes": {},
              "scope": "authenticated UI->backend WebTransport connectivity; no media claim"}

    def redact(text):
        for value in (secret, control, password):
            text = text.replace(value, "[redacted]")
        return re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[jwt-redacted]", text)

    def docker(*argv, stdin=None, timeout=120):
        proc = subprocess.run(["docker", *argv], input=stdin, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if proc.returncode:
            raise RuntimeError(redact(proc.stderr[-3000:]))
        return (proc.stdout + (proc.stderr if argv[0] == "logs" else "")).strip()

    def run(name, image, options, command):
        cid = docker("run", "-d", "--name", owner + "-" + name, "--label", "org.legilimens.run=" + owner,
                     "--network", owner, "--network-alias", name, "--memory", "512m", "--cpus", "2",
                     *options, image, *command)
        containers.append(cid)
        return cid

    with tempfile.TemporaryDirectory(prefix="legilimens-vc-connect-") as scratch:
        scratch = Path(scratch)
        cert_dir = scratch / "target"
        target_pin = ensure_cert(cert_dir).cert_hash
        served = None
        httpd = None
        t = None
        try:
            report["images"] = {k: docker("image", "inspect", v, "--format", "{{.Id}}") for k, v in IMAGES.items()}
            docker("network", "create", "--label", "org.legilimens.run=" + owner, owner)
            network_created = True
            run("nats", IMAGES["nats"], ["--read-only"], [])
            db = run("db", IMAGES["postgres"], ["--tmpfs", "/var/lib/postgresql",
                     "-e", "POSTGRES_PASSWORD=" + password, "-e", "POSTGRES_DB=videocall"], [])
            wait_for(lambda: docker("exec", db, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "videocall"))
            docker("exec", "-i", db, "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "videocall",
                   stdin=(checkout / "dbmate/db/schema.sql").read_text(encoding="utf-8"))
            shared = ["--read-only", "--tmpfs", "/tmp", "-e", "JWT_SECRET=" + secret,
                      "-e", "NATS_URL=nats://nats:4222", "-e", "FEATURE_MEETING_MANAGEMENT=true", "-e", "RUST_LOG=info"]
            run("meeting", IMAGES["backend"], [*shared,
                "-p", f"127.0.0.1:{ports['meeting']}:8081",
                "-e", f"DATABASE_URL=postgres://postgres:{password}@db:5432/videocall",
                "-e", "COOKIE_SECURE=false", "-e", "TOKEN_TTL_SECS=300",
                "-e", f"CORS_ALLOWED_ORIGIN={ui_origin}"], ["/src/target/debug/meeting-api"])
            transport_cid = run("transport", IMAGES["backend"], [*shared,
                "--mount", f"type=bind,source={cert_dir},target=/certs,readonly",
                "-p", f"127.0.0.1:{ports['upstream']}:4433/udp", "-p", f"127.0.0.1:{ports['health']}:8080",
                "-e", "LISTEN_URL=0.0.0.0:4433", "-e", "HEALTH_LISTEN_URL=0.0.0.0:8080",
                "-e", "KEY_PATH=/certs/key.pem", "-e", "CERT_PATH=/certs/cert.pem"],
                ["/src/target/debug/webtransport_server"])
            wait_for(lambda: http(ports["meeting"], "/version"), label="meeting API")
            wait_for(lambda: http(ports["health"], "/version"), label="transport health")

            # Legilimens proxy in front of the transport server, pinned to its cert.
            proxy = Proc([sys.executable, str(ROOT / "python/backend.py"),
                *[a for n in ("proxy", "target", "api", "ws") for a in ("--port-" + n, str(ports[n]))]], ROOT,
                env={"LEGILIMENS_CERTS_DIR": str(scratch / "proxy"), "LEGILIMENS_CONTROL_TOKEN": control,
                     "LEGILIMENS_PARENT_PID": str(os.getpid()), "PYTHONUTF8": "1"})
            processes.append(proxy)
            if not proxy.wait_line(lambda line: line.startswith("READY instanceId="), 30):
                raise RuntimeError("Legilimens did not become ready")
            http(ports["api"], "/target", control, {"host": "127.0.0.1", "port": ports["upstream"], "certHash": target_pin})
            http(ports["api"], "/intercept", control, {"action": "start"})
            proxy_pin = http(ports["api"], "/cert-hash", control)["hash"]

            # Serve the real UI once (one origin -> one exact CORS origin). connect-src must
            # allow BOTH transport origins since we swap webTransportHost between routes.
            import serve_ui
            direct_url = f"https://127.0.0.1:{ports['upstream']}"
            proxied_url = f"https://127.0.0.1:{ports['proxy']}"
            # No WebSocket media server: never point the app at Legilimens' capture WS.
            ws_url = f"ws://127.0.0.1:{ports['unused_ws']}"
            served = serve_ui.stage(dist, ports["meeting"], ports["unused_ws"], direct_url)
            httpd = serve_ui.serve(served, ports["ui"], ports["meeting"], ports["unused_ws"], direct_url)
            httpd.csp = httpd.csp.replace("connect-src 'self'", f"connect-src 'self' {proxied_url}")
            import threading
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()

            def session_jwt(sub, name):
                return jwt(secret, {"sub": sub, "name": name, "iss": "videocall-meeting-backend",
                                    "iat": int(time.time()), "exp": int(time.time()) + 600})

            routes = [("direct", direct_url, target_pin), ("proxied", proxied_url, proxy_pin)]
            for route, wt_url, pin in routes:
                (served / "config.js").write_text(serve_ui.local_config(ports["meeting"], ports["unused_ws"], wt_url),
                                                  encoding="utf-8")
                rooms = {k: f"{route}_{k}_{secrets.token_hex(4)}" for k in ("a", "b", "bad", "missing", "wrong")}
                route_report = output / f"{route}.json"
                route_report.unlink(missing_ok=True)
                conn = {"uiUrl": f"http://127.0.0.1:{ports['ui']}/", "route": route, "webTransportHost": wt_url,
                        "apiOrigin": f"http://127.0.0.1:{ports['meeting']}", "transportContainer": transport_cid,
                        "wsUrl": ws_url, "pin": pin, "wrongPin": flip_pin(pin), "rooms": rooms,
                        "sessions": [session_jwt("local-a", "Local A"), session_jwt("local-b", "Local B")],
                        "badJwt": jwt("wrong-secret", {"sub": "x", "name": "x", "iss": "videocall-meeting-backend",
                                                       "iat": int(time.time()), "exp": int(time.time()) + 600}),
                        "report": str(route_report)}
                env = {"VIDEOCALL_CONNECT": json.dumps(conn), "PYTHONUTF8": "1"}
                if playwright:
                    env["PLAYWRIGHT_MODULE"] = playwright
                driver = Proc([args.node, str(Path(__file__).with_name("connect-ui.mjs"))], ROOT, env=env, tree=True)
                processes.append(driver)
                exit_code = driver.p.wait(timeout=240)
                (output / f"{route}-driver.log").write_text(redact("\n".join(driver.snapshot())), encoding="utf-8")
                logs = docker("logs", transport_cid)
                joined = {k: bool(re.search(r"Successfully joined room " + re.escape(room) + r"(?:\s|$)", logs))
                          for k, room in rooms.items()}
                data = json.loads(route_report.read_text()) if route_report.exists() else {"cases": {}}
                # Require BOTH browser-native readiness/explicit rejection and this run's
                # attributed server log. No join alone is not a passing negative test.
                verdict = {
                    "sessionA_joined": joined["a"], "sessionB_joined": joined["b"],
                    "badAuth_rejected": not joined["bad"] and data.get("cases", {}).get("badAuth", {}).get("passed") is True,
                    "missingAuth_rejected": not joined["missing"] and data.get("cases", {}).get("missingAuth", {}).get("passed") is True,
                    "wrongPin_rejected": not joined["wrong"] and data.get("cases", {}).get("wrongPin", {}).get("passed") is True,
                    "browserPassed": exit_code == 0 and data.get("passed") is True,
                }
                verdict["passed"] = (verdict["sessionA_joined"] and verdict["sessionB_joined"]
                                     and verdict["badAuth_rejected"] and verdict["missingAuth_rejected"]
                                     and verdict["wrongPin_rejected"] and verdict["browserPassed"])
                report["routes"][route] = {"backendVerified": verdict, "rooms": rooms,
                                           "browserEvidence": data.get("cases", {}), "exit": exit_code}

            report["passed"] = set(report["routes"]) == {"direct", "proxied"} and all(
                r["backendVerified"]["passed"] for r in report["routes"].values())
        except Exception as exc:
            report["error"] = redact(str(exc))
        finally:
            errors = []
            if httpd is not None:
                if t is not None:
                    httpd.shutdown()
                    t.join(timeout=5)
                    if t.is_alive():
                        errors.append("UI server thread did not stop")
                httpd.server_close()
            for proc in reversed(processes):
                try:
                    proc.stop()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            for cid in reversed(containers):
                try:
                    (output / (cid[:12] + ".log")).write_text(redact(docker("logs", cid)), encoding="utf-8")
                except Exception:
                    pass
                try:
                    docker("rm", "-f", "-v", cid)
                except Exception as exc:
                    errors.append(redact(str(exc)))
            if network_created:
                try:
                    docker("network", "rm", owner)
                except Exception as exc:
                    errors.append(redact(str(exc)))
            if served is not None:
                import shutil
                shutil.rmtree(served, ignore_errors=True)
                if served.exists():
                    errors.append("Temporary UI directory not removed")
            try:
                wait_for(lambda: all(port_free(ports[n], k) for n, k in kinds.items()), 10)
            except TimeoutError:
                errors.append("Ports not released")
            report["cleanup"] = not errors
            report["cleanupErrors"] = errors
            report["passed"] = report["passed"] and report["cleanup"]

    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cleanup": report.get("cleanup"),
                      "routes": {k: v.get("backendVerified") for k, v in report["routes"].items()},
                      "error": report.get("error")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
