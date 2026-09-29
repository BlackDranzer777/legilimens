"""Backend lifecycle: supervised startup readiness, failure-safe exits, parent
liveness, and coordinated shutdown.

Unit tests exercise the reusable primitives in-process. Integration tests spawn the
real backend on dedicated high loopback ports with a temporary certificate directory,
so the developer's certificates and default ports are never touched.
"""

import asyncio
import json
import os
import socket
import queue
import threading
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # python/
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

import lifecycle  # noqa: E402

PORTS = {"proxy": 15433, "target": 15434, "ws": 15435, "api": 15436}
TOKEN = "test-token_" + "A" * 24        # 32+ url-safe chars


def _port_free(port, udp=False):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
    if os.name == 'nt':
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _all_ports_free():
    return all(_port_free(p, name in ('proxy', 'target')) for name, p in PORTS.items())


def _health(token=TOKEN, timeout=3):
    req = urllib.request.Request(f"http://127.0.0.1:{PORTS['api']}/health",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


class SupervisorUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanups_run_newest_first(self):
        order = []
        sup = lifecycle.Supervisor()
        sup.push("a", lambda: order.append("a"))
        sup.push("b", lambda: order.append("b"))
        incomplete = await sup.aclose(2.0)
        self.assertEqual(order, ["b", "a"])
        self.assertEqual(incomplete, [])

    async def test_aclose_is_idempotent(self):
        calls = []
        sup = lifecycle.Supervisor()
        sup.push("x", lambda: calls.append(1))
        await sup.aclose(1.0)
        await sup.aclose(1.0)
        self.assertEqual(calls, [1])

    async def test_slow_step_times_out_and_is_reported(self):
        sup = lifecycle.Supervisor()

        async def slow():
            await asyncio.sleep(5)

        sup.push("slow", slow)
        sup.push("fast", lambda: None)
        incomplete = await sup.aclose(0.3)
        self.assertIn("slow", incomplete)

    async def test_failing_step_recorded_others_still_run(self):
        ran = []
        sup = lifecycle.Supervisor()
        sup.push("ok", lambda: ran.append("ok"))
        sup.push("boom", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
        incomplete = await sup.aclose(1.0)
        self.assertEqual(ran, ["ok"])
        self.assertTrue(any("boom" in s for s in incomplete))


class ParentLivenessUnitTests(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(lifecycle.parent_alive(os.getpid()))

    def test_dead_pid_not_alive(self):
        helper = subprocess.Popen([sys.executable, "-c", "pass"])
        helper.wait()
        # A just-exited pid must not read as alive.
        self.assertFalse(lifecycle.parent_alive(helper.pid))

    def test_invalid_pid_not_alive(self):
        self.assertFalse(lifecycle.parent_alive(0))
        self.assertFalse(lifecycle.parent_alive(-1))


class BackendProcess:
    """Spawn python/backend.py on the dedicated test ports with a temp cert dir."""

    def __init__(self, parent_pid=None, extra_env=None):
        self._temp = tempfile.TemporaryDirectory()
        self.certs_dir = self._temp.name
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
               "LEGILIMENS_CONTROL_TOKEN": TOKEN, "LEGILIMENS_CERTS_DIR": self.certs_dir}
        if parent_pid is not None:
            env["LEGILIMENS_PARENT_PID"] = str(parent_pid)
        if extra_env:
            env.update(extra_env)
        # Instrument the real server, not a stub: a cancelled serve() skips this.
        bootstrap = '''
import api, backend, os, asyncio, datetime, certs
original = api.make_api_server
def make_server(port):
    server = original(port)
    shutdown = server.shutdown
    async def observed_shutdown(*args, **kwargs):
        await shutdown(*args, **kwargs)
        print('TEST_API_GRACEFUL_SHUTDOWN', flush=True)
    server.shutdown = observed_shutdown
    return server
api.make_api_server = make_server
if os.environ.get('TEST_ROTATE_CERTIFICATE') == '1':
    original_scheduler = backend._renewal_scheduler
    async def rotate(request_shutdown):
        await asyncio.sleep(1)
        certs.RENEWAL_THRESHOLD = datetime.timedelta(days=14)
        backend.RENEWAL_CHECK_INTERVAL = 0.01
        await original_scheduler(request_shutdown)
    backend._renewal_scheduler = rotate
backend.main()
'''
        env['PYTHONPATH'] = str(ROOT)
        self.proc = subprocess.Popen(
            [sys.executable, '-c', bootstrap,
             "--port-proxy", str(PORTS["proxy"]), "--port-target", str(PORTS["target"]),
             "--port-ws", str(PORTS["ws"]), "--port-api", str(PORTS["api"])],
            cwd=str(REPO), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
        self.ready_instance = None
        self.lines = []
        self._queue = queue.Queue()
        def read_output():
            for line in self.proc.stdout:
                self.lines.append(line)
                self._queue.put(line)
            self._queue.put(None)
        self._reader = threading.Thread(target=read_output, daemon=True)
        self._reader.start()

    def wait_ready(self, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self._queue.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                return None
            if not line:
                if self.proc.poll() is not None:
                    return None
                continue
            if line.startswith("READY"):
                self.ready_instance = line.split("instanceId=")[-1].strip()
                return self.ready_instance
        return None

    def drain(self):
        self.proc.wait(timeout=15)
        self._reader.join(timeout=2)

    def wait_exit(self, timeout=15):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        try:
            self._reader.join(timeout=2)
            if self.proc.stdout and not self.proc.stdout.closed:
                self.proc.stdout.close()
        except Exception:
            pass
        self._temp.cleanup()


class BackendLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        if not _all_ports_free():
            self.skipTest("test ports 1543x already in use")
        self._procs = []

    def tearDown(self):
        for p in self._procs:
            p.kill()
        # ports should be released after teardown
        for _ in range(20):
            if _all_ports_free():
                break
            time.sleep(0.25)

    def _spawn(self, **kw):
        p = BackendProcess(**kw)
        self._procs.append(p)
        return p

    def test_ready_is_authenticated_and_identified(self):
        b = self._spawn()
        rid = b.wait_ready()
        self.assertIsNotNone(rid, "backend never reported READY")
        status, body = _health()
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "legilimens")
        self.assertEqual(body["instanceId"], rid, "READY id must match /health id")
        # unauthenticated request is rejected
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{PORTS['api']}/health", timeout=3)
        self.assertEqual(ctx.exception.code, 401)

    def test_parent_death_triggers_clean_exit(self):
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            b = self._spawn(parent_pid=parent.pid)
            self.assertIsNotNone(b.wait_ready(), "backend never reported READY")
            parent.kill()
            parent.wait()
            b.drain()
            code = b.wait_exit(timeout=15)
            self.assertEqual(code, lifecycle.EXIT_OK, "parent death should cause a clean exit")
        finally:
            if parent.poll() is None:
                parent.kill()

    def test_port_conflict_second_instance_exits_nonzero(self):
        first = self._spawn()
        self.assertIsNotNone(first.wait_ready(), "first backend never reported READY")
        second = self._spawn()
        self.assertIsNone(second.wait_ready(timeout=20), "second backend must not report READY")
        code = second.wait_exit(timeout=20)
        self.assertIsNotNone(code, "second backend did not exit")
        self.assertNotEqual(code, 0, "port conflict must yield a nonzero exit")
        # the first instance is unharmed
        self.assertEqual(_health()[0], 200)

    def test_authenticated_shutdown_endpoint_exits_cleanly(self):
        b = self._spawn()
        self.assertIsNotNone(b.wait_ready(), "backend never reported READY")
        # unauthenticated shutdown is rejected
        req = urllib.request.Request(f"http://127.0.0.1:{PORTS['api']}/shutdown", data=b"", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 401)
        self.assertIsNone(b.proc.poll(), "backend stopped on an unauthenticated request")
        # authenticated shutdown stops it cleanly
        req = urllib.request.Request(f"http://127.0.0.1:{PORTS['api']}/shutdown", data=b"",
                                     method="POST", headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual(r.status, 200)
        b.drain()
        self.assertEqual(b.wait_exit(timeout=15), lifecycle.EXIT_OK)
        output = ''.join(b.lines)
        self.assertIn('TEST_API_GRACEFUL_SHUTDOWN', output)
        self.assertNotIn('cleanup incomplete', output.lower())
        self.assertTrue(_all_ports_free())

    def test_api_exit_after_ready_is_failure(self):
        b = self._spawn(extra_env={"LEGILIMENS_TEST_STOP_API_AFTER": "0.5"})
        self.assertIsNotNone(b.wait_ready(), "backend never reported READY")
        b.drain()
        code = b.wait_exit(timeout=15)
        self.assertIsNotNone(code, "backend did not exit after API stopped")
        self.assertNotEqual(code, 0, "unexpected API exit must be a failure")

    def test_rotation_exits_cleanly_and_restart_serves_new_pin(self):
        first = self._spawn(extra_env={'TEST_ROTATE_CERTIFICATE': '1'})
        rid = first.wait_ready()
        self.assertIsNotNone(rid)
        def pin():
            req = urllib.request.Request(f"http://127.0.0.1:{PORTS['api']}/cert-hash",
                                         headers={'Authorization': f'Bearer {TOKEN}'})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.loads(response.read())
        old_pin = pin()
        first.drain()
        self.assertEqual(first.wait_exit(), lifecycle.EXIT_RESTART)
        self.assertNotIn('cleanup incomplete', ''.join(first.lines).lower())
        second = self._spawn(extra_env={'LEGILIMENS_CERTS_DIR': first.certs_dir})
        self.assertNotEqual(second.wait_ready(), rid)
        self.assertNotEqual(pin(), old_pin)

    def test_api_port_conflict_unwinds_already_started_udp_services(self):
        blocker = socket.socket()
        try:
            blocker.bind(('127.0.0.1', PORTS['api']))
            blocker.listen()
            b = self._spawn()
            self.assertIsNone(b.wait_ready())
            b.drain()
            self.assertEqual(b.wait_exit(), lifecycle.EXIT_FAILURE)
            self.assertTrue(_port_free(PORTS['proxy'], udp=True))
            self.assertTrue(_port_free(PORTS['target'], udp=True))
            self.assertTrue(_port_free(PORTS['ws']))
        finally:
            blocker.close()

    def test_repeated_start_stop_reuses_ports_without_orphans(self):
        for _ in range(2):
            parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            try:
                b = self._spawn(parent_pid=parent.pid)
                self.assertIsNotNone(b.wait_ready(), "backend never reported READY in a cycle")
                parent.kill(); parent.wait()
                b.drain()
                self.assertEqual(b.wait_exit(timeout=15), lifecycle.EXIT_OK)
            finally:
                if parent.poll() is None:
                    parent.kill()
            # ports must be free before the next cycle
            for _ in range(20):
                if _all_ports_free():
                    break
                time.sleep(0.25)
            self.assertNotIn('cleanup incomplete', ''.join(b.lines).lower())
            self.assertTrue(_all_ports_free(), "ports not released between cycles")


if __name__ == "__main__":
    unittest.main(verbosity=2)
