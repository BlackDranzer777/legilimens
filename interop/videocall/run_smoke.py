"""Disposable authenticated browser admission test, not a video-meeting verdict."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

from preflight import ROOT, MANIFEST, inspect_source

sys.path.insert(0, str(ROOT / "interop/harness"))
sys.path.insert(0, str(ROOT / "python"))
from support import Proc, allocate_ports, port_free
from certs import ensure_cert

IMAGES = {
    "backend": "legilimens-videocall:31a8b207-backend",
    "nats": "nats@sha256:d69eb29526c1d98afdfb2e2434763bef77b5f3c83e2e24769c13a4d104be475e",
    "postgres": "postgres@sha256:120230d04218c2acb30d7bb427798bd6c5beb0ef225883132aad653ccad44bce",
}


def jwt(secret, claims):
    def enc(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=")
    body = enc(b'{"alg":"HS256","typ":"JWT"}') + b"." + enc(json.dumps(claims).encode())
    return (body + b"." + enc(hmac.new(secret.encode(), body, hashlib.sha256).digest())).decode()


def http(port, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(f"http://127.0.0.1:{port}{path}", headers=headers,
                      data=None if body is None else json.dumps(body).encode())
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def wait_for(check, timeout=30, label="service"):
    deadline = time.monotonic() + timeout
    last_error = "check returned false"
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, ValueError, RuntimeError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise TimeoutError(f"{label} readiness timed out: {last_error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--output", type=Path, default=ROOT / "build/videocall-smoke")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checkout = ROOT / manifest["checkout"]
    inspect_source(checkout, manifest)
    kinds = {name: socket.SOCK_DGRAM for name in ("upstream", "proxy", "target")}
    kinds.update({name: socket.SOCK_STREAM for name in ("meeting", "health", "api", "ws")})
    ports = allocate_ports(kinds)
    owner = "legilimens-vc-" + secrets.token_hex(6)
    secret, control, password = (secrets.token_urlsafe(32) for _ in range(3))
    containers, processes = [], []
    network_created = False
    report = {"passed": False, "commit": manifest["commit"], "ports": ports,
              "scope": "authenticated native-browser WebTransport admission; no media/UI claim"}

    def redact(text):
        for value in (secret, control, password):
            text = text.replace(value, "[redacted]")
        return re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[jwt-redacted]", text)

    def docker(*argv, stdin=None, timeout=60):
        proc = subprocess.run(["docker", *argv], input=stdin, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if proc.returncode:
            raise RuntimeError(redact(proc.stderr[-3000:]))
        return (proc.stdout + (proc.stderr if argv[0] == "logs" else "")).strip()

    def run(name, image, options, command):
        cid = docker("run", "-d", "--name", owner + "-" + name,
                     "--label", "org.legilimens.run=" + owner, "--network", owner,
                     "--network-alias", name, "--memory", "512m", "--cpus", "2",
                     *options, image, *command)
        containers.append(cid)
        return cid

    with tempfile.TemporaryDirectory(prefix="legilimens-videocall-") as scratch:
        scratch = Path(scratch)
        cert_dir = scratch / "target"
        target_pin = ensure_cert(cert_dir).cert_hash
        try:
            report["images"] = {k: docker("image", "inspect", v, "--format", "{{.Id}}") for k, v in IMAGES.items()}
            # Docker Desktop 26 does not publish ports on the internal-only network.
            # Use an owned bridge; publish only application ports on host loopback.
            docker("network", "create", "--label", "org.legilimens.run=" + owner, owner)
            network_created = True
            run("nats", IMAGES["nats"], ["--read-only"], [])
            db = run("db", IMAGES["postgres"], ["--tmpfs", "/var/lib/postgresql",
                     "-e", "POSTGRES_PASSWORD=" + password, "-e", "POSTGRES_DB=videocall"], [])
            wait_for(lambda: docker("exec", db, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "videocall"))
            docker("exec", "-i", db, "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "videocall",
                   stdin=(checkout / "dbmate/db/schema.sql").read_text(encoding="utf-8"))
            shared = ["--read-only", "--tmpfs", "/tmp", "-e", "JWT_SECRET=" + secret,
                      "-e", "NATS_URL=nats://nats:4222", "-e", "FEATURE_MEETING_MANAGEMENT=true",
                      "-e", "RUST_LOG=info"]
            meeting = run("meeting", IMAGES["backend"], [*shared,
                "-p", f"127.0.0.1:{ports['meeting']}:8081",
                "-e", f"DATABASE_URL=postgres://postgres:{password}@db:5432/videocall",
                "-e", "COOKIE_SECURE=false", "-e", "TOKEN_TTL_SECS=300",
                "-e", "CORS_ALLOWED_ORIGIN=http://127.0.0.1"], ["/src/target/debug/meeting-api"])
            run("transport", IMAGES["backend"], [*shared,
                "--mount", f"type=bind,source={cert_dir},target=/certs,readonly",
                "-p", f"127.0.0.1:{ports['upstream']}:4433/udp", "-p", f"127.0.0.1:{ports['health']}:8080",
                "-e", "LISTEN_URL=0.0.0.0:4433", "-e", "HEALTH_LISTEN_URL=0.0.0.0:8080",
                "-e", "KEY_PATH=/certs/key.pem", "-e", "CERT_PATH=/certs/cert.pem"],
                ["/src/target/debug/webtransport_server"])
            report["meetingPublishedPorts"] = docker("port", meeting)
            if not report["meetingPublishedPorts"]:
                raise RuntimeError("Docker did not publish the meeting API loopback port")
            wait_for(lambda: http(ports["meeting"], "/version"), label="meeting API from Windows")
            wait_for(lambda: http(ports["health"], "/version"), label="transport health from Windows")
            report["servicesReady"] = True
            session = jwt(secret, {"sub": "local-researcher", "name": "Local Researcher",
                "iss": "videocall-meeting-backend", "iat": int(time.time()), "exp": int(time.time()) + 600})
            joined = http(ports["meeting"], "/api/v1/meetings/interop-room/join", session, {"display_name": "Local Researcher"})
            room_token = joined["result"]["room_token"]
            assert room_token, "Meeting API did not issue a room token"
            report["meetingTokenIssued"] = True
            claims = json.loads(base64.urlsafe_b64decode(room_token.split('.')[1] + '==='))
            invalid_token = jwt("wrong-secret", claims)
            claims["exp"] = int(time.time()) - 120
            proxy = Proc([sys.executable, str(ROOT / "python/backend.py"),
                *[arg for name in ("proxy", "target", "api", "ws") for arg in ("--port-" + name, str(ports[name]))]], ROOT,
                env={"LEGILIMENS_CERTS_DIR": str(scratch / "proxy"), "LEGILIMENS_CONTROL_TOKEN": control,
                     "LEGILIMENS_PARENT_PID": str(os.getpid()), "PYTHONUTF8": "1"})
            processes.append(proxy)
            if not proxy.wait_line(lambda line: line.startswith("READY instanceId="), 30):
                raise RuntimeError("Legilimens did not become ready")
            http(ports["api"], "/target", control, {"host": "127.0.0.1", "port": ports["upstream"], "certHash": target_pin})
            http(ports["api"], "/intercept", control, {"action": "start"})
            config = {"origin": f"http://127.0.0.1:{ports['meeting']}",
                "direct": {"url": f"https://127.0.0.1:{ports['upstream']}", "pin": target_pin},
                "proxied": {"url": f"https://127.0.0.1:{ports['proxy']}", "pin": http(ports["api"], "/cert-hash", control)["hash"]},
                "tokens": {"valid": room_token, "invalid": invalid_token, "expired": jwt(secret, claims), "missing": ""},
                "report": str(output / "browser.json")}
            browser = Proc([args.node, str(Path(__file__).with_name("browser-smoke.mjs"))], ROOT,
                           env={"VIDEOCALL_CONFIG": json.dumps(config)}, tree=True)
            processes.append(browser)
            report["browserExit"] = browser.p.wait(timeout=100)
            report["passed"] = report["browserExit"] == 0
            if report["browserExit"]:
                report["browserError"] = redact("\n".join(browser.snapshot()[-8:]))
        except Exception as exc:
            report["error"] = redact(str(exc))
        finally:
            errors = []
            for proc in reversed(processes):
                try:
                    proc.stop()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            for cid in reversed(containers):
                try:
                    logs = redact(docker("logs", cid))
                    (output / (cid[:12] + ".log")).write_text(logs, encoding="utf-8")
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
            try:
                wait_for(lambda: all(port_free(ports[n], k) for n, k in kinds.items()), 10)
            except TimeoutError:
                errors.append("Ports not released")
            report["cleanup"] = not errors
            report["cleanupErrors"] = errors
            report["passed"] = report["passed"] and report["cleanup"]
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
