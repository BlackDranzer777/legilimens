"""
WebTransport MITM proxy on :4433.

Accepts incoming WebTransport sessions, connects upstream to the target,
and bridges datagrams and streams in both directions while applying
tamper rules and logging every packet to the WebSocket broadcaster.

captureMode:
  'capturing' → packets logged and forwarded (optionally tampered)
  'paused'    → packets DROPPED, session stays alive (no reconnect needed)
"""

import asyncio
import base64
import json
import re
import ssl
import time
import uuid
from pathlib import Path
from typing import Optional

from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import (
    DatagramReceived,
    HeadersReceived,
    WebTransportStreamDataReceived,
)
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, QuicEvent, StreamDataReceived

import api
from logger import broadcast_async, log_error, log_info

CERTS_DIR = Path(__file__).parent / "certs"

SUSPICIOUS_KEYWORDS = [
    "session_token", "password", "secret", "api_key",
    "token", "auth", "bearer", "credential", "private_key", "access_token",
]


# ---------- payload helpers ----------

def _coerce_value(v: str):
    """Convert string value to int/float/bool/str for JSON injection."""
    if re.match(r'^-?\d+(\.\d+)?$', v):
        return float(v) if '.' in v else int(v)
    if v == 'true':
        return True
    if v == 'false':
        return False
    return v


def _apply_tamper(node, rule: dict, coerced) -> bool:
    changed = False
    if isinstance(node, list):
        for item in node:
            if _apply_tamper(item, rule, coerced):
                changed = True
    elif isinstance(node, dict):
        condition = (
            not rule["matchField"]
            or str(node.get(rule["matchField"], "")) == str(rule["matchValue"])
        )
        if condition and rule["field"] in node:
            node[rule["field"]] = coerced
            changed = True
        for key in list(node.keys()):
            if isinstance(node[key], (dict, list)):
                if _apply_tamper(node[key], rule, coerced):
                    changed = True
    return changed


def tamper_payload(payload: str) -> tuple[bool, str]:
    rule = api.tamper_rule
    if not rule["enabled"] or not rule["field"]:
        return False, payload

    try:
        data = json.loads(payload)
        coerced = _coerce_value(rule["value"])
        changed = _apply_tamper(data, rule, coerced)
        if not changed:
            return False, payload
        return True, json.dumps(data)
    except (json.JSONDecodeError, Exception):
        if rule["matchField"]:
            return False, payload
        field_esc = re.escape(rule["field"])
        pattern = rf'("{field_esc}"\s*:\s*)("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)'
        is_numeric = bool(re.match(r'^-?\d+(\.\d+)?$', rule["value"]))
        replacement = rf'\g<1>{rule["value"]}' if is_numeric else rf'\g<1>"{rule["value"]}"'
        modified = re.sub(pattern, replacement, payload)
        return modified != payload, modified


def is_suspicious(payload: str) -> bool:
    lower = payload.lower()
    return any(k in lower for k in SUSPICIOUS_KEYWORDS)


def make_event(
    *,
    direction: str,
    etype: str,
    payload: str,
    raw_size: int,
    latency: int,
    flag: str,
    stream_id: Optional[str] = None,
) -> dict:
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "direction": direction,
        "type": etype,
        "size": raw_size,
        "payload": payload[:300] + "…" if len(payload) > 300 else payload,
        "rawSize": raw_size,
        "latency": latency,
        "flag": flag,
    }
    if stream_id is not None:
        event["streamId"] = stream_id
    return event


# ---------- upstream client protocol ----------

