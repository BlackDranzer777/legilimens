"""Deterministic saturation checks without external targets or load generation."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_control_security import request
import api
import logger
import attack_runner as runner
from control_security import security


class BroadcastLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturated_client_does_not_block_healthy_client(self):
        slow, fast = Mock(), Mock()
        slow.send = AsyncMock()
        slow.close = AsyncMock()
        slowbox, fastbox = logger.Outbox(), logger.Outbox()
        with patch.object(logger, "_outboxes", {slow: slowbox, fast: fastbox}), patch.object(logger, "MAX_QUEUE_BYTES", 100):
            for i in range(30):
                await logger.broadcast_async({"n": i})
                # The healthy subscriber keeps draining while the slow one doesn't.
                fastbox.bytes -= len(fastbox.queue.get_nowait())
            self.assertTrue(slowbox.overflow)
            self.assertLessEqual(slowbox.bytes, 100)
            self.assertFalse(fastbox.overflow)
            await logger._sender(slow, slowbox)
            self.assertEqual(slow.close.call_args.kwargs["code"], 1013)

    async def test_queue_count_and_oversized_event_notice(self):
        client, outbox = Mock(), logger.Outbox()
        with patch.object(logger, "_outboxes", {client: outbox}):
            await logger.broadcast_async({"payload": "x" * (logger.MAX_EVENT_BYTES + 1)})
            notice = outbox.queue.get_nowait()
            outbox.bytes -= len(notice)
            self.assertEqual(json.loads(notice)["event"]["type"], "resource")
            for _ in range(logger.MAX_MESSAGES + 20):
                await logger.broadcast_async({"n": 1})
            self.assertEqual(outbox.queue.qsize(), logger.MAX_MESSAGES)
            self.assertTrue(outbox.overflow)

    async def test_send_timeout_closes_slow_subscriber(self):
        client, outbox = Mock(), logger.Outbox()
        async def blocked(_):
            await asyncio.Event().wait()
        client.send = AsyncMock(side_effect=blocked)
        client.close = AsyncMock()
        outbox.queue.put_nowait("message")
        outbox.bytes = 7
        with patch.object(logger, "SEND_TIMEOUT", 0.01):
            await logger._sender(client, outbox)
        self.assertEqual(outbox.bytes, 0)
        self.assertEqual(client.close.call_args.kwargs["code"], 1013)


class InterceptLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_drops_new_message_and_cancellation_releases_slot(self):
        config = {**api.manual_intercept, "enabled": True}
        with patch.object(api, "manual_intercept", config), patch.object(api, "pending_intercepts", {}), \
                patch.object(api, "_pending_futures", {}), patch.object(api, "MAX_PENDING_INTERCEPTS", 1), \
                patch.object(api, "broadcast_async", AsyncMock()):
            args = dict(session_id="fixture", direction="incoming", message_type="datagram", payload="held", raw_size=4)
            first = asyncio.create_task(api.await_manual_intercept(**args))
            await asyncio.sleep(0)
            second = await api.await_manual_intercept(**args)
            self.assertEqual(second["action"], "drop")
            self.assertEqual(second["status"], "capacity")
            self.assertEqual(len(api.pending_intercepts), 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(api.pending_intercepts, {})
            self.assertEqual(api._pending_futures, {})

    async def test_intercept_byte_limits_and_edited_payload_limit(self):
        with patch.object(api, "manual_intercept", {**api.manual_intercept, "enabled": True}), \
                patch.object(api, "MAX_PENDING_BYTES", 3), patch.object(api, "broadcast_async", AsyncMock()):
            result = await api.await_manual_intercept(session_id="fixture", direction="incoming", message_type="datagram", payload="\u00e9\u00e9", raw_size=4)
            self.assertEqual(result["action"], "drop")
        fut = asyncio.get_running_loop().create_future()
        with patch.object(api, "pending_intercepts", {"held": {"payload": "x"}}), \
                patch.object(api, "_pending_futures", {"held": fut}), patch.object(api, "MAX_INTERCEPT_PAYLOAD_BYTES", 3):
            status, _, _ = await request("POST", "/intercept/held/decision", token=security.token,
                                         body={"action": "forward", "payload": "long"})
            self.assertEqual(status, 413)
            self.assertFalse(fut.done())
            with patch.object(api, "MAX_PENDING_BYTES", 2):
                status, _, _ = await request("POST", "/intercept/held/decision", token=security.token,
                                             body={"action": "forward", "payload": "xx"})
                self.assertEqual(status, 413)
                self.assertFalse(fut.done())
        fut.cancel()


class AttackLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_eviction_preserves_active_tasks_and_rejects_capacity(self):
        entered = asyncio.Event()
        async def blocked(*_):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(runner, "_attacks", {}), patch.object(runner, "broadcast", Mock()), \
                patch.object(runner, "broadcast_async", AsyncMock()), patch.object(runner, "MAX_ACTIVE_ATTACKS", 1), \
                patch.object(runner, "MAX_ATTACK_HISTORY", 3), patch.dict(runner._RUNNERS, {"fixture": blocked}):
            aid = runner.start_attack("fixture", "https://127.0.0.1", {})
            await entered.wait()
            try:
                with self.assertRaises(runner.AttackCapacityError):
                    runner.start_attack("fixture", "https://127.0.0.1", {})
            finally:
                await runner.stop_attack_task(aid)
            runner._RUNNERS["fixture"] = AsyncMock(return_value={})
            for _ in range(8):
                current = runner.start_attack("fixture", "https://127.0.0.1", {})
                await runner._attacks[current]["task"]
            self.assertEqual(len(runner._attacks), 3)
            self.assertNotIn(aid, runner._attacks)

    async def test_stop_timeout_does_not_claim_task_stopped_or_lose_tracking(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(*_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await release.wait()
        with patch.object(runner, "_attacks", {}), patch.object(runner, "broadcast", Mock()), \
                patch.object(runner, "broadcast_async", AsyncMock()), patch.object(runner, "STOP_TIMEOUT", 0.01), \
                patch.dict(runner._RUNNERS, {"fixture": blocked}):
            aid = runner.start_attack("fixture", "https://127.0.0.1", {})
            await entered.wait()
            try:
                status, _, _ = await request("POST", f"/attack/{aid}/stop", token=security.token)
                self.assertEqual(status, 504)
                self.assertFalse(runner._attacks[aid]["task"].done())
                self.assertNotEqual(runner.get_attack_status(aid)["status"], "stopped")
            finally:
                release.set()
                await runner._attacks[aid]["task"]
            self.assertEqual(runner.get_attack_status(aid)["status"], "stopped")
