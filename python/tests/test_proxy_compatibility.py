"""Local QUIC integration tests; no external services or installed certificates.

Run: .venv/Scripts/python.exe -m unittest discover -s python/tests -v
"""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import ssl
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import DatagramReceived, HeadersReceived, WebTransportStreamDataReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import api
import proxy


class EchoProtocol(QuicConnectionProtocol):
    """Binary echo fixture which also enforces handshake origin and credentials."""

    rejection_mode = None

    def __init__(self, *args, requests, **kwargs):
        super().__init__(*args, **kwargs)
        self.http = None
        self.requests = requests
        self.uni_streams = {}
        self.local_streams = set()
        self.stream_replies = asyncio.Queue()

    def quic_event_received(self, event):
        if self.http is None:
            self.http = H3Connection(self._quic, enable_webtransport=True)
        if isinstance(event, StreamDataReceived) and event.stream_id in self.local_streams:
            self.stream_replies.put_nowait((event.data, event.end_stream))
            return
        for item in self.http.handle_event(event):
            if isinstance(item, HeadersReceived):
                self.requests.put_nowait(item.headers)
                if self.rejection_mode:
                    if self.rejection_mode == "close":
                        self.close(error_code=0x100, reason_phrase="Admission denied")
                    elif self.rejection_mode == "reset":
                        self._quic.reset_stream(item.stream_id, error_code=0x10B)
                    else:
                        self._quic.stop_stream(item.stream_id, error_code=0x10B)
                    self.transmit()
                    continue
                headers = dict(item.headers)
                authenticated = (headers.get(b"origin") == b"https://app.example"
                                 and headers.get(b"authorization") == b"Bearer test-token")
                self.http.send_headers(item.stream_id, [
                    (b":status", b"200" if authenticated else b"401"),
                    (b"x-test-target", b"echo"),
                ], end_stream=not authenticated)
            elif isinstance(item, DatagramReceived):
                if item.data == b"open-server-stream":
                    sid = self.http.create_webtransport_stream(item.stream_id)
                    self.local_streams.add(sid)
                    self._quic.send_stream_data(sid, b"\xffserver", end_stream=True)
                else:
                    self.http.send_datagram(item.stream_id, item.data)
            elif isinstance(item, WebTransportStreamDataReceived):
                sid = item.stream_id
                if sid & 2:
                    if sid not in self.uni_streams:
                        self.uni_streams[sid] = self.http.create_webtransport_stream(
                            item.session_id, is_unidirectional=True)
                    sid = self.uni_streams[sid]
                self._quic.send_stream_data(sid, item.data, end_stream=item.stream_ended)
        self.transmit()


class ProxyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        now = datetime.now(timezone.utc)
        key = ec.generate_private_key(ec.SECP256R1())
        self.key = key
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        self.cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                     .public_key(key.public_key()).serial_number(x509.random_serial_number())
                     .not_valid_before(now - timedelta(minutes=1))
                     .not_valid_after(now + timedelta(days=1))
                     .add_extension(x509.SubjectAlternativeName([
                         x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
                     ]), critical=False).sign(key, hashes.SHA256()))
        self.cert_path = Path(self.tmp.name) / "cert.pem"
        self.key_path = Path(self.tmp.name) / "key.pem"
        self.cert_path.write_bytes(self.cert.public_bytes(serialization.Encoding.PEM))
        self.key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                 serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.pin = base64.b64encode(self.cert.fingerprint(hashes.SHA256())).decode()
        self.requests = asyncio.Queue()
        self.targets = []
        self.patches = [
            patch.object(api, "capture_mode", "capturing"),
            patch.object(api, "tamper_rule", dict(api.tamper_rule, enabled=False)),
            patch.object(api, "manual_intercept", dict(api.manual_intercept, enabled=False)),
            patch.object(proxy, "broadcast_async", AsyncMock()),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        def target_protocol(*a, **kw):
            protocol = EchoProtocol(*a, requests=self.requests, **kw)
            self.targets.append(protocol)
            return protocol
        self.target_server = await serve("127.0.0.1", 0, configuration=self.server_config(),
                                         create_protocol=target_protocol)
        self.proxy_server = await serve("127.0.0.1", 0, configuration=self.server_config(),
                                        create_protocol=proxy.ProxyServerProtocol)
        self.target_port = self.target_server._transport.get_extra_info("sockname")[1]
        self.proxy_port = self.proxy_server._transport.get_extra_info("sockname")[1]
        target_patch = patch.object(api, "target_config", {
            "host": "127.0.0.1", "port": self.target_port, "certHash": self.pin})
        target_patch.start()
        self.addCleanup(target_patch.stop)

    def server_config(self):
        config = QuicConfiguration(is_client=False, alpn_protocols=H3_ALPN,
                                   max_datagram_frame_size=65536)
        config.load_cert_chain(str(self.cert_path), str(self.key_path))
        return config

    async def asyncTearDown(self):
        await proxy.disconnect_all()
        self.proxy_server.close()
        self.target_server.close()
        await asyncio.sleep(0.15)

    @asynccontextmanager
    async def client(self, authenticated=True):
        config = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN,
                                   max_datagram_frame_size=65536, verify_mode=ssl.CERT_REQUIRED)
        config.load_verify_locations(cafile=str(self.cert_path))
        async with connect("127.0.0.1", self.proxy_port, configuration=config,
                           create_protocol=proxy.UpstreamClientProtocol) as client:
            client.datagrams = asyncio.Queue()
            client.streams = asyncio.Queue()
            async def datagram(data):
                await client.datagrams.put(data)
            async def stream(sid, data, ended):
                await client.streams.put((sid, data, ended))
            client.set_callbacks(datagram, stream)
            headers = [(b"origin", b"https://app.example"), (b"cookie", b"session=test"),
                       (b"x-app-version", b"1")]
            if authenticated:
                headers.append((b"authorization", b"Bearer test-token"))
            client.accepted = await asyncio.wait_for(client.connect_webtransport(
                "/transport?room=7", f"127.0.0.1:{self.proxy_port}", headers), 5)
            yield client

    async def test_binary_datagrams_and_original_handshake(self):
        async with self.client() as client:
            self.assertTrue(client.accepted)
            headers = dict(await asyncio.wait_for(self.requests.get(), 2))
            self.assertEqual(headers[b":path"], b"/transport?room=7")
            self.assertEqual(headers[b":authority"], f"127.0.0.1:{self.target_port}".encode())
            self.assertEqual(headers[b"origin"], b"https://app.example")
            self.assertEqual(headers[b"cookie"], b"session=test")
            self.assertEqual(headers[b"x-app-version"], b"1")
            self.assertIn((b"x-test-target", b"echo"), client.response_headers)
            for data in (bytes(range(256)), b"\x00\xff\xfe", b'{"score": 12}', b""):
                client.send_datagram(data)
                self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), data)

    async def test_backend_quic_cleanup_drains_active_protocols(self):
        from backend import _close_quic_server
        async with self.client() as client:
            self.assertTrue(client.accepted)
            protocols = set(self.proxy_server._protocols.values())
            self.assertTrue(protocols)
            await asyncio.wait_for(_close_quic_server(self.proxy_server), 5)
            self.assertTrue(self.proxy_server._transport.is_closing())
            for protocol in protocols:
                await asyncio.wait_for(protocol.wait_closed(), 0.5)

    async def identify_session(self, client, marker):
        client.send_datagram(marker.encode())
        self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), marker.encode())
        events = [call.args[0] for call in proxy.broadcast_async.await_args_list]
        return next(e["sessionId"] for e in events
                    if e.get("payload") == marker and e.get("direction") == "incoming")

    async def test_full_capture_and_replay_preserve_unicode_and_whitespace(self):
        payload = " \t" + "x" * 600 + "\u00e9" * 80 + "\n"
        async with self.client() as client:
            session_id = await self.identify_session(client, payload)
            events = [call.args[0] for call in proxy.broadcast_async.await_args_list]
            capture = next(e for e in events if e.get("sessionId") == session_id and e["direction"] == "incoming" and e["type"] == "datagram")
            self.assertEqual(capture["payload"], payload)
            self.assertEqual(len(capture["payloadPreview"]), 301)
            self.assertTrue(capture["replayable"])
            result = await proxy.replay_message("incoming", "datagram", capture["payload"], session_id)
            self.assertTrue(result["ok"])
            self.assertEqual(result["sessionId"], session_id)
            self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), payload.encode())
            for value in (" \t\n", ""):
                self.assertTrue((await proxy.replay_message("outgoing", "datagram", value, session_id))["ok"])
                self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), value.encode())

    async def test_capture_target_remains_bound_to_session_after_settings_change(self):
        async with self.client() as client:
            sid = await self.identify_session(client, "before-target-change")
            expected = f"127.0.0.1:{self.target_port}"
            # A settings change only affects future sessions, never existing evidence.
            api.target_config = {"host": "unused.invalid", "port": 443, "certHash": ""}
            client.send_datagram(b"after-target-change")
            self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), b"after-target-change")
            self.assertTrue((await proxy.replay_message("outgoing", "datagram", "replayed", sid))["ok"])
            self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), b"replayed")
            events = [call.args[0] for call in proxy.broadcast_async.await_args_list]
            self.assertTrue(any(e["flag"] == "replay" for e in events))
            self.assertTrue(all(e["target"] == expected for e in events if e.get("sessionId") == sid))

    async def test_connection_without_target_has_explicit_unknown_metadata(self):
        event = proxy.make_event(direction="incoming", etype="connection", payload="connected",
                                 raw_size=0, latency=0, flag="normal", session_id="fixture")
        self.assertIsNone(event["target"])

    async def test_replay_routes_only_to_selected_session_in_both_directions(self):
        async with self.client() as first, self.client() as second:
            first_id = await self.identify_session(first, "first-client")
            second_id = await self.identify_session(second, "second-client")
            self.assertNotEqual(first_id, second_id)
            sessions = (await api.get_sessions())["items"]
            self.assertEqual({item["id"] for item in sessions}, {first_id, second_id})
            for direction in ("incoming", "outgoing"):
                for chosen, other, sid in ((first, second, first_id), (second, first, second_id)):
                    payload = "only-" + sid
                    result = await proxy.replay_message(direction, "datagram", payload, sid)
                    self.assertTrue(result["ok"])
                    self.assertEqual(await asyncio.wait_for(chosen.datagrams.get(), 2), payload.encode())
                    with self.assertRaises(asyncio.TimeoutError):
                        await asyncio.wait_for(other.datagrams.get(), 0.05)

    async def test_replay_rejects_unknown_and_closed_sessions_without_fallback(self):
        async with self.client() as first, self.client() as second:
            first_id = await self.identify_session(first, "closing-client")
            second_id = await self.identify_session(second, "remaining-client")
            session = next(s for s in proxy.LIVE_SESSIONS if s.session_uuid == first_id)
            session.server_protocol.close_session(session.client_session_id)
            for sid in ("", "unknown", first_id):
                result = await proxy.replay_message("incoming", "datagram", "must-not-route", sid)
                self.assertFalse(result["ok"])
            self.assertEqual([item["id"] for item in (await api.get_sessions())["items"]], [second_id])
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(second.datagrams.get(), 0.05)

    async def test_replay_rejects_unready_session(self):
        session = proxy.ProxySession(None, 0, "not-ready")
        proxy._register(session)
        try:
            self.assertFalse((await proxy.replay_message("incoming", "datagram", "test", "not-ready"))["ok"])
            self.assertEqual((await api.get_sessions())["items"], [])
        finally:
            proxy._unregister(session)

    async def test_long_binary_and_stream_events_are_not_truncated(self):
        data = bytes(range(256)) * 20
        encoded = base64.b64encode(data).decode()
        for kind in ("datagram", "stream"):
            event = proxy.make_event(direction="incoming", etype=kind, payload=encoded,
                                     raw_size=len(data), latency=0, flag="normal",
                                     payload_encoding="base64", session_id="fixture")
            self.assertEqual(base64.b64decode(event["payload"]), data)
            self.assertFalse(event["replayable"])
        event = proxy.make_event(direction="incoming", etype="stream", payload="x" * 10000,
                                 raw_size=10000, latency=0, flag="normal", session_id="fixture")
        self.assertEqual(len(event["payload"]), 10000)
        session = proxy.ProxySession(None, 0, "fixture")
        await session._log_intercept_drop(direction="incoming", message_type="datagram", raw_size=10, intercept_id=None)
        self.assertFalse(proxy.broadcast_async.await_args.args[0]["replayable"])

    async def test_binary_streams_and_empty_fin(self):
        async with self.client() as client:
            self.assertTrue(client.accepted)
            for uni in (False, True):
                sid = client._http.create_webtransport_stream(client.get_session_id(), is_unidirectional=uni)
                if not uni:
                    client.register_wt_data_stream(sid)
                for data in (bytes(range(256)), b"\xe2", b"\x82\xac", b""):
                    ended = not data
                    client._quic.send_stream_data(sid, data, end_stream=ended)
                    client.transmit()
                    try:
                        _, received, fin = await asyncio.wait_for(client.streams.get(), 2)
                    except asyncio.TimeoutError:
                        self.fail(f"No stream echo: unidirectional={uni}, data={data!r}, fin={ended}")
                    self.assertEqual(received, data)
                    self.assertEqual(fin, ended)

    async def test_completed_streams_do_not_exhaust_active_stream_budget(self):
        async with self.client() as client:
            session_id = await self.identify_session(client, "stream-lifecycle")
            session = next(s for s in proxy.LIVE_SESSIONS if s.session_uuid == session_id)
            for uni in (True, False):
                for index in range(proxy.MAX_QUIC_STREAMS + 8):
                    sid = client._http.create_webtransport_stream(
                        client.get_session_id(), is_unidirectional=uni)
                    if not uni:
                        client.register_wt_data_stream(sid)
                    payload = f"stream-{uni}-{index}".encode()
                    client._quic.send_stream_data(sid, payload, end_stream=True)
                    client.transmit()
                    try:
                        _, received, fin = await asyncio.wait_for(client.streams.get(), 2)
                    except asyncio.TimeoutError:
                        self.fail(f"Completed streams exhausted budget: uni={uni}, index={index}")
                    self.assertEqual(received, payload)
                    self.assertTrue(fin)
                self.assertFalse(session._closed)
                self.assertEqual(session._stream_sid, {})
                self.assertEqual(session._stream_fin, {})
                self.assertEqual(session._stream_locks, {})
                self.assertEqual(session._c2u, {})
                self.assertEqual(session._u2c, {})
                self.assertEqual(session.upstream._wt_data_streams, set())
                self.assertEqual(session.server_protocol._local_stream_sessions, {})
                self.assertLess(len(session.upstream._quic._streams), 16)
                self.assertLess(len(session.server_protocol._quic._streams), 16)
                self.assertLess(len(client._quic._streams), 16)

    async def test_upstream_rejection_is_not_reported_as_success(self):
        async with self.client(authenticated=False) as client:
            self.assertFalse(client.accepted)
            self.assertEqual(dict(client.response_headers)[b":status"], b"401")

    async def check_aborted_connect(self, mode):
        with patch.object(EchoProtocol, "rejection_mode", mode):
            async with self.client() as client:
                self.assertFalse(client.accepted)
                self.assertEqual(dict(client.response_headers)[b":status"], b"502")

    async def test_upstream_close_before_headers_fails_promptly(self):
        await self.check_aborted_connect("close")

    async def test_upstream_connect_reset_fails_promptly(self):
        await self.check_aborted_connect("reset")

    async def test_upstream_connect_stop_sending_fails_promptly(self):
        await self.check_aborted_connect("stop")

    async def test_server_initiated_bidirectional_stream_reply(self):
        async with self.client() as client:
            session_id = await self.identify_session(client, "half-closed-server-stream")
            session = next(s for s in proxy.LIVE_SESSIONS if s.session_uuid == session_id)
            client.send_datagram(b"open-server-stream")
            sid, data, fin = await asyncio.wait_for(client.streams.get(), 2)
            self.assertEqual(data, b"\xffserver")
            self.assertTrue(fin)
            self.assertIn(sid, session._c2u)
            self.assertIn(sid, session.server_protocol._local_stream_sessions)
            client._quic.send_stream_data(sid, b"\xfeclient", end_stream=True)
            client.transmit()
            reply, fin = await asyncio.wait_for(self.targets[0].stream_replies.get(), 2)
            self.assertEqual(reply, b"\xfeclient")
            self.assertTrue(fin)
            self.assertEqual(session._c2u, {})
            self.assertEqual(session.server_protocol._local_stream_sessions, {})

    async def test_manual_intercept_preserves_stream_order_and_dropped_fin(self):
        api.manual_intercept.update(enabled=True, directions=["incoming"])
        async def pending():
            for _ in range(100):
                if api.pending_intercepts:
                    return next(iter(api.pending_intercepts.values()))
                await asyncio.sleep(0.01)
            self.fail("Intercept did not arrive")
        async with self.client() as client:
            sid = client._http.create_webtransport_stream(client.get_session_id())
            client.register_wt_data_stream(sid)
            client._quic.send_stream_data(sid, b"first")
            client.transmit()
            first = await pending()
            client._quic.send_stream_data(sid, b"second", end_stream=True)
            client.transmit()
            await asyncio.sleep(0.05)
            self.assertEqual(len(api.pending_intercepts), 1)
            api._resolve_pending(first["id"], "forward")
            _, data, fin = await asyncio.wait_for(client.streams.get(), 2)
            self.assertEqual(data, b"first")
            self.assertFalse(fin)
            second = await pending()
            self.assertEqual(second["payload"], "second")
            api._resolve_pending(second["id"], "drop")
            _, data, fin = await asyncio.wait_for(client.streams.get(), 2)
            self.assertEqual(data, b"")
            self.assertTrue(fin)
            session = next(iter(proxy.LIVE_SESSIONS))
            self.assertEqual(session._stream_sid, {})
            self.assertEqual(session._stream_locks, {})
            self.assertEqual(session._stream_fin, {})

    async def test_client_fin_keeps_pairing_until_held_reply_fin_is_forwarded(self):
        api.manual_intercept.update(enabled=True, directions=["outgoing"])
        async with self.client() as client:
            session = next(iter(proxy.LIVE_SESSIONS))
            sid = client._http.create_webtransport_stream(client.get_session_id())
            client.register_wt_data_stream(sid)
            client._quic.send_stream_data(sid, b"held-reply", end_stream=True)
            client.transmit()
            for _ in range(100):
                if api.pending_intercepts:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(api.pending_intercepts)
            item = next(iter(api.pending_intercepts.values()))
            self.assertEqual(session._stream_fin[sid], {"incoming"})
            up_id = session._c2u[sid]
            self.assertIn(up_id, session.upstream._wt_data_streams)
            api._resolve_pending(item["id"], "forward")
            _, data, fin = await asyncio.wait_for(client.streams.get(), 2)
            self.assertEqual(data, b"held-reply")
            self.assertTrue(fin)
            self.assertEqual(session._stream_sid, {})
            self.assertEqual(session._stream_locks, {})
            self.assertEqual(session._stream_fin, {})
            self.assertEqual(session.upstream._wt_data_streams, set())

    async def test_wrong_pin_rejected_before_application_headers(self):
        api.target_config["certHash"] = base64.b64encode(b"x" * 32).decode()
        async with self.client() as client:
            self.assertFalse(client.accepted)
            self.assertEqual(dict(client.response_headers)[b":status"], b"502")
            self.assertTrue(self.requests.empty())

    async def test_untrusted_certificate_rejected_without_pin(self):
        api.target_config["certHash"] = ""
        async with self.client() as client:
            self.assertFalse(client.accepted)
            self.assertEqual(dict(client.response_headers)[b":status"], b"502")
            self.assertTrue(self.requests.empty())

    async def test_ca_mode_accepts_trusted_certificate(self):
        api.target_config["certHash"] = ""
        def trusted_config(**kwargs):
            self.assertEqual(kwargs["verify_mode"], ssl.CERT_REQUIRED)
            config = QuicConfiguration(**kwargs)
            config.load_verify_locations(cafile=str(self.cert_path))
            return config
        with patch.object(proxy, "QuicConfiguration", trusted_config):
            async with self.client() as client:
                self.assertTrue(client.accepted)

    async def test_ca_mode_rejects_hostname_mismatch(self):
        api.target_config["certHash"] = ""
        def trusted_wrong_hostname(**kwargs):
            kwargs["server_name"] = "wrong.example"
            config = QuicConfiguration(**kwargs)
            config.load_verify_locations(cafile=str(self.cert_path))
            return config
        with patch.object(proxy, "QuicConfiguration", trusted_wrong_hostname):
            async with self.client() as client:
                self.assertFalse(client.accepted)
                self.assertEqual(dict(client.response_headers)[b":status"], b"502")
                self.assertTrue(self.requests.empty())

    async def test_matching_pin_rejects_expired_certificate(self):
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(self.cert.subject).issuer_name(self.cert.issuer)
                .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=2)).not_valid_after(now - timedelta(days=1))
                .sign(self.key, hashes.SHA256()))
        upstream = SimpleNamespace(_quic=SimpleNamespace(tls=SimpleNamespace(_peer_certificate=cert)))
        pin = base64.b64encode(cert.fingerprint(hashes.SHA256())).decode()
        with self.assertRaisesRegex(ssl.SSLCertVerificationError, "not currently valid"):
            proxy.verify_upstream_pin(upstream, pin)

    async def test_binary_intercept_forward_edit_drop(self):
        api.manual_intercept.update(enabled=True, directions=["incoming"])
        async with self.client() as client:
            data = b"\xff\x00\xfe"
            for replacement in (None, base64.b64encode(b"\x80edited").decode()):
                client.send_datagram(data)
                for _ in range(100):
                    if api.pending_intercepts:
                        break
                    await asyncio.sleep(0.01)
                item = next(iter(api.pending_intercepts.values()))
                self.assertEqual(item["payloadEncoding"], "base64")
                with self.assertRaises(api.HTTPException):
                    api._resolve_pending(item["id"], "forward", "not base64!")
                api._resolve_pending(item["id"], "forward", replacement)
                expected = data if replacement is None else base64.b64decode(replacement)
                self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), expected)
            client.send_datagram(data)
            for _ in range(100):
                if api.pending_intercepts:
                    break
                await asyncio.sleep(0.01)
            item = next(iter(api.pending_intercepts.values()))
            api._resolve_pending(item["id"], "drop")
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(client.datagrams.get(), 0.15)

    async def test_json_tamper_does_not_rewrite_fragments_or_binary(self):
        api.tamper_rule.update(enabled=True, field="score", value="99", matchField="")
        async with self.client() as client:
            for data, expected in ((b'{"score": 1}', b'{"score": 99}'),
                                   (b'{"score": 1,', b'{"score": 1,'),
                                   (b'\xff{"score": 1}', b'\xff{"score": 1}')):
                client.send_datagram(data)
                self.assertEqual(await asyncio.wait_for(client.datagrams.get(), 2), expected)


if __name__ == "__main__":
    unittest.main()