class UpstreamClientProtocol(QuicConnectionProtocol):
    """WebTransport client that connects to the target server."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._session_id: int | None = None
        self._ready_event = asyncio.Event()
        self._datagram_cb = None
        self._stream_cb = None
        self._request_stream_id: int | None = None
        # WT streams WE opened to the target. aioquic does NOT emit a
        # WebTransportStreamDataReceived for replies on locally-created streams, so we
        # capture their replies from the raw QUIC StreamDataReceived event instead.
        self._wt_data_streams: set[int] = set()

    def set_callbacks(self, on_datagram, on_stream_data):
        self._datagram_cb = on_datagram
        self._stream_cb = on_stream_data

    def register_wt_data_stream(self, stream_id: int) -> None:
        self._wt_data_streams.add(stream_id)

    def quic_event_received(self, event: QuicEvent):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        # Replies on streams we created arrive only as raw QUIC stream data (see above).
        if (isinstance(event, StreamDataReceived)
                and event.stream_id in self._wt_data_streams
                and event.data and self._stream_cb):
            asyncio.create_task(self._stream_cb(event.stream_id, event.data, event.end_stream))
        for h3_event in self._http.handle_event(event):
            self._h3_event_received(h3_event)

    def _h3_event_received(self, event):
        if isinstance(event, HeadersReceived):
            if self._request_stream_id is not None and event.stream_id == self._request_stream_id:
                headers = {k: v for k, v in event.headers}
                status = headers.get(b":status", b"")
                if status == b"200":
                    self._session_id = event.stream_id
                    self._ready_event.set()

        elif isinstance(event, DatagramReceived):
            if self._datagram_cb and event.stream_id == self._session_id:
                asyncio.create_task(self._datagram_cb(event.data))

        elif isinstance(event, WebTransportStreamDataReceived):
            if self._stream_cb and event.session_id == self._session_id:
                asyncio.create_task(self._stream_cb(event.stream_id, event.data, event.stream_ended))

    async def connect_webtransport(self, path: str, authority: str) -> bool:
        """Send HTTP/3 CONNECT to establish a WebTransport session."""
        stream_id = self._quic.get_next_available_stream_id(is_unidirectional=False)
        self._request_stream_id = stream_id
        self._http.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"CONNECT"),
                (b":scheme", b"https"),
                (b":authority", authority.encode()),
                (b":path", path.encode()),
                (b":protocol", b"webtransport"),
                (b"origin", f"https://{authority}".encode()),
                (b"sec-webtransport-http3-draft", b"draft02"),
            ],
        )
        self.transmit()
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=10.0)
            return True
        except asyncio.TimeoutError:
            return False

    def send_datagram(self, data: bytes):
        if self._session_id is not None and self._http:
            self._http.send_datagram(stream_id=self._session_id, data=data)
            self.transmit()

    def get_session_id(self) -> int | None:
        return self._session_id

    def close_session(self):
        try:
            self._quic.close()
            self.transmit()
        except Exception:
            pass


# ---------- live-session registry (for teardown + accurate counts) ----------
# Every active ProxySession is tracked here so we can (a) report a correct count to
# the UI and (b) tear sessions down — both when a single client disconnects and on a
# global DISCONNECT. api.active_sessions mirrors this set purely for its len().

LIVE_SESSIONS: set["ProxySession"] = set()


def _register(session: "ProxySession") -> None:
    LIVE_SESSIONS.add(session)
    api.active_sessions.add(session)


def _unregister(session: "ProxySession") -> None:
    LIVE_SESSIONS.discard(session)
    api.active_sessions.discard(session)


# ---------- proxy session (one MITM pairing) ----------

class ProxySession:
    """Bridges one client WebTransport session to the upstream target."""

    def __init__(self, server_protocol: "ProxyServerProtocol", client_session_id: int, session_uuid: str):
        self.server_protocol = server_protocol
        self.client_session_id = client_session_id
        self.session_uuid = session_uuid
        self.upstream: UpstreamClientProtocol | None = None
        # Set once the upstream WT session is established. The client can open a stream
        # the instant it connects — before the proxy has finished dialing upstream — so
        # stream forwarding waits on this to avoid losing the first chunk to that race.
        self._upstream_ready = asyncio.Event()
        # Bidirectional stream pairing between the client side and the upstream side.
        self._c2u: dict[int, int] = {}        # client_stream_id  -> upstream_stream_id
        self._u2c: dict[int, int] = {}        # upstream_stream_id -> client_stream_id
        self._stream_sid: dict[int, str] = {}  # client_stream_id  -> short UI id

    async def start(self, path: str):
        target = api.target_config
        authority = f"{target['host']}:{target['port']}"
        url = f"https://{target['host']}:{target['port']}{path}"

        config = QuicConfiguration(
            alpn_protocols=H3_ALPN,
            is_client=True,
            max_datagram_frame_size=65536,
            verify_mode=ssl.CERT_NONE,
        )

        try:
            async with connect(
                target["host"],
                target["port"],
                configuration=config,
                create_protocol=UpstreamClientProtocol,
                wait_connected=True,
            ) as upstream:
                self.upstream = upstream
                upstream.set_callbacks(
                    on_datagram=self._upstream_datagram_received,
                    on_stream_data=self._upstream_stream_data_received,
                )
                ok = await upstream.connect_webtransport(path, authority)
                if not ok:
                    raise ConnectionError("WebTransport CONNECT timed out")

                self._upstream_ready.set()
                log_info("Connected to upstream target", {
                    "sessionId": self.session_uuid,
                    "target": f"{target['host']}:{target['port']}",
                })
                # Hold until upstream closes
                await upstream.wait_closed()
        except Exception as e:
            log_error("Failed to connect to upstream target", e)
            await broadcast_async(make_event(
                direction="outgoing",
                etype="connection",
                payload=f"Failed to reach upstream {target['host']}:{target['port']}: {e}",
                raw_size=0,
                latency=0,
                flag="suspicious",
            ))
            try:
                self.server_protocol.close_session(self.client_session_id)
            except Exception:
                pass

    async def client_datagram_received(self, data: bytes):
        if api.capture_mode != "capturing":
            return
        t0 = int(time.time() * 1000)
        raw = data.decode(errors="replace")
        tampered, payload = tamper_payload(raw)
        out = payload.encode()
        if self.upstream:
            self.upstream.send_datagram(out)
        latency = int(time.time() * 1000) - t0
        flag = "tampered" if tampered else ("suspicious" if is_suspicious(payload) else "normal")
        await broadcast_async(make_event(
            direction="incoming", etype="datagram",
            payload=payload, raw_size=len(data), latency=latency, flag=flag,
        ))

    async def _upstream_datagram_received(self, data: bytes):
        if api.capture_mode != "capturing":
            return
        t0 = int(time.time() * 1000)
        raw = data.decode(errors="replace")
        tampered, payload = tamper_payload(raw)
        out = payload.encode()
        try:
            self.server_protocol.send_datagram(self.client_session_id, out)
            self.server_protocol.transmit()
        except Exception:
            pass
        latency = int(time.time() * 1000) - t0
        flag = "tampered" if tampered else ("suspicious" if is_suspicious(payload) else "normal")
        await broadcast_async(make_event(
            direction="outgoing", etype="datagram",
            payload=payload, raw_size=len(data), latency=latency, flag=flag,
        ))

    def _sid_for(self, client_stream_id: int) -> tuple[str, bool]:
        """Return (short UI id, is_new) for a client stream, creating it if unseen."""
        sid = self._stream_sid.get(client_stream_id)
        if sid is not None:
            return sid, False
        sid = str(uuid.uuid4())[:8]
        self._stream_sid[client_stream_id] = sid
        return sid, True

    async def client_stream_data_received(self, client_stream_id: int, data: bytes, ended: bool):
        # client → upstream. Each client stream is paired with exactly ONE upstream
        # stream (created on first sight), so multi-chunk streams forward in order.
        if api.capture_mode != "capturing":
            return
        sid, is_new = self._sid_for(client_stream_id)
        if is_new:
            await broadcast_async(make_event(
                direction="incoming", etype="stream",
                payload=f"Stream {sid} opened", raw_size=0, latency=0,
                flag="normal", stream_id=sid,
            ))

        # Wait for the upstream to be ready so the first chunk isn't lost to the race.
        if not self._upstream_ready.is_set():
            try:
                await asyncio.wait_for(self._upstream_ready.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                return

        t0 = int(time.time() * 1000)
        tampered, payload = (False, "")
        if data:
            tampered, payload = tamper_payload(data.decode(errors="replace"))

        up_id = self._c2u.get(client_stream_id)
        if up_id is None and self.upstream and self.upstream.get_session_id() is not None:
            try:
                up_id = self.upstream._http.create_webtransport_stream(self.upstream.get_session_id())
                self._c2u[client_stream_id] = up_id
                self._u2c[up_id] = client_stream_id
                # So the upstream client forwards replies on this stream back to us.
                self.upstream.register_wt_data_stream(up_id)
            except Exception:
                up_id = None
        if up_id is not None:
            try:
                self.upstream._quic.send_stream_data(up_id, payload.encode(), end_stream=ended)
                self.upstream.transmit()
            except Exception:
                pass

        if data:
            latency = int(time.time() * 1000) - t0
            flag = "tampered" if tampered else ("suspicious" if is_suspicious(payload) else "normal")
            await broadcast_async(make_event(
                direction="incoming", etype="stream",
                payload=payload, raw_size=len(data), latency=latency, flag=flag, stream_id=sid,
            ))

    async def _upstream_stream_data_received(self, upstream_stream_id: int, data: bytes, ended: bool):
        # upstream → client. Maps the upstream stream back to its paired client stream
        # and writes the reply on it. If the target opened the stream itself, open a
        # matching client stream so the data still reaches the client.
        if api.capture_mode != "capturing":
            return
        client_stream_id = self._u2c.get(upstream_stream_id)
        if client_stream_id is None:
            try:
                client_stream_id = self.server_protocol._http.create_webtransport_stream(self.client_session_id)
                self._u2c[upstream_stream_id] = client_stream_id
                self._c2u[client_stream_id] = upstream_stream_id
            except Exception:
                return
        sid, is_new = self._sid_for(client_stream_id)
        if is_new:
            await broadcast_async(make_event(
                direction="outgoing", etype="stream",
                payload=f"Stream {sid} opened", raw_size=0, latency=0,
                flag="normal", stream_id=sid,
            ))

        t0 = int(time.time() * 1000)
        tampered, payload = (False, "")
        if data:
            tampered, payload = tamper_payload(data.decode(errors="replace"))

        try:
            self.server_protocol._quic.send_stream_data(client_stream_id, payload.encode(), end_stream=ended)
            self.server_protocol.transmit()
        except Exception:
            pass

        if data:
            latency = int(time.time() * 1000) - t0
            flag = "tampered" if tampered else ("suspicious" if is_suspicious(payload) else "normal")
            await broadcast_async(make_event(
                direction="outgoing", etype="stream",
                payload=payload, raw_size=len(data), latency=latency, flag=flag, stream_id=sid,
            ))

    def close(self):
        if self.upstream:
            self.upstream.close_session()


# ---------- proxy server protocol ----------

class ProxyServerProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._sessions: dict[int, ProxySession] = {}

    def quic_event_received(self, event: QuicEvent):
        # In server mode every QUIC connection shares one UDP transport, so asyncio's
        # connection_lost does NOT fire per connection. ConnectionTerminated is the
        # reliable per-connection close signal — use it to tear sessions down.
        if isinstance(event, ConnectionTerminated):
            self._teardown_all()
            return
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for h3_event in self._http.handle_event(event):
            self._h3_event_received(h3_event)

    def _teardown_all(self):
        for session in list(self._sessions.values()):
            session.close()
            _unregister(session)
        self._sessions.clear()

    def _h3_event_received(self, event):
        if isinstance(event, HeadersReceived):
            headers = {k: v for k, v in event.headers}
            if (headers.get(b":method") == b"CONNECT"
                    and headers.get(b":protocol") == b"webtransport"):
                path = headers.get(b":path", b"/").decode()
                self._accept_session(event.stream_id, path)

        elif isinstance(event, DatagramReceived):
            session = self._sessions.get(event.stream_id)
            if session:
                asyncio.create_task(session.client_datagram_received(event.data))

        elif isinstance(event, WebTransportStreamDataReceived):
            session = self._sessions.get(event.session_id)
            if session:
                # Forward even an empty final chunk so the stream's FIN propagates.
                asyncio.create_task(
                    session.client_stream_data_received(event.stream_id, event.data, event.stream_ended)
                )

    def _accept_session(self, stream_id: int, path: str):
        self._http.send_headers(
            stream_id=stream_id,
            headers=[
                (b":status", b"200"),
                (b"sec-webtransport-http3-draft", b"draft02"),
            ],
        )
        self.transmit()

        session_uuid = str(uuid.uuid4())
        session = ProxySession(self, stream_id, session_uuid)
        self._sessions[stream_id] = session

        log_info("Incoming WebTransport session", {"sessionId": session_uuid, "path": path})
        asyncio.create_task(broadcast_async(make_event(
            direction="incoming", etype="connection",
            payload=f"Session {session_uuid[:8]} connected via {path}",
            raw_size=0, latency=0, flag="normal",
        )))

        _register(session)

        async def run_session():
            try:
                await session.start(path)
            finally:
                # Upstream closed (or connect failed) → drop this session everywhere.
                _unregister(session)
                self._sessions.pop(stream_id, None)

        asyncio.create_task(run_session())

    def send_datagram(self, session_id: int, data: bytes):
        if self._http:
            self._http.send_datagram(stream_id=session_id, data=data)

    def close_session(self, session_id: int):
        session = self._sessions.pop(session_id, None)
        if session:
            session.close()
            _unregister(session)
        try:
            self._quic.close()
            self.transmit()
        except Exception:
            pass

    def connection_lost(self, exc):
        # Belt-and-suspenders: fires if the whole UDP transport dies. Per-connection
        # teardown is normally driven by ConnectionTerminated in quic_event_received.
        self._teardown_all()
        try:
            super().connection_lost(exc)
        except Exception:
            pass


async def disconnect_all():
    """Hard-cut every live session (UI DISCONNECT). Severs both the upstream and the
    client QUIC connection; the client must redial to return."""
    for session in list(LIVE_SESSIONS):
        try:
            session.close()  # close upstream
        except Exception:
            pass
        try:
            session.server_protocol.close_session(session.client_session_id)  # close client
        except Exception:
            pass
        _unregister(session)


async def start_proxy(port: int = 4433):
    certs_dir = Path(__file__).parent / "certs"

    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=False,
        max_datagram_frame_size=65536,
        idle_timeout=30.0,  # abandoned client connections self-clean within 30s
    )
    config.load_cert_chain(str(certs_dir / "cert.pem"), str(certs_dir / "key.pem"))

    api.register_disconnect_fn(disconnect_all)

    server = await serve(
        "0.0.0.0",
        port,
        configuration=config,
        create_protocol=ProxyServerProtocol,
    )
    log_info("MITM proxy started", {"port": port})
    return server
