"""Settings and lifecycle regressions; attack runners are inert test doubles."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_control_security import request
import api
import attack_runner as runner
from control_security import security


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_manual_updates_leave_settings_and_queue_unchanged(self):
        baseline = copy.deepcopy(api.manual_intercept)
        with patch.object(api, "manual_intercept", baseline), patch.object(api, "_forward_all_pending") as forward:
            before = copy.deepcopy(baseline)
            for change in (
                {"directions": ["incoming"], "timeoutMs": 999},
                {"directions": ["outgoing"], "types": []},
                {"directions": ["incoming", "typo"]},
                {"types": ["stream", "typo"]},
                {"enabled": False, "types": ["stream"], "timeoutMs": 300001},
                {"timeoutMs": "invalid"},
            ):
                with self.subTest(change=change):
                    status, _, _ = await request("POST", "/intercept/manual", token=security.token, body=change)
                    self.assertIn(status, (400, 422))
                    self.assertEqual(api.manual_intercept, before)
                    forward.assert_not_called()

    async def test_valid_manual_update_commits_before_forwarding(self):
        config = copy.deepcopy(api.manual_intercept)
        config["enabled"] = True
        def forward():
            self.assertEqual(config, {"enabled": False, "directions": ["incoming"], "types": ["stream"], "timeoutMs": 1000})
            return 2
        with patch.object(api, "manual_intercept", config), patch.object(api, "_forward_all_pending", side_effect=forward):
            status, _, body = await request("POST", "/intercept/manual", token=security.token, body={
                "enabled": False, "directions": ["incoming", "incoming"], "types": ["stream"], "timeoutMs": 1000})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["autoForwarded"], 2)

    async def test_tamper_toggle_preserves_existing_scope(self):
        original = {"enabled": False, "field": "score", "value": "42", "matchField": "player", "matchValue": "Alice"}
        with patch.object(api, "tamper_rule", original):
            status, _, body = await request("POST", "/tamper", token=security.token, body={"enabled": True})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {**original, "enabled": True})
            await request("POST", "/tamper", token=security.token, body={"matchField": "", "matchValue": ""})
            self.assertEqual(api.tamper_rule["matchField"], "")

    async def test_invalid_target_preserves_existing_target(self):
        before = copy.deepcopy(api.target_config)
        for change in ({"host": "new.test", "port": 0}, {"host": "new.test", "port": 443, "certHash": "bad"}):
            status, _, _ = await request("POST", "/target", token=security.token, body=change)
            self.assertEqual(status, 400)
            self.assertEqual(api.target_config, before)


class AttackStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.records = patch.object(runner, "_attacks", {})
        self.records.start()
        self.broadcast = AsyncMock()
        self.emitter = patch.object(runner, "broadcast_async", self.broadcast)
        self.emitter.start()
        self.sync_broadcast = Mock()
        self.sync_emitter = patch.object(runner, "broadcast", self.sync_broadcast)
        self.sync_emitter.start()
        self.addCleanup(self.sync_emitter.stop)
        self.addCleanup(self.records.stop)
        self.addCleanup(self.emitter.stop)

    async def asyncTearDown(self):
        for record in runner._attacks.values():
            if not record["task"].done():
                await runner.stop_attack_task(record["attackId"])
        await asyncio.sleep(0)

    def start(self, fn):
        with patch.dict(runner._RUNNERS, {"fixture": fn}):
            return runner.start_attack("fixture", "https://127.0.0.1", {})

    async def assert_terminal(self, attack_id, status):
        await asyncio.sleep(0)
        record = runner.get_attack_status(attack_id)
        self.assertEqual(record["status"], status)
        self.assertIsNotNone(record["completedAt"])
        events = [call.args[0] for call in self.broadcast.await_args_list + self.sync_broadcast.call_args_list]
        terminal = [e for e in events if e["status"] in runner._TERMINAL]
        self.assertEqual([e["status"] for e in terminal], [status])
        return record

    async def test_stop_before_coroutine_starts(self):
        fn = AsyncMock()
        aid = self.start(fn)
        self.assertTrue(await runner.stop_attack_task(aid))
        fn.assert_not_awaited()
        await self.assert_terminal(aid, "stopped")

    async def test_stop_during_initial_broadcast(self):
        entered = asyncio.Event()
        async def broadcast(event):
            if event["status"] == "running":
                entered.set()
                await asyncio.Event().wait()
        self.broadcast.side_effect = broadcast
        fn = AsyncMock()
        aid = self.start(fn)
        await entered.wait()
        await runner.stop_attack_task(aid)
        fn.assert_not_awaited()
        await self.assert_terminal(aid, "stopped")

    async def test_concurrent_stop_waits_for_cleanup_and_reports_same_state(self):
        entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        callbacks = []
        async def run(_target, _params, cb):
            callbacks.append(cb)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await release.wait()
        aid = self.start(run)
        await entered.wait()
        first = asyncio.create_task(api.stop_attack(aid))
        await cleanup.wait()
        second = asyncio.create_task(api.stop_attack(aid))
        await asyncio.sleep(0)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        release.set()
        results = await asyncio.gather(first, second)
        self.assertEqual([r["status"] for r in results], ["stopped", "stopped"])
        record = await self.assert_terminal(aid, "stopped")
        self.assertIsNone(record["error"])
        calls = self.sync_broadcast.call_count
        callbacks[0](1, 1, "late")
        await asyncio.sleep(0)
        self.assertEqual(self.sync_broadcast.call_count, calls)

    async def test_stop_preserves_completed_and_failed_results(self):
        for error in (None, RuntimeError("fixture failure")):
            self.broadcast.reset_mock()
            self.sync_broadcast.reset_mock()
            fn = AsyncMock(return_value={"fixture": True}, side_effect=error)
            aid = self.start(fn)
            await runner._attacks[aid]["task"]
            expected = "failed" if error else "complete"
            status, _, body = await request("POST", f"/attack/{aid}/stop", token=security.token)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["status"], expected)
            await self.assert_terminal(aid, expected)

    async def test_unknown_stop_returns_404(self):
        status, _, _ = await request("POST", "/attack/missing/stop", token=security.token)
        self.assertEqual(status, 404)
