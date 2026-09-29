"""Control-plane regression tests. All network tests bind ephemeral loopback ports."""

import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

import api
import logger
import proxy
import vulnerable_server
from control_security import BIND_HOST, ControlSecurity, security, valid_host


async def request(method, path, *, token=None, origin=None, host="127.0.0.1:4436", body=None, extra=()):
    """Exercise the actual ASGI middleware and routes without an HTTP test dependency."""
    headers = [(b"host", host.encode())]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    data = b"" if body is None else json.dumps(body).encode()
    headers += [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
    headers += list(extra)
    path, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": query.encode(), "root_path": "", "headers": headers,
             "client": (BIND_HOST, 12345), "server": (BIND_HOST, 4436)}
    messages = []

    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}

    async def send(message):
        messages.append(message)

    await api.app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), payload


class SecurityConfigurationTests(unittest.TestCase):
    def test_random_tokens_and_invalid_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            first, second = ControlSecurity(), ControlSecurity()
            self.assertNotEqual(first.token, second.token)
            self.assertGreaterEqual(len(first.token), 32)
        with patch.dict(os.environ, {"LEGILIMENS_CONTROL_TOKEN": ""}):
            with self.assertRaises(ValueError):
                ControlSecurity()
        self.assertFalse(security.valid_token(None))
        self.assertFalse(security.valid_token("\N{SNOWMAN}"))

    def test_hosts_are_exact_not_suffixes(self):
        for value in ("127.0.0.1:4436", "localhost:4436"):
            self.assertTrue(valid_host(value))
        for value in ("evil.test:4436", "localhost.evil.test:4436", "localhost:4436@evil.test",
                      "127.0.0.1:4436/path", "localhost", "localhost:invalid", "0.0.0.0:4436"):
            self.assertFalse(valid_host(value), value)

    def test_custom_ports_preserve_origin_allowlist_reference(self):
        with patch.dict(os.environ, {}, clear=True):
            config = ControlSecurity()
        origins = config.origins
        config.configure(5446, 5445, 5443)
        self.assertIs(origins, config.origins)
        self.assertIn("http://127.0.0.1:5446", origins)
        self.assertNotIn("http://127.0.0.1:4436", origins)
        self.assertNotIn("null", origins)


class ApiSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_requires_and_forwards_explicit_session_identity(self):
        body = {"payload": " \t", "direction": "incoming", "messageType": "datagram"}
        callback = AsyncMock(return_value={"ok": True, "sessionId": "chosen"})
        with patch.object(api, "_replay_fn", callback):
            status, _, _ = await request("POST", "/replay", token=security.token, body=body)
            self.assertEqual(status, 422)
            status, _, _ = await request("POST", "/replay", token=security.token, body={**body, "sessionId": ""})
            self.assertEqual(status, 400)
            callback.assert_not_awaited()
            status, _, _ = await request("POST", "/replay", token=security.token, body={**body, "sessionId": "chosen"})
            self.assertEqual(status, 200)
            callback.assert_awaited_once_with("incoming", "datagram", " \t", "chosen")
            callback.return_value = {"ok": False, "error": "selected session is closed"}
            status, _, payload = await request("POST", "/replay", token=security.token, body={**body, "sessionId": "closed"})
            self.assertEqual(status, 409)
            self.assertEqual(json.loads(payload)["detail"], "selected session is closed")

    async def test_every_control_route_requires_authentication(self):
        for route in api.app.routes:
            if not hasattr(route, "methods"):
                continue
            path = route.path.replace("{attack_id}", "example").replace("{intercept_id}", "example")
            for method in route.methods:
                with self.subTest(method=method, path=path):
                    status, _, payload = await request(method, path, body={})
                    self.assertEqual(status, 401)
                    self.assertNotIn(security.token.encode(), payload)

    async def test_rejected_writes_have_no_side_effects(self):
        before = copy.deepcopy(api.tamper_rule)
        with patch("attack_runner.start_attack") as attack:
            for token in (None, "incorrect"):
                status, _, _ = await request("POST", "/tamper", token=token, body={"enabled": True})
                self.assertEqual(status, 401)
                status, _, _ = await request("POST", "/attack", token=token,
                                             body={"type": "flooding"})
                self.assertEqual(status, 401)
            attack.assert_not_called()
        self.assertEqual(api.tamper_rule, before)

    async def test_trusted_browser_and_cli_can_read_and_write(self):
        before = copy.deepcopy(api.tamper_rule)
        self.addCleanup(setattr, api, "tamper_rule", before)
        for origin in (None, "http://127.0.0.1:5180", "http://localhost:4436"):
            status, headers, payload = await request("GET", "/health", token=security.token, origin=origin)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload)["service"], "legilimens")
            self.assertNotIn(security.token.encode(), payload)
            self.assertEqual(headers[b"cache-control"], b"no-store")
            if origin:
                self.assertEqual(headers[b"access-control-allow-origin"].decode(), origin)
            status, _, _ = await request("POST", "/tamper", token=security.token, origin=origin,
                                         body={"enabled": True})
            self.assertEqual(status, 200)
            self.assertTrue(api.tamper_rule["enabled"])

    async def test_untrusted_origins_are_blocked_even_with_token(self):
        for origin in ("https://untrusted.example", "null", "http://localhost:5181",
                       "http://localhost.evil.test:5180", "http://127.0.0.1:5180/"):
            status, headers, _ = await request("POST", "/tamper", token=security.token,
                                              origin=origin, body={"enabled": True})
            self.assertEqual(status, 403, origin)
            self.assertNotIn(b"access-control-allow-origin", headers)

    async def test_host_rebinding_duplicate_headers_and_query_tokens_rejected(self):
        for host in ("evil.test:4436", "127.0.0.1.evil.test:4436"):
            status, _, _ = await request("GET", "/health", host=host, token=security.token)
            self.assertEqual(status, 403)
        for extra, expected in (
            ([(b"host", b"localhost:4436")], 403),
            ([(b"origin", b"null")], 403),
            ([(b"authorization", b"Bearer wrong")], 401),
        ):
            status, _, _ = await request("GET", "/health", token=security.token,
                                         origin="http://127.0.0.1:5180", extra=extra)
            self.assertEqual(status, expected)
        status, _, _ = await request("GET", f"/health?token={security.token}")
        self.assertEqual(status, 401)

    async def test_cors_preflight_and_unauthorized_response(self):
        headers = [(b"access-control-request-method", b"POST"),
                   (b"access-control-request-headers", b"authorization,content-type")]
        status, result, _ = await request("OPTIONS", "/tamper", origin="http://127.0.0.1:5180", extra=headers)
        self.assertEqual(status, 200)
        self.assertNotEqual(result[b"access-control-allow-origin"], b"*")
        status, _, _ = await request("OPTIONS", "/tamper", origin="https://untrusted.example", extra=headers)
        self.assertEqual(status, 403)
        status, result, _ = await request("GET", "/health", origin="http://127.0.0.1:5180")
        self.assertEqual(status, 401)
        self.assertEqual(result[b"access-control-allow-origin"], b"http://127.0.0.1:5180")

    async def test_ui_assets_public_but_documentation_not_exposed(self):
        if (api._ui / "index.html").exists():
            status, headers, body = await request("GET", "/")
            self.assertEqual(status, 200)
            self.assertNotIn(security.token.encode(), body)
            self.assertEqual(headers[b"x-frame-options"], b"DENY")
        for path in ("/docs", "/openapi.json", "/redoc"):
            status, _, _ = await request("GET", path)
            self.assertEqual(status, 401)

    async def test_service_entrypoints_bind_only_loopback(self):
        with patch.object(api.uvicorn, "Config") as config, patch.object(api.uvicorn, "Server") as server:
            server.return_value.serve = AsyncMock()
            await api.start_api(5446)
            self.assertEqual(config.call_args.kwargs["host"], BIND_HOST)
            self.assertFalse(config.call_args.kwargs["proxy_headers"])
        for module, start in ((proxy, proxy.start_proxy), (vulnerable_server, vulnerable_server.start_vulnerable_server)):
            with patch.object(module, "serve", new_callable=AsyncMock) as serve, \
                 patch.object(module, "QuicConfiguration", return_value=Mock()), \
                 patch.object(module, "harden_udp_server", return_value=True):
                await start(5443)
                self.assertEqual(serve.call_args.args[0], BIND_HOST)


class WebSocketSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = await logger.start_logger(0)
        host, port = self.server.sockets[0].getsockname()[:2]
        self.assertEqual(host, BIND_HOST)
        self.url = f"ws://{host}:{port}/"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.assertFalse(logger._clients)

    async def test_untrusted_origins_and_query_tokens_fail_handshake(self):
        for origin in ("https://untrusted.example", "null", "http://localhost:5181"):
            with self.assertRaises(InvalidStatus) as caught:
                async with connect(self.url, origin=origin):
                    self.fail("Untrusted origin connected")
            self.assertEqual(caught.exception.response.status_code, 403)
        with self.assertRaises(InvalidStatus):
            async with connect(self.url + "?token=" + security.token):
                self.fail("Token in URL accepted")

    async def test_host_header_is_validated(self):
        from websockets.datastructures import Headers
        connection = Mock()
        logger._check_handshake(connection, Mock(path="/", headers=Headers({"Host": "evil.test:4435"})))
        self.assertEqual(connection.respond.call_args.args[0], 403)

    async def test_wrong_missing_or_malformed_auth_never_receives_captures(self):
        for message in ("not-json", "[]", json.dumps({"type": "authenticate", "token": "wrong"}),
                        json.dumps({"type": "authenticate"})):
            async with connect(self.url, origin="http://127.0.0.1:5180") as ws:
                await ws.send(message)
                with self.assertRaises(ConnectionClosed) as caught:
                    await ws.recv()
                self.assertEqual(caught.exception.rcvd.code, 1008)
                self.assertFalse(logger._clients)
        with patch.object(logger, "AUTH_TIMEOUT", 0.05):
            async with connect(self.url) as ws:
                await logger.broadcast_async({"payload": "must-not-leak"})
                with self.assertRaises(ConnectionClosed) as caught:
                    await ws.recv()
                self.assertEqual(caught.exception.rcvd.code, 1008)

    async def test_authenticated_browser_and_cli_receive_captures(self):
        for origin in (None, "http://127.0.0.1:5180"):
            async with connect(self.url, origin=origin) as ws:
                self.assertFalse(logger._clients)
                await ws.send(json.dumps({"type": "authenticate", "token": security.token}))
                self.assertEqual(json.loads(await ws.recv())["type"], "authenticated")
                await logger.broadcast_async({"payload": "fixture-only"})
                packet = json.loads(await asyncio.wait_for(ws.recv(), 1))
                self.assertEqual(packet["event"], {"payload": "fixture-only"})
                self.assertEqual(packet["epoch"], logger.EPOCH)
            await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
