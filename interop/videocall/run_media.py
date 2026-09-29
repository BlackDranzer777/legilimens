"""Two-participant real-UI synthetic-media verification (video decode; audio observed
or BLOCKED), direct and (only after a direct pass) through Legilimens.

Reuses the connectivity harness's owned local stack (NATS, PostgreSQL, meeting API,
WebTransport server) + Legilimens proxy, generates distinct per-participant fixtures,
serves the real Dioxus UI, and drives two Chrome participants into one room. Loopback
only, disposable state, owned-resource cleanup. No installer/production claim.

    python interop/videocall/run_media.py --node <node> --output build/videocall-media/<run-id>
"""
import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preflight import ROOT, MANIFEST, inspect_source
from run_smoke import jwt, http, wait_for, IMAGES
import media_fixtures
import serve_ui
from media_results import driver_result, video_baseline_ready, run_verdict
sys.path.insert(0, str(ROOT / "interop/harness"))
sys.path.insert(0, str(ROOT / "python"))
from support import Proc, allocate_ports, port_free
from certs import ensure_cert

DEFAULT_PLAYWRIGHT = Path(
    r"C:/Users/divya/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs")


def run(args):
    output = args.output.resolve()
    run_id = output.name

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checkout = ROOT / manifest["checkout"]
    inspect_source(checkout, manifest)
    dist = args.frontend_dist.resolve()
    if not (dist / "index.html").exists():
        raise SystemExit("built UI missing; run interop/videocall/build_frontend.py first")

    fixtures = media_fixtures.generate(output / "fixtures", run_id)
    fx = {p: {"video": os.path.abspath(fixtures["participants"][p]["video"]),
              "audio": os.path.abspath(fixtures["participants"][p]["audio"]),
              "marker": fixtures["participants"][p]["marker"]} for p in ("A", "B")}

    playwright = os.environ.get("PLAYWRIGHT_MODULE")
    if not playwright and DEFAULT_PLAYWRIGHT.exists():
        playwright = DEFAULT_PLAYWRIGHT.as_uri()

    kinds = {"upstream": socket.SOCK_DGRAM, "proxy": socket.SOCK_DGRAM, "target": socket.SOCK_DGRAM,
             "meeting": socket.SOCK_STREAM, "health": socket.SOCK_STREAM, "api": socket.SOCK_STREAM,
             "ws": socket.SOCK_STREAM, "ui": socket.SOCK_STREAM, "unused_ws": socket.SOCK_STREAM}
    ports = allocate_ports(kinds)
    owner = "legilimens-vcm-" + secrets.token_hex(6)
    secret, control, password = (secrets.token_urlsafe(32) for _ in range(3))
    ui_origin = f"http://127.0.0.1:{ports['ui']}"
    containers, processes, network_created = [], [], False
    report = {"schemaVersion": 2, "passed": False, "status": "BLOCKED", "runId": run_id,
              "commit": manifest["commit"], "ports": ports, "routes": {},
              "fixtures": {p: {"videoSha256": fixtures["participants"][p]["videoSha256"],
                               "audioSha256": fixtures["participants"][p]["audioSha256"]} for p in ("A", "B")},
              "scope": "two-participant real-UI synthetic media; video decode; audio observed-or-blocked",
              "frontend": {"path": str(dist), "variant": "original" if dist == (ROOT / "build/videocall-frontend/dist").resolve() else "custom-unverified"}}
    build_record = dist.parent / "fixture-build.json"
    if build_record.is_file():
        report["frontend"].update(json.loads(build_record.read_text(encoding="utf-8")))

    def redact(text):
        for value in (secret, control, password):
            text = text.replace(value, "[redacted]")
        return re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[jwt-redacted]", text)

    def docker(*argv, stdin=None, timeout=120):
        proc = subprocess.run(["docker", *argv], input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        if proc.returncode:
            raise RuntimeError(redact(proc.stderr[-3000:]))
        return (proc.stdout + (proc.stderr if argv[0] == "logs" else "")).strip()

    def run(name, image, options, command):
        cid = docker("run", "-d", "--name", owner + "-" + name, "--label", "org.legilimens.run=" + owner,
                     "--network", owner, "--network-alias", name, "--memory", "768m", "--cpus", "2",
                     *options, image, *command)
        containers.append(cid)
        return cid

    with tempfile.TemporaryDirectory(prefix="legilimens-vcm-") as scratch:
        scratch = Path(scratch)
        cert_dir = scratch / "target"
        target_pin = ensure_cert(cert_dir).cert_hash
        served = httpd = t = None
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
            run("meeting", IMAGES["backend"], [*shared, "-p", f"127.0.0.1:{ports['meeting']}:8081",
                "-e", f"DATABASE_URL=postgres://postgres:{password}@db:5432/videocall",
                "-e", "COOKIE_SECURE=false", "-e", "TOKEN_TTL_SECS=600",
                "-e", f"CORS_ALLOWED_ORIGIN={ui_origin}"], ["/src/target/debug/meeting-api"])
            transport_cid = run("transport", IMAGES["backend"], [*shared,
                "--mount", f"type=bind,source={cert_dir},target=/certs,readonly",
                "-p", f"127.0.0.1:{ports['upstream']}:4433/udp", "-p", f"127.0.0.1:{ports['health']}:8080",
                "-e", "LISTEN_URL=0.0.0.0:4433", "-e", "HEALTH_LISTEN_URL=0.0.0.0:8080",
                "-e", "KEY_PATH=/certs/key.pem", "-e", "CERT_PATH=/certs/cert.pem"],
                ["/src/target/debug/webtransport_server"])
            wait_for(lambda: http(ports["meeting"], "/version"), label="meeting API")
            wait_for(lambda: http(ports["health"], "/version"), label="transport health")

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

            direct_url = f"https://127.0.0.1:{ports['upstream']}"
            proxied_url = f"https://127.0.0.1:{ports['proxy']}"
            served = serve_ui.stage(dist, ports["meeting"], ports["unused_ws"], direct_url)
            httpd = serve_ui.serve(served, ports["ui"], ports["meeting"], ports["unused_ws"], direct_url)
            httpd.csp = httpd.csp.replace("connect-src 'self'", f"connect-src 'self' {proxied_url}")
            t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()

            def sjwt(sub, name):
                return jwt(secret, {"sub": sub, "name": name, "iss": "videocall-meeting-backend",
                                    "iat": int(time.time()), "exp": int(time.time()) + 1200})

            def run_route(route, wt_url, pin):
                (served / "config.js").write_text(serve_ui.local_config(ports["meeting"], ports["unused_ws"], wt_url), encoding="utf-8")
                room = f"{route}_{secrets.token_hex(4)}"
                rep = output / f"{route}.json"; rep.unlink(missing_ok=True)
                conn = {"uiUrl": f"http://127.0.0.1:{ports['ui']}/", "apiOrigin": f"http://127.0.0.1:{ports['meeting']}",
                        "route": route, "webTransportHost": wt_url, "pin": pin, "room": room,
                        "mediaWsOrigin": f"ws://127.0.0.1:{ports['unused_ws']}",
                        "transportContainer": transport_cid, "fixtures": fx,
                        "sessions": [sjwt("media-a", "Media A"), sjwt("media-b", "Media B")],
                        "report": str(rep)}
                env = {"VIDEOCALL_MEDIA": json.dumps(conn), "PYTHONUTF8": "1"}
                if playwright:
                    env["PLAYWRIGHT_MODULE"] = playwright
                driver = Proc([args.node, str(Path(__file__).with_name("media-browser.mjs"))], ROOT, env=env, tree=True)
                processes.append(driver)
                try:
                    code = driver.p.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    driver.stop()
                    code = driver.p.returncode
                (output / f"{route}-driver.log").write_text(redact("\n".join(driver.snapshot())), encoding="utf-8")
                data = json.loads(rep.read_text()) if rep.exists() else {"passed": False, "error": "no report"}
                data = driver_result(data, code)
                report["routes"][route] = data
                return video_baseline_ready(data)

            direct_ok = run_route("direct", direct_url, target_pin)
            if direct_ok and not args.direct_only:
                run_route("proxied", proxied_url, proxy_pin)
            else:
                report["routes"]["proxied"] = {"status": "NOT_RUN", "reason":
                    "Explicit direct-only diagnostic" if args.direct_only else "direct media baseline did not pass"}

        except Exception as exc:
            report["error"] = redact(str(exc))
        finally:
            errors = []
            if httpd is not None:
                try:
                    if t is not None:
                        httpd.shutdown()
                        t.join(timeout=5)
                        if t.is_alive():
                            errors.append("UI server thread did not stop")
                    httpd.server_close()
                except Exception as exc:
                    errors.append(redact(str(exc)))
            for index, proc in reversed(list(enumerate(processes))):
                try:
                    proc.stop()
                except Exception as exc:
                    errors.append(type(exc).__name__)
                # Preserve the proxy's close/resource diagnostics, including on timeout.
                name = "proxy.log" if index == 0 else f"child-{index}.log"
                try:
                    (output / name).write_text(redact("\n".join(proc.snapshot())), encoding="utf-8")
                except Exception as exc:
                    errors.append(redact(str(exc)))
            for cid in reversed(containers):
                try:
                    (output / (cid[:12] + ".log")).write_text(redact(docker("logs", cid)), encoding="utf-8")
                except Exception:
                    errors.append("Could not preserve owned container log")
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
                try:
                    if served.resolve().parent != Path(tempfile.gettempdir()).resolve() or not served.name.startswith("legilimens-vc-ui-"):
                        raise RuntimeError("Refusing cleanup outside the owned UI temp directory")
                    shutil.rmtree(served)
                except Exception as exc:
                    errors.append(redact(str(exc)))
            try:
                wait_for(lambda: all(port_free(ports[n], k) for n, k in kinds.items()), 10)
            except TimeoutError:
                errors.append("Ports not released")
            report["cleanup"] = not errors
            report["cleanupErrors"] = errors
            report.update(run_verdict(report["routes"], report["cleanup"], report.get("error")))

    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "status": report["status"], "cleanup": report.get("cleanup"), "runId": run_id,
                      "routes": {k: {"passed": v.get("passed"), "videoPassed": v.get("videoPassed"),
                                     "status": v.get("status"), "reasons": v.get("reasons"), "error": v.get("error"),
                                     "video": {d: {"status": s.get("status"), "reasons": s.get("reasons")}
                                               for d, s in v.get("video", {}).items()},
                                     "audio": {d: s.get("status") for d, s in v.get("audio", {}).items()}}
                                 for k, v in report["routes"].items()}, "error": report.get("error")}, indent=2))
    print("evidence:", output)
    return 0 if report["passed"] else 2 if report["status"] == "BLOCKED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--direct-only", action="store_true", help="Diagnose the direct baseline; cannot pass the complete gate")
    parser.add_argument("--frontend-dist", type=Path, default=ROOT / "build/videocall-frontend/dist",
                        help="Explicit alternate test build; recorded separately from the original pinned fixture")
    parser.add_argument("--output", type=Path, default=ROOT / f"build/videocall-media/run-{int(time.time())}-{secrets.token_hex(3)}")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be empty: preserve previous evidence and use a new run directory")
    output.mkdir(parents=True, exist_ok=True)
    try:
        return run(args)
    except Exception as exc:
        result = {"passed": False, "status": "BLOCKED", "error": str(exc), "routes": {}, "cleanup": False}
        (output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 2


if __name__ == "__main__":
    sys.exit(main())
