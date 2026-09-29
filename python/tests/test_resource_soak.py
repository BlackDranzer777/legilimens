"""Opt-in 30-second loopback QUIC/WS workload; emits measured JSON, not a benchmark claim.

PowerShell: $env:LEGILIMENS_SOAK='1'; .venv/Scripts/python.exe -m unittest discover -s python/tests -p test_resource_soak.py -v
"""
import asyncio
from contextlib import AsyncExitStack
import gc
import json
import os
import time
import tracemalloc
import unittest
from unittest.mock import patch

import test_proxy_compatibility as compatibility
import proxy
import logger
from control_security import security
from websockets.asyncio.client import connect


@unittest.skipUnless(os.environ.get("LEGILIMENS_SOAK") == "1", "opt-in local soak")
class ResourceSoakTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = compatibility.ProxyCompatibilityTests.asyncSetUp
    asyncTearDown = compatibility.ProxyCompatibilityTests.asyncTearDown
    server_config = compatibility.ProxyCompatibilityTests.server_config
    client = compatibility.ProxyCompatibilityTests.client

    async def test_four_clients_datagrams_streams_and_capture_memory(self):
        server = await logger.start_logger(0)
        capture_count = 0
        max_tasks = max_bytes = 0
        samples = []
        start = time.monotonic()
        tracemalloc.start()
        try:
            async with AsyncExitStack() as stack:
                stack.enter_context(patch.object(proxy, "broadcast_async", logger.broadcast_async))
                capture = await stack.enter_async_context(connect(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/"))
                await capture.send(json.dumps({"type": "authenticate", "token": security.token}))
                await capture.recv()
                async def drain():
                    nonlocal capture_count
                    async for message in capture:
                        event = json.loads(message)
                        self.assertIn("sequence", event)
                        capture_count += 1
                reader = asyncio.create_task(drain())
                clients = [await stack.enter_async_context(self.client()) for _ in range(4)]
                async def worker(client, number):
                    nonlocal max_tasks, max_bytes
                    count = 0
                    sid = client._http.create_webtransport_stream(client.get_session_id())
                    client.register_wt_data_stream(sid)
                    while time.monotonic() - start < 30:
                        payload = (f"{number}:{count}:".encode() + b"x" * 256)
                        client.send_datagram(payload)
                        self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), payload)
                        if count % 10 == 0:
                            client._quic.send_stream_data(sid, payload)
                            client.transmit()
                            received = b""
                            while len(received) < len(payload):
                                _, data, _ = await asyncio.wait_for(client.streams.get(), 2)
                                received += data
                            self.assertEqual(received, payload)
                        max_tasks = max(max_tasks, *(len(s._tasks) for s in proxy.LIVE_SESSIONS))
                        max_bytes = max(max_bytes, *(s._task_bytes for s in proxy.LIVE_SESSIONS))
                        count += 1
                        await asyncio.sleep(0.002)
                    return count
                async def sample():
                    for delay in (5, 12, 12):
                        await asyncio.sleep(delay)
                        gc.collect()
                        samples.append(tracemalloc.get_traced_memory()[0])
                counts = await asyncio.gather(*(worker(client, i) for i, client in enumerate(clients)), sample())
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
                peak = tracemalloc.get_traced_memory()[1]
                self.assertGreater(sum(counts[:4]), 100)
                self.assertGreater(capture_count, 100)
                self.assertLess(max(samples) - samples[0], 8 * 1024 * 1024)
                self.assertLessEqual(max_tasks, proxy.MAX_SESSION_TASKS)
                self.assertLessEqual(max_bytes, proxy.MAX_SESSION_BYTES)
                print("RESOURCE_SOAK " + json.dumps({"seconds": round(time.monotonic() - start, 2),
                    "clients": 4, "datagram_roundtrips": sum(counts[:4]), "capture_events": capture_count,
                    "python_heap_samples": samples, "python_heap_peak": peak,
                    "max_session_tasks": max_tasks, "max_session_task_bytes": max_bytes}), flush=True)
            await proxy.disconnect_all()
            self.assertFalse(proxy._budget_sessions)
            self.assertFalse(proxy.LIVE_SESSIONS)
        finally:
            tracemalloc.stop()
            server.close()
            await server.wait_closed()
