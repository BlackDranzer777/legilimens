"""
Legilimens backend entry point — a supervised lifecycle.

Startup runs as ordered steps; each successful step registers its cleanup, so a
failure at any point unwinds exactly what started. READY is printed only after
every listener is bound AND an authenticated /health responds for THIS instance,
so the signal is truthful. Startup failures and unexpected service exits produce a
nonzero exit.

Shutdown is a single idempotent path shared by Ctrl+C, parent exit (launcher-owned
mode), certificate rotation, and startup failure. It stops new work, cancels attack
runs, closes proxy sessions, and shuts down the WebSocket and HTTP services within a
deadline, reporting any step that did not finish.

Certificate ownership lives here (via certs.ensure_cert): launchers no longer
generate certificates. Listeners bind the certificate at start, so renewal while
running stages a fresh certificate and requests a controlled restart (exit 75) that
the launcher performs; the restarted backend serves and publishes the new one.

Environment:
  LEGILIMENS_CONTROL_TOKEN   shared bearer token (also gates the readiness probe)
  LEGILIMENS_PARENT_PID      parent process to watch; its exit triggers shutdown
  LEGILIMENS_CERTS_DIR / LEGILIMENS_DATA_DIR / LEGILIMENS_UI_DIR   path overrides

CLI: --port-proxy 4433  --port-target 4434  --port-ws 4435  --port-api 4436
"""

import argparse
import asyncio
import os
import signal
import sys

# Windows consoles default to cp1252; force UTF-8 so the banner / cert hash print.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import api as api_module
import attack_runner
import lifecycle
from certs import CertificateError, ensure_cert, validate as validate_cert
from logger import log_info, log_error, start_logger
from proxy import start_proxy, disconnect_all
from vulnerable_server import start_vulnerable_server
from control_security import security

SHUTDOWN_DEADLINE = 8.0          # seconds to unwind all services
RENEWAL_CHECK_INTERVAL = 3600    # re-check the certificate hourly while running
API_READY_TIMEOUT = 15.0


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port-proxy",  type=int, default=4433)
    p.add_argument("--port-target", type=int, default=4434)
    p.add_argument("--port-ws",     type=int, default=4435)
    p.add_argument("--port-api",    type=int, default=4436)
    return p.parse_args()


def _parent_pid():
    raw = os.environ.get("LEGILIMENS_PARENT_PID")
    return int(raw) if raw and raw.isdigit() else None


async def _close_server(server):
    server.close()
    await server.wait_closed()


async def _close_quic_server(server):
    # aioquic has no server.wait_closed(); close() closes its UDP transport.
    # Save connection protocols before close() clears the server's registry.
    protocols = set(server._protocols.values())
    server.close()
    if protocols:
        await asyncio.gather(*(p.wait_closed() for p in protocols))
    await asyncio.sleep(0)  # Let the UDP transport's connection_lost callback run.


async def _serve_api(server):
    try:
        await server.serve()
    except SystemExit as e:
        # Uvicorn raises SystemExit on bind failure; keep it inside supervision.
        raise RuntimeError(f"HTTP server failed to start ({e})") from e


async def _stop_api(server, task):
    server.should_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), 5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


def _print_banner(args, cert_hash):
    C = "\033[36m[legilimens]\033[0m"
    print(f"{C} Proxy:    https://localhost:{args.port_proxy}  (WebTransport MITM)", flush=True)
    print(f"{C} Target:   https://localhost:{args.port_target}  (Vulnerable server)", flush=True)
    print(f"{C} WS Log:   ws://localhost:{args.port_ws}     (UI events)", flush=True)
    print(f"{C} HTTP API: http://localhost:{args.port_api}   (Control API)", flush=True)
    print(f"{C} Cert hash: {cert_hash}", flush=True)
    if not os.environ.get("LEGILIMENS_CONTROL_TOKEN"):
        print(f"[legilimens] Control token (private, changes on restart): {security.token}", flush=True)
        print(f"[legilimens] UI: http://127.0.0.1:{args.port_api}/#token={security.token}", flush=True)
        print(f"[legilimens] Dev UI: http://127.0.0.1:5180/#token={security.token}", flush=True)
    print("", flush=True)


async def _renewal_scheduler(request_shutdown):
    """Renew the certificate before expiry; a controlled restart activates it.

    A fresh file cannot rotate an already-bound listener, so when the certificate
    crosses the renewal threshold we stage a new one and request EXIT_RESTART. If no
    usable certificate can be produced we stop rather than serve past expiry silently.
    """
    while True:
        await asyncio.sleep(RENEWAL_CHECK_INTERVAL)
        try:
            status = validate_cert()
            if not status.needs_renewal:
                continue
            new = ensure_cert()
            if new.cert_hash and new.cert_hash != api_module._cert_hash_cache:
                log_info("Certificate renewed; requesting controlled restart",
                         {"expires": new.not_after.isoformat() if new.not_after else None})
                await request_shutdown("certificate rotation", lifecycle.EXIT_RESTART)
                return
        except CertificateError as e:
            log_error("No usable certificate; stopping to avoid serving past expiry",
                      {"error": str(e)})
            await request_shutdown("certificate expired", lifecycle.EXIT_FAILURE)
            return
        except Exception as e:
            log_error("Certificate renewal check failed", {"error": str(e)})


