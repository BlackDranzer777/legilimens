"""One real browser renewal/reconnect scenario; no production test hooks."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "interop/harness"))
from support import Proc, allocate_ports, port_free

BOOTSTRAP = """
import asyncio, datetime, os
from pathlib import Path
import backend, certs
original = backend._renewal_scheduler
async def triggered(shutdown):
    while not Path(os.environ['ROTATION_TRIGGER']).exists():
        await asyncio.sleep(0.1)
    certs.RENEWAL_THRESHOLD = datetime.timedelta(days=14)
    backend.RENEWAL_CHECK_INTERVAL = 0.01
    await original(shutdown)
backend._renewal_scheduler = triggered
backend.main()
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--output", default="build/rotation-browser")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    kinds = dict(proxy=socket.SOCK_DGRAM, target=socket.SOCK_DGRAM,
                 api=socket.SOCK_STREAM, ws=socket.SOCK_STREAM)
    ports = allocate_ports(kinds)
    token = secrets.token_urlsafe(32)
    report = {"passed": False, "ports": ports}
    procs = []
    with tempfile.TemporaryDirectory(prefix="rotation-") as scratch:
        trigger = Path(scratch) / "rotate"
        env = {"PYTHONPATH": str(ROOT / "python"), "PYTHONUTF8": "1",
               "LEGILIMENS_CONTROL_TOKEN": token, "LEGILIMENS_CERTS_DIR": scratch,
               "LEGILIMENS_PARENT_PID": str(os.getpid()), "ROTATION_TRIGGER": str(trigger)}
        port_args = [arg for name, port in ports.items() for arg in (f"--port-{name}", str(port))]
        def start(rotate):
            command = [sys.executable, "-c", BOOTSTRAP] if rotate else [sys.executable, str(ROOT / "python/backend.py")]
            proc = Proc([*command, *port_args], ROOT, env=env)
            procs.append(proc)
            line = proc.wait_line(lambda line: line.startswith("READY instanceId="), 30)
            if not line:
                raise RuntimeError("Backend readiness failed")
            return proc, line
        try:
            first, old_ready = start(True)
            config = {"api": f"http://127.0.0.1:{ports['api']}", "proxy": ports["proxy"],
                      "token": token, "output": str(output)}
            browser = Proc([args.node, str(ROOT / "scripts/tests/rotation-browser.mjs")], ROOT,
                           env={"ROTATION_CONFIG": json.dumps(config)}, tree=True)
            procs.append(browser)
            if not browser.wait_line(lambda line: line == "ROTATE", 30):
                raise RuntimeError("Browser did not reach renewal trigger: " + "\n".join(browser.snapshot()[-4:]))
            trigger.touch()
            report["rotationExit"] = first.p.wait(timeout=30)
            assert report["rotationExit"] == 75, "Renewal did not request clean restart"
            _, new_ready = start(False)
            report["instanceChanged"] = old_ready != new_ready
            code = browser.p.wait(timeout=60)
            report["browserExit"] = code
            if code:
                raise RuntimeError("Browser assertions failed: " + "\n".join(browser.snapshot()[-8:]))
            report["passed"] = report["instanceChanged"]
        except Exception as exc:
            report["error"] = str(exc).replace(token, "[redacted]")
        finally:
            errors = []
            for proc in reversed(procs):
                try:
                    proc.stop()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not all(port_free(ports[n], k) for n, k in kinds.items()):
                time.sleep(0.1)
            report["cleanup"] = not errors and all(port_free(ports[n], k) for n, k in kinds.items())
            report["passed"] = report["passed"] and report["cleanup"]
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
