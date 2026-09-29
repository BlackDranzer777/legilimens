"""Admission, request budgets, run budgets and snapshot consistency."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
from collections import deque

from test_control_security import request
import api
import proxy
import logger
import control_security as security_module
from control_security import ControlBoundary, security
import attack_runner as runner


class RequestBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def check_body(self, chunks, *, length=None, limit=16):
        called = False
        async def app(scope, receive, send):
            nonlocal called
            called = True
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        headers = [(b"host", b"127.0.0.1:4436"), (b"authorization", ("Bearer " + security.token).encode())]
        if length is not None:
            headers.append((b"content-length", str(length).encode()))
        scope = {"type": "http", "method": "POST", "path": "/tamper", "headers": headers}
        messages = iter(chunks)
        async def receive():
            return next(messages)
        sent = []
        async def send(message):
            sent.append(message)
        boundary = ControlBoundary(app)
        with patch.object(security_module, "MAX_BODY_BYTES", limit):
            await boundary(scope, receive, send)
        self.assertEqual(boundary.inflight, 0)
        return sent[0]["status"], called

    async def test_declared_and_chunked_oversized_bodies_never_reach_route(self):
        self.assertEqual(await self.check_body([], length=17), (413, False))
        chunks = [{"type": "http.request", "body": b"a" * 10, "more_body": True},
                  {"type": "http.request", "body": b"b" * 10, "more_body": False}]
        for length in (None, 1):
            self.assertEqual(await self.check_body(chunks, length=length), (413, False))
        self.assertEqual(await self.check_body([{"type": "http.request", "body": b"x" * 16}]), (200, True))

    async def test_slow_body_times_out_and_releases_admission(self):
        boundary = ControlBoundary(AsyncMock())
        scope = {"type": "http", "method": "POST", "path": "/tamper", "headers": [
            (b"host", b"127.0.0.1:4436"), (b"authorization", ("Bearer " + security.token).encode())]}
        async def receive():
            await asyncio.Event().wait()
        send = AsyncMock()
        with patch.object(security_module, "BODY_TIMEOUT", 0.01):
            await boundary(scope, receive, send)
        self.assertEqual(send.call_args_list[0].args[0]["status"], 408)
        self.assertEqual(boundary.inflight, 0)
        boundary.inflight = security_module.MAX_CONTROL_REQUESTS
        send.reset_mock()
        await boundary(scope, receive, send)
        self.assertEqual(send.call_args_list[0].args[0]["status"], 429)


class TransportBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_buffers_fail_closed_when_full_or_incompatible(self):
        quic = SimpleNamespace(_streams={}, _datagrams_pending=deque())
        self.assertFalse(proxy.transport_over_budget(quic))
        with patch.object(proxy, "MAX_QUIC_BUFFER_BYTES", 16):
            quic._streams[0] = SimpleNamespace(sender=SimpleNamespace(_buffer=b"x" * 8), receiver=SimpleNamespace(_buffer=b"y" * 8))
            self.assertFalse(proxy.transport_over_budget(quic))
            with self.assertRaises(BufferError):
                proxy.require_transport_capacity(quic, 1)
        self.assertTrue(proxy.transport_over_budget(object()))

    async def test_task_budget_closes_session_and_drains_pending_work(self):
        server = Mock()
        session = proxy.ProxySession(server, 0, "fixture")
        async def hold():
            await asyncio.Event().wait()
        with patch.object(proxy, "MAX_SESSION_TASKS", 2), patch.object(proxy, "broadcast", Mock()):
            session.submit(hold, size=10)
            session.submit(hold, size=10)
            tasks = list(session._tasks)
            session.submit(hold, size=10)
            self.assertTrue(session._closed)
            self.assertEqual(len(session._tasks), 2)
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)
            self.assertEqual(session._task_bytes, 0)
            self.assertFalse(session._tasks)
            server.close_session.assert_called_once_with(0)

    async def test_byte_stream_and_session_admission_limits(self):
        with patch.object(proxy, "broadcast", Mock()):
            session = proxy.ProxySession(Mock(), 0, "fixture")
            fn = AsyncMock()
            session.submit(fn, size=proxy.MAX_SESSION_BYTES + 1)
            fn.assert_not_called()
            self.assertTrue(session._closed)
            stream_session = proxy.ProxySession(Mock(), 0, "stream-fixture")
            stream_session._stream_sid = {i: str(i) for i in range(proxy.MAX_SESSION_STREAMS)}
            self.assertTrue(stream_session.admit_stream("incoming", 0))
            self.assertFalse(stream_session.admit_stream("incoming", 999))
            self.assertTrue(stream_session._closed)
        server = Mock(_sessions={})
        with patch.object(proxy, "_budget_sessions", {object()}), patch.object(proxy, "MAX_SESSIONS", 1):
            proxy.ProxyServerProtocol._accept_session(server, 0, "/", [])
            server.send_session_response.assert_called_once_with(0, b"503")
            self.assertFalse(server._sessions)

    async def test_draining_session_keeps_capacity_until_tasks_finish(self):
        session = proxy.ProxySession(Mock(), 0, "fixture")
        lifecycle = asyncio.create_task(asyncio.sleep(0))
        worker = asyncio.create_task(asyncio.sleep(0))
        session._lifecycle_task = lifecycle
        session._tasks.add(worker)
        with patch.object(proxy, "_budget_sessions", {session}):
            await lifecycle
            session.release_budget()
            self.assertIn(session, proxy._budget_sessions)
            await worker
            session._tasks.clear()
            session.release_budget()
            self.assertNotIn(session, proxy._budget_sessions)


class RunBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_size_budget_rejects_before_proxy_callback(self):
        with patch.object(api, "_replay_fn", AsyncMock()) as callback:
            status, _, _ = await request("POST", "/replay", token=security.token, body={
                "sessionId": "fixture", "direction": "incoming", "messageType": "datagram", "payload": "x" * 1025})
            self.assertEqual(status, 413)
            callback.assert_not_awaited()

    async def test_invalid_parameters_and_unsupported_modes_do_not_start_tasks(self):
        for mode, params in [("flooding", {"connections": 129}), ("flooding", {"connections": True}),
                             ("flooding", {"connections": 1.5}), ("flooding", {"extra": 1}),
                             ("loris", {"cycles": 10, "connections": 128}),
                             ("loris", {"cycleDelay": float("nan")}), ("encapsulation", {"packets": 1001})]:
            with self.subTest(mode=mode, params=params), patch.object(runner, "_attacks", {}):
                with self.assertRaises(ValueError):
                    runner.start_attack(mode, "https://127.0.0.1", params)
                self.assertFalse(runner._attacks)
        for mode in ("fuzz", "out_of_joint"):
            status, _, _ = await request("POST", "/attack", token=security.token, body={"type": mode})
            self.assertEqual(status, 400)

    async def test_run_deadline_and_result_size_are_explicit(self):
        async def hold(*_):
            await asyncio.Event().wait()
        with patch.object(runner, "_attacks", {}), patch.object(runner, "broadcast", Mock()), \
                patch.object(runner, "broadcast_async", AsyncMock()), patch.object(runner, "MAX_RUN_SECONDS", 0.01), \
                patch.dict(runner._RUNNERS, {"fixture": hold}):
            aid = runner.start_attack("fixture", "https://127.0.0.1", {})
            await runner._attacks[aid]["task"]
            self.assertEqual(runner.get_attack_status(aid)["status"], "failed")
            self.assertIn("deadline", runner.get_attack_status(aid)["error"])
            runner._RUNNERS["fixture"] = AsyncMock(return_value={"value": "x" * 70000})
            aid = runner.start_attack("fixture", "https://127.0.0.1", {})
            await runner._attacks[aid]["task"]
            self.assertTrue(runner.get_attack_status(aid)["result"]["omitted"])

    async def test_recovery_snapshot_has_atomic_cursor_and_current_state(self):
        with patch.object(runner, "_attacks", {}), patch.object(api, "pending_intercepts", {}):
            status, _, body = await request("GET", "/state", token=security.token)
            self.assertEqual(status, 200)
            snapshot = json.loads(body)
            self.assertEqual(snapshot["epoch"], logger.EPOCH)
            self.assertEqual(snapshot["sequence"], logger.cursor()["sequence"])
            self.assertEqual(snapshot["captureMode"], api.capture_mode)
            self.assertEqual(snapshot["pendingIntercepts"], [])


class IngressBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_thread_ingress_coalesces_callbacks_and_bounds_bytes(self):
        loop = Mock()
        with patch.object(logger, "_get_loop", return_value=loop), \
                patch.object(logger, "_thread_events", deque()), patch.object(logger, "_thread_bytes", 0), \
                patch.object(logger, "_thread_scheduled", False), patch.object(logger, "_thread_overflow", False), \
                patch.object(logger, "MAX_QUEUE_BYTES", 128), patch.object(logger, "_enqueue", Mock()) as enqueue:
            for _ in range(1000):
                logger.broadcast({"message": "payload"})
            loop.call_soon_threadsafe.assert_called_once()
            self.assertLessEqual(logger._thread_bytes, 128)
            self.assertTrue(logger._thread_overflow)
            logger._drain_thread_events()
            self.assertEqual(logger._thread_bytes, 0)
            self.assertFalse(logger._thread_scheduled)
            self.assertTrue(any(json.loads(call.args[0]).get("type") == "resource" for call in enqueue.call_args_list))

    async def test_peer_and_authenticated_subscriber_admission(self):
        client = Mock()
        client.close = AsyncMock()
        with patch.object(logger, "_peers", logger.MAX_PEERS):
            await logger._ws_handler(client)
            self.assertEqual(client.close.call_args.kwargs["code"], 1013)
        client.recv = AsyncMock(return_value=json.dumps({"type": "authenticate", "token": security.token}))
        with patch.object(logger, "_outboxes", {i: object() for i in range(logger.MAX_SUBSCRIBERS)}):
            await logger._ws_handler(client)
            self.assertEqual(client.close.call_args.kwargs["code"], 1013)
        self.assertEqual(logger._peers, 0)
