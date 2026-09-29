import copy
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
import run_interop as h
from support import Proc, allocate_ports, bound_socket, port_free


def passing_report():
    side = {"cases": {**h.EXPECTED, "datagrams": dict.fromkeys(h.DATAGRAMS, True)},
            "two_client_isolation": True, "reconnect": True}
    return {"baseline": copy.deepcopy(side), "proxied": copy.deepcopy(side),
            "control": dict.fromkeys(h.CONTROL, True), "cleanup": True}


class VerdictTests(unittest.TestCase):
    def test_valid_report_passes(self):
        self.assertTrue(all(ok for _, ok in h._summarize(passing_report())))

    def test_each_missing_required_result_fails(self):
        for side in ("baseline", "proxied"):
            for key in h.EXPECTED:
                report = passing_report()
                del report[side]["cases"][key]
                self.assertFalse(all(ok for _, ok in h._summarize(report)), (side, key))
            for name in h.DATAGRAMS:
                report = passing_report()
                report[side]["cases"]["datagrams"][name] = False
                self.assertFalse(all(ok for _, ok in h._summarize(report)))

    def test_failed_baseline_is_not_hidden_by_successful_proxy(self):
        for key in ("two_client_isolation", "reconnect"):
            report = passing_report()
            report["baseline"][key] = False
            self.assertFalse(all(ok for _, ok in h._summarize(report)))

    def test_matching_wrong_metadata_fails(self):
        report = passing_report()
        for side in ("baseline", "proxied"):
            report[side]["cases"]["origin"] = "wrong-origin"
        self.assertFalse(all(ok for _, ok in h._summarize(report)))

    def test_truthy_non_boolean_does_not_pass(self):
        report = passing_report()
        report["proxied"]["cases"]["connect"] = "false"
        self.assertFalse(all(ok for _, ok in h._summarize(report)))

    def test_cleanup_and_execution_errors_fail(self):
        for key, value in (("cleanup", False), ("error", "failed")):
            report = passing_report()
            report[key] = value
            self.assertFalse(all(ok for _, ok in h._summarize(report)))

    def test_requested_browser_requires_result(self):
        report = passing_report()
        report["browser_requested"] = True
        self.assertFalse(all(ok for _, ok in h._summarize(report)))

    def test_capture_success_does_not_hide_browser_transport_failure(self):
        report = passing_report()
        report["browser_requested"] = True
        side = {"datagrams": [True] * 4, "bidiSmall": True, "bidiMultichunk": True,
                "uni": True, "twoTabs": True, "reconnect": True,
                "emptyDatagram": False, "datagramAfterEmpty": False}
        report["browser"] = {"passed": True, "captureRoundtrip": True,
                             "direct": side, "proxied": side}
        checks = dict(h._summarize(report))
        self.assertIs(checks["browser.capture_roundtrip"], True)
        self.assertIs(checks["browser.direct.nonempty_datagrams"], True)
        self.assertIs(checks["browser.direct.emptyDatagram"], False)
        self.assertFalse(all(checks.values()))

    def test_wrong_session_xor_is_rejected(self):
        self.assertFalse(h.replay_delivered_to_selected("a", {"a": False, "b": True}))
        self.assertFalse(h.replay_delivered_to_selected("a", {"a": True, "b": True}))
        self.assertFalse(h.replay_delivered_to_selected("a", {"a": False, "b": False}))
        self.assertTrue(h.replay_delivered_to_selected("a", {"a": True, "b": False}))

    def test_capture_requires_exact_payload_and_metadata(self):
        event = {"id": "e", "type": "datagram", "direction": "incoming", "payload": "abc",
                 "payloadEncoding": "utf8", "rawSize": 3, "timestamp": 1,
                 "sessionId": "a", "target": "127.0.0.1:1", "flag": "normal"}
        self.assertTrue(h.valid_capture(event, "abc", "a", "127.0.0.1:1"))
        for key, value in (("payload", "xabc"), ("rawSize", 4), ("sessionId", "b"), ("target", "wrong")):
            self.assertFalse(h.valid_capture({**event, key: value}, "abc", "a", "127.0.0.1:1"))


class ProcessTests(unittest.TestCase):
    def test_silent_child_timeout_and_cleanup(self):
        proc = Proc([sys.executable, "-c", "import time; time.sleep(20)"], h.ROOT)
        try:
            start = time.monotonic()
            self.assertIsNone(proc.wait_line(lambda line: line == "READY", timeout=0.1))
            self.assertLess(time.monotonic() - start, 1)
        finally:
            proc.stop()
        self.assertIsNotNone(proc.p.returncode)

    def test_drains_output_after_ready_and_retention_is_bounded(self):
        proc = Proc([sys.executable, "-u", "-c",
                     "print('READY'); [print('x'*1000) for _ in range(4000)]; print('DONE')"], h.ROOT)
        try:
            self.assertIsNotNone(proc.wait_line(lambda line: line == "READY"))
            self.assertIsNotNone(proc.wait_line(lambda line: line == "DONE"))
            self.assertLessEqual(len(proc.snapshot()), 2048)
        finally:
            proc.stop()

    def test_early_exit_output_available(self):
        proc = Proc([sys.executable, "-c", "print('startup error')"], h.ROOT)
        try:
            self.assertIsNone(proc.wait_line(lambda line: line == "READY", 3))
            self.assertIn("startup error", proc.snapshot())
        finally:
            proc.stop()

    def test_protocol_aware_busy_ports(self):
        for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
            with bound_socket(0, kind) as sock:
                port = sock.getsockname()[1]
                self.assertFalse(port_free(port, kind))
            self.assertTrue(port_free(port, kind))

    def test_allocated_ports_are_free(self):
        ports = allocate_ports(h.KINDS)
        self.assertTrue(all(port_free(ports[name], kind) for name, kind in h.KINDS.items()))


class PayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_http3_alpn_is_rejected(self):
        client = object.__new__(h.wt_client.WTClient)
        client._alpn = None
        with self.assertRaisesRegex(RuntimeError, "ALPN"):
            await client.connect_session("/echo", "localhost:1234")

    async def test_substring_is_not_exact_receipt(self):
        class Client:
            async def recv_datagram(self, timeout):
                return b'{"score": 10, "player": "changed"}'
        self.assertFalse(await h.received_exact(Client(), b'{"score": 10, "player": "Other"}'))


@unittest.skipUnless(os.environ.get("INTEROP_INTEGRATION") == "1", "opt-in real Go/backend test")
class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_session_fault_fails_and_fresh_scratch_is_cleaned(self):
        real_api = h.api

        def wrong_session(method, path, body=None):
            if method == "POST" and path == "/replay":
                _, sessions = real_api("GET", "/sessions")
                other = next(item["id"] for item in sessions["items"] if item["id"] != body["sessionId"])
                body = {**body, "sessionId": other}
            return real_api(method, path, body)

        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp) / "initially-absent" / "scratch"
            report_path = Path(temp) / "initially-absent-report" / "report.json"
            args = SimpleNamespace(scratch=str(scratch), report=str(report_path), browser=False)
            with patch.object(h, "api", wrong_session):
                report = await h.amain(args)
            self.assertNotIn("error", report)
            self.assertIs(report["control"]["replay_to_selected_session"], False)
            self.assertFalse(all(ok for _, ok in h._summarize(report)))
            self.assertIs(report["cleanup"], True)
            self.assertTrue(report_path.exists())
            self.assertEqual(list(scratch.iterdir()), [], "Owned certificates/run directories leaked")


if __name__ == "__main__":
    unittest.main()
