"""
Supervised startup/shutdown primitives for the Legilimens backend.

- INSTANCE_ID: a per-launch identifier printed on the READY line and echoed by
  /health, so a launcher can prove the health response comes from the process it
  spawned (not a pre-existing server on the same port).
- Supervisor: an ordered cleanup stack unwound in reverse within a deadline; it is
  idempotent and reports steps that did not finish cleanly.
- parent_alive / watch_parent: a read-only parent-liveness check for
  launcher-owned mode. It never terminates the parent; it only observes it.
- wait_http_ready: an authenticated loopback readiness probe used to gate READY.
"""

import asyncio
import json
import os
import secrets
import sys
import urllib.request

INSTANCE_ID = secrets.token_hex(8)

# Distinct process exit codes so a launcher can react precisely.
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_RESTART = 75   # a controlled restart is required (e.g. certificate rotation)


# ---------- cleanup supervisor ----------

class Supervisor:
    """Registers cleanup steps as services start; unwinds them in reverse."""

    def __init__(self):
        self._cleanups = []      # (name, callable returning None or awaitable)
        self._closed = False

    def push(self, name, cleanup):
        self._cleanups.append((name, cleanup))

    @property
    def closed(self) -> bool:
        return self._closed

    async def aclose(self, deadline: float = 8.0):
        """Run cleanups newest-first within `deadline`. Returns incomplete steps.

        Idempotent: a second call is a no-op and returns an empty list.
        """
        if self._closed:
            return []
        self._closed = True
        loop = asyncio.get_running_loop()
        end = loop.time() + deadline
        incomplete = []
        for name, cleanup in reversed(self._cleanups):
            remaining = max(0.1, end - loop.time())
            try:
                result = cleanup()
                if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                    await asyncio.wait_for(result, remaining)
            except asyncio.TimeoutError:
                incomplete.append(name)
            except asyncio.CancelledError:
                # A step raising CancelledError (a service ending its own tasks) must
                # not abort the unwind — but our own cancellation still propagates.
                if asyncio.current_task().cancelling():
                    raise
                incomplete.append(f"{name}: cancelled")
            except Exception as exc:  # keep unwinding despite one bad step
                incomplete.append(f"{name}: {exc}")
        self._cleanups.clear()
        return incomplete


# ---------- parent liveness (read-only) ----------

def parent_alive(pid: int) -> bool:
    """True while process `pid` is running. Never signals or terminates it."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return False  # gone or inaccessible -> treat as not alive
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)          # signal 0 only checks existence on POSIX
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # exists but not ours to signal


async def watch_parent(pid: int, on_dead, interval: float = 1.0):
    """Poll parent liveness; call `on_dead` once when the parent disappears."""
    while True:
        await asyncio.sleep(interval)
        if not parent_alive(pid):
            await on_dead()
            return


# ---------- authenticated readiness probe ----------

def _get_health(port: int, token: str, timeout: float):
    url = f"http://127.0.0.1:{port}/health"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


async def wait_http_ready(port: int, token: str, *, started_flag, abort_flag=None,
                          timeout: float = 15.0) -> bool:
    """Return True once the API answers an authenticated /health for THIS instance.

    `started_flag()` should report the server's own started state (e.g.
    uvicorn Server.started) so the probe only runs once the listener is bound.
    `abort_flag()`, if given, returning True (e.g. the serve task already died)
    ends the wait immediately with False.
    """
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if abort_flag is not None and abort_flag():
            return False
        if started_flag():
            try:
                status, body = await loop.run_in_executor(None, _get_health, port, token, 2.0)
                if (status == 200 and body.get("service") == "legilimens"
                        and body.get("instanceId") == INSTANCE_ID):
                    return True
            except Exception:
                pass
        await asyncio.sleep(0.1)
    return False
