"""
Legilimens backend entry point.

Starts all four services concurrently in one asyncio process:
  :4433 — WebTransport MITM proxy
  :4434 — Deliberately vulnerable WebTransport server
  :4435 — WebSocket log broadcaster
  :4436 — FastAPI HTTP control API

Prints "READY" to stdout once all services are up (Electron signal).
Exits cleanly when stdin closes or on Ctrl-C.

CLI args (all optional, defaults shown):
  --port-proxy  4433
  --port-target 4434
  --port-ws     4435
  --port-api    4436
"""

import argparse
import asyncio
import os
import stat
import sys
from pathlib import Path

import api as api_module
from certs import certs_exist, get_cert_hash
from logger import log_info, log_error, start_logger
from proxy import start_proxy
from vulnerable_server import start_vulnerable_server


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port-proxy",  type=int, default=4433)
    p.add_argument("--port-target", type=int, default=4434)
    p.add_argument("--port-ws",     type=int, default=4435)
    p.add_argument("--port-api",    type=int, default=4436)
    return p.parse_args()


def _stdin_is_pipe() -> bool:
    """True only when stdin is a real pipe — i.e. Electron closed it."""
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
        return stat.S_ISFIFO(mode)
    except Exception:
        return False


async def watch_stdin():
    """Exit when stdin is closed — Electron parent process sends this signal."""
    if not _stdin_is_pipe():
        return  # terminal / /dev/null — skip, nothing to watch
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    try:
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
    except Exception:
        pass
    log_info("stdin closed — shutting down")
    loop.stop()


async def main():
    args = parse_args()

    if not certs_exist():
        print("[legilimens] ERROR: No certificate found. Run: python python/certs.py", flush=True)
        sys.exit(1)

    h = get_cert_hash()
    if h:
        api_module.set_cert_hash(h)

    print("\033[36m[legilimens]\033[0m Starting all services...", flush=True)

    # Start services
    ws_server = await start_logger(args.port_ws)
    proxy_server = await start_proxy(args.port_proxy)
    vuln_server = await start_vulnerable_server(args.port_target)

    # FastAPI runs as a task (uvicorn internal loop)
    api_task = asyncio.create_task(_run_api(args.port_api))

    print(f"\033[36m[legilimens]\033[0m Proxy:    https://localhost:{args.port_proxy}  (WebTransport MITM)", flush=True)
    print(f"\033[36m[legilimens]\033[0m Target:   https://localhost:{args.port_target}  (Vulnerable server)", flush=True)
    print(f"\033[36m[legilimens]\033[0m WS Log:   ws://localhost:{args.port_ws}     (UI events)", flush=True)
    print(f"\033[36m[legilimens]\033[0m HTTP API: http://localhost:{args.port_api}   (Control API)", flush=True)
    print(f"\033[36m[legilimens]\033[0m Cert hash: {h}", flush=True)
    print("", flush=True)

    # Signal to Electron that all services are up
    print("READY", flush=True)

    # Watch stdin for Electron shutdown signal (non-blocking; best-effort)
    asyncio.create_task(watch_stdin())

    try:
        # Run until cancelled
        await asyncio.gather(api_task, return_exceptions=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        log_info("Shutting down all services")
        ws_server.close()
        proxy_server.close()
        vuln_server.close()
        await ws_server.wait_closed()


async def _run_api(port: int):
    from api import start_api
    await start_api(port)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