async def run() -> int:
    args = parse_args()
    api_module.bundled_target_port = args.port_target
    api_module.target_config["port"] = args.port_target
    security.configure(args.port_api, args.port_ws, args.port_proxy)

    sup = lifecycle.Supervisor()
    stop = asyncio.Event()
    outcome = {"code": lifecycle.EXIT_OK, "reason": "clean"}
    started = {"shutdown": False}

    async def request_shutdown(reason, code=lifecycle.EXIT_OK):
        if started["shutdown"]:
            return
        started["shutdown"] = True
        outcome["reason"] = reason
        if code != lifecycle.EXIT_OK:
            outcome["code"] = code
        log_info("Shutdown requested", {"reason": reason})
        stop.set()

    # Let an authenticated launcher request a graceful stop via POST /shutdown.
    api_module.register_shutdown_fn(request_shutdown)

    # --- certificate: the backend owns preparation, renewing if needed ---
    try:
        cert_status = ensure_cert()
    except CertificateError as e:
        print(f"[legilimens] FATAL: no usable certificate: {e}", file=sys.stderr, flush=True)
        return lifecycle.EXIT_FAILURE
    api_module.set_cert_hash(cert_status.cert_hash)

    # --- supervised startup: register each cleanup as its step succeeds ---
    try:
        print("\033[36m[legilimens]\033[0m Starting all services...", flush=True)
        ws_server = await start_logger(args.port_ws)
        sup.push("ws-logger", lambda: _close_server(ws_server))

        proxy_server = await start_proxy(args.port_proxy)
        sup.push("proxy-listener", lambda: _close_quic_server(proxy_server))
        sup.push("proxy-sessions", disconnect_all)

        vuln_server = await start_vulnerable_server(args.port_target)
        sup.push("target-listener", lambda: _close_quic_server(vuln_server))

        api_server = api_module.make_api_server(args.port_api)
        api_task = asyncio.create_task(_serve_api(api_server), name="api")
        sup.push("attacks", attack_runner.cancel_all)

        sup.push("api", lambda: _stop_api(api_server, api_task))

        ready = await lifecycle.wait_http_ready(
            args.port_api, security.token,
            started_flag=lambda: api_server.started,
            abort_flag=lambda: api_task.done(),
            timeout=API_READY_TIMEOUT)
        if not ready:
            raise RuntimeError("HTTP API did not become ready (bind failure or timeout)")
    except Exception as e:
        log_error("Startup failed", {"error": str(e)})
        print(f"[legilimens] startup failed: {e}", file=sys.stderr, flush=True)
        incomplete = await sup.aclose(SHUTDOWN_DEADLINE)
        if incomplete:
            print(f"[legilimens] cleanup incomplete: {incomplete}", file=sys.stderr, flush=True)
        return lifecycle.EXIT_FAILURE

    # --- truthful READY (all listeners bound + authenticated health confirmed) ---
    _print_banner(args, cert_status.cert_hash)
    print(f"READY instanceId={lifecycle.INSTANCE_ID}", flush=True)

    # --- signal handling (POSIX) + long-running supervision ---
    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.ensure_future(request_shutdown(f"signal {s}")))
        except (NotImplementedError, RuntimeError):
            pass  # Windows: rely on KeyboardInterrupt below

    watchers = []
    parent_pid = _parent_pid()
    if parent_pid:
        watchers.append(asyncio.create_task(
            lifecycle.watch_parent(parent_pid, lambda: request_shutdown("parent exited")),
            name="parent-watch"))
    watchers.append(asyncio.create_task(_renewal_scheduler(request_shutdown), name="cert-renewal"))

    async def _guard_api():
        try:
            await asyncio.shield(api_task)
        except asyncio.CancelledError:
            return
        except Exception as e:
            await request_shutdown(f"api service crashed: {e}", lifecycle.EXIT_FAILURE)
            return
        await request_shutdown("api service exited unexpectedly", lifecycle.EXIT_FAILURE)
    watchers.append(asyncio.create_task(_guard_api(), name="api-guard"))

    # Test-only fault injection: simulate the API service exiting after READY, so a
    # regression test can assert the guard turns it into a nonzero exit. No normal
    # launch sets this variable.
    _fault_after = os.environ.get("LEGILIMENS_TEST_STOP_API_AFTER")
    if _fault_after:
        async def _fault():
            await asyncio.sleep(float(_fault_after))
            api_server.should_exit = True
        watchers.append(asyncio.create_task(_fault(), name="test-fault"))

    try:
        await stop.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        await request_shutdown("interrupt")

    # --- coordinated shutdown ---
    for w in watchers:
        w.cancel()
    incomplete = await sup.aclose(SHUTDOWN_DEADLINE)
    for w in watchers:
        try:
            await w
        except (asyncio.CancelledError, Exception):
            pass
    if incomplete:
        outcome["code"] = lifecycle.EXIT_FAILURE
        print(f"[legilimens] cleanup incomplete: {incomplete}", file=sys.stderr, flush=True)
        log_error("Cleanup incomplete", {"steps": incomplete})
    log_info("Shutdown complete", {"reason": outcome["reason"], "code": outcome["code"]})
    return outcome["code"]


def main():
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = lifecycle.EXIT_OK
    sys.exit(code)


if __name__ == "__main__":
    main()
