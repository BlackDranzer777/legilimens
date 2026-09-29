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
import hmac
import json
import re
import ssl
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from cryptography.hazmat.primitives import hashes

from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import (
    DatagramReceived,
    HeadersReceived,
    WebTransportStreamDataReceived,
)
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, QuicEvent, StopSendingReceived, StreamDataReceived, StreamReset

import api
from logger import broadcast, broadcast_async, log_error, log_info
from udp_fix import harden_udp_server
from paths import certs_dir as get_certs_dir

CERTS_DIR = get_certs_dir()
MAX_SESSIONS = 32
MAX_SESSION_TASKS = 128
MAX_SESSION_BYTES = 2 * 1024 * 1024
MAX_SESSION_STREAMS = 128
MAX_QUIC_BUFFER_BYTES = 4 * 1024 * 1024
MAX_QUIC_STREAMS = 272


def transport_over_budget(quic, extra=0):
    # aioquic has no public pending-byte accessor. Fail closed on incompatible upgrades.
    try:
        streams = quic._streams
        datagrams = quic._datagrams_pending
        # aioquic 1.3.0 leaves send-only receivers unfinished. Mark only the
        # impossible receive half complete; aioquic still waits for sender ACKs
        # and performs its own stream retirement. Never discard send buffers.
        is_client = getattr(quic, "_is_client", None)
        if isinstance(is_client, bool):
            local_initiator = 0 if is_client else 1
            for stream_id, stream in streams.items():
                if stream_id & 2 and stream_id & 1 == local_initiator:
                    stream.receiver.is_finished = True
        return (len(streams) > MAX_QUIC_STREAMS or len(datagrams) > MAX_SESSION_TASKS
                or extra + sum(len(s.sender._buffer) + len(s.receiver._buffer) for s in streams.values())
                + sum(len(d) for d in datagrams) > MAX_QUIC_BUFFER_BYTES)
    except (AttributeError, TypeError):
        return True


def require_transport_capacity(quic, extra):
    if transport_over_budget(quic, extra):
        try:
            streams = list(quic._streams.values())
            buffered = sorted(streams, key=lambda s: len(s.sender._buffer) + len(s.receiver._buffer), reverse=True)
            log_info("QUIC capacity rejected", {
                "extraBytes": extra, "transportStreams": len(streams),
                "datagrams": len(quic._datagrams_pending),
                "sendBytes": sum(len(s.sender._buffer) for s in streams),
                "receiveBytes": sum(len(s.receiver._buffer) for s in streams),
                "largestStreams": [{"id": s.stream_id, "sendBytes": len(s.sender._buffer),
                                    "receiveBytes": len(s.receiver._buffer),
                                    "sendOffset": s.sender.highest_offset,
                                    "peerLimit": s.max_stream_data_remote}
                                   for s in buffered[:4]],
            })
        except (AttributeError, TypeError):
            log_info("QUIC capacity inspection unavailable")
        raise BufferError("QUIC transport buffer limit exceeded")

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
    except (ValueError, TypeError, RecursionError):
        # A stream chunk is not a message boundary. Never rewrite fragments
        # using a regex; callers need application framing for split JSON.
        return False, payload


def is_suspicious(payload: str) -> bool:
    lower = payload.lower()
    return any(k in lower for k in SUSPICIOUS_KEYWORDS)


def inspect_payload(data: bytes) -> tuple[bool, str, str]:
    """Keep wire bytes separate from the editable display representation."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, base64.b64encode(data).decode("ascii"), "base64"
    changed, text = tamper_payload(text)
    return changed, text, "utf8"


def payload_bytes(original: bytes, payload: str, encoding: str, changed: bool) -> bytes:
    if not changed:
        return original
    return base64.b64decode(payload, validate=True) if encoding == "base64" else payload.encode("utf-8")


def forwarded_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    excluded = {b"host", b"connection", b"keep-alive", b"proxy-authenticate",
                b"proxy-authorization", b"transfer-encoding", b"upgrade"}
    return [(k, v) for k, v in headers if not k.startswith(b":") and k not in excluded]


def verify_upstream_pin(upstream, pin: str) -> None:
    # aioquic exposes the peer certificate through its TLS context, not a public
    # protocol accessor. Fail closed if that interface changes.
    cert = getattr(getattr(upstream._quic, "tls", None), "_peer_certificate", None)
    if cert is None:
        raise ssl.SSLCertVerificationError("Upstream did not provide a certificate")
    expected = base64.b64decode(pin, validate=True)
    if len(expected) != 32 or not hmac.compare_digest(cert.fingerprint(hashes.SHA256()), expected):
        raise ssl.SSLCertVerificationError("Upstream certificate pin mismatch")
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise ssl.SSLCertVerificationError("Upstream certificate is not currently valid")


def make_event(
    *,
    direction: str,
    etype: str,
    payload: str,
    raw_size: int,
    latency: int,
    flag: str,
    stream_id: Optional[str] = None,
    payload_encoding: str = "utf8",
    session_id: str | None = None,
    replayable: bool | None = None,
    target: str | None = None,
) -> dict:
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "direction": direction,
        "type": etype,
        "size": raw_size,
        "payload": payload,
        "payloadPreview": payload[:300] + "…" if len(payload) > 300 else payload,
        "sessionId": session_id,
        "target": target,
        "replayable": (etype == "datagram" and payload_encoding == "utf8" and session_id is not None)
                      if replayable is None else replayable,
        "rawSize": raw_size,
        "latency": latency,
        "flag": flag,
        "payloadEncoding": payload_encoding,
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
        self.connect_error: str | None = None
        self.response_headers: list[tuple[bytes, bytes]] = []
        # WT streams WE opened to the target. aioquic does NOT emit a
        # WebTransportStreamDataReceived for replies on locally-created streams, so we
        # capture their replies from the raw QUIC StreamDataReceived event instead.
        self._wt_data_streams: set[int] = set()
        self._callback_tasks = set()
        self._callback_bytes = 0

    def _dispatch(self, callback, *args):
        result = callback(*args)
        if not asyncio.iscoroutine(result):
            return
        size = sum(len(arg) for arg in args if isinstance(arg, bytes))
        if len(self._callback_tasks) >= MAX_SESSION_TASKS or self._callback_bytes + size > MAX_SESSION_BYTES:
            result.close()
            self.close_session()
            return
        self._callback_bytes += size
        task = asyncio.create_task(result)
        self._callback_tasks.add(task)
        def finished(done):
            self._callback_tasks.discard(done)
            self._callback_bytes -= size
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)

    def set_callbacks(self, on_datagram, on_stream_data):
        self._datagram_cb = on_datagram
        self._stream_cb = on_stream_data

    def register_wt_data_stream(self, stream_id: int) -> None:
        self._wt_data_streams.add(stream_id)

    def quic_event_received(self, event: QuicEvent):
        if transport_over_budget(self._quic):
            self.close(error_code=0x100, reason_phrase="Transport resource limit")
            return
        if isinstance(event, ConnectionTerminated):
            if self._session_id is None:
                self.connect_error = "Upstream closed before accepting WebTransport CONNECT"
                self._ready_event.set()
            for task in self._callback_tasks:
                task.cancel()
            return
        if (isinstance(event, (StreamReset, StopSendingReceived))
                and event.stream_id == self._request_stream_id and self._session_id is None):
            self.connect_error = "Upstream aborted WebTransport CONNECT"
            self._ready_event.set()
            return
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        # Replies on streams we created arrive only as raw QUIC stream data (see above).
        if (isinstance(event, StreamDataReceived)
                and event.stream_id in self._wt_data_streams
                and self._stream_cb):
            self._dispatch(self._stream_cb, event.stream_id, event.data, event.end_stream)
            return  # The reply is raw stream data, not another HTTP/3 frame.
        for h3_event in self._http.handle_event(event):
            self._h3_event_received(h3_event)

    def _h3_event_received(self, event):
        if isinstance(event, HeadersReceived):
            if self._request_stream_id is not None and event.stream_id == self._request_stream_id:
                headers = {k: v for k, v in event.headers}
                status = headers.get(b":status", b"")
                if status.startswith(b"1"):
                    return
                self.response_headers = event.headers
                if status == b"200":
                    self._session_id = event.stream_id
                self._ready_event.set()

        elif isinstance(event, DatagramReceived):
            if self._datagram_cb and event.stream_id == self._session_id:
                self._dispatch(self._datagram_cb, event.data)

        elif isinstance(event, WebTransportStreamDataReceived):
            if self._stream_cb and event.session_id == self._session_id:
                self._dispatch(self._stream_cb, event.stream_id, event.data, event.stream_ended)

    async def connect_webtransport(self, path: str, authority: str,
                                   headers: list[tuple[bytes, bytes]] | None = None) -> bool:
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
            ] + forwarded_headers(headers or []),
        )
        self.transmit()
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=10.0)
            return self._session_id is not None
        except asyncio.TimeoutError:
            return False

    def send_datagram(self, data: bytes):
        if self._session_id is not None and self._http:
            require_transport_capacity(self._quic, len(data) + 16)
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
_budget_sessions: set["ProxySession"] = set()


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
        self._stream_locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._stream_fin: dict[int, set[str]] = {}
        self._accepted = False
        self._closed = False
        self.target_label = ""
        self._tasks = set()
        self._task_bytes = 0
        self._lifecycle_task = None

    def submit(self, callback, *args, size=0):
        if self._closed:
            return
        if len(self._tasks) >= MAX_SESSION_TASKS or self._task_bytes + size > MAX_SESSION_BYTES:
            self.overloaded("forwarding work")
            return
        self._task_bytes += size
        task = asyncio.create_task(callback(*args))
        self._tasks.add(task)
        def finished(done):
            self._tasks.discard(done)
            self._task_bytes -= size
            if not done.cancelled() and done.exception() is not None:
                self.overloaded("forwarding failure")
            self.release_budget()
        task.add_done_callback(finished)

    def release_budget(self):
        if self._lifecycle_task and self._lifecycle_task.done() and not self._tasks:
            _budget_sessions.discard(self)

    def overloaded(self, reason):
        if self._closed:
            return
        log_info("Proxy session resource limit", {
            "sessionId": self.session_uuid, "reason": reason,
            "activeStreams": len(self._stream_sid), "tasks": len(self._tasks),
        })
        broadcast({"type": "resource", "message": f"Session {self.session_uuid[:8]} closed: {reason} limit or failure."})
        self.close()
        self.server_protocol.close_session(self.client_session_id)

    def admit_stream(self, direction, stream_id):
        key = (direction, stream_id)
        new_stream = (stream_id not in self._stream_sid if direction == "incoming" else stream_id not in self._u2c)
        if new_stream and len(self._stream_sid) >= MAX_SESSION_STREAMS:
            self.overloaded("active streams")
            return False
        if key not in self._stream_locks and len(self._stream_locks) >= MAX_SESSION_STREAMS * 2:
            self.overloaded("active streams")
            return False
        return not self._closed

    @property
    def ready_for_replay(self):
        return self._accepted and not self._closed and self.upstream is not None

    async def start(self, path: str, headers: list[tuple[bytes, bytes]] | None = None):
        target = api.target_config
        host = target['host']
        authority = f"[{host}]:{target['port']}" if ":" in host else f"{host}:{target['port']}"
        self.target_label = authority
        pin = target.get("certHash", "")

        config = QuicConfiguration(
            alpn_protocols=H3_ALPN,
            is_client=True,
            max_datagram_frame_size=65536,
            verify_mode=ssl.CERT_NONE if pin else ssl.CERT_REQUIRED,
            server_name=host,
            idle_timeout=10,
        )

        try:
            async with connect(
                target["host"],
                target["port"],
                configuration=config,
                create_protocol=UpstreamClientProtocol,
                wait_connected=True,
            ) as upstream:
                if pin:
                    verify_upstream_pin(upstream, pin)
                self.upstream = upstream
                upstream.set_callbacks(
                    on_datagram=lambda data: self.submit(self._upstream_datagram_received, data, size=len(data)),
                    on_stream_data=lambda sid, data, ended: self.submit(self._upstream_stream_data_received, sid, data, ended, size=len(data)),
                )
                ok = await upstream.connect_webtransport(path, authority, headers)
                if not ok:
                    if upstream.response_headers:
                        status = dict(upstream.response_headers).get(b":status", b"502")
                        self.server_protocol.send_session_response(
                            self.client_session_id, status, forwarded_headers(upstream.response_headers))
                        return
                    raise ConnectionError(upstream.connect_error or "WebTransport CONNECT timed out")

                self.server_protocol.send_session_response(
                    self.client_session_id, b"200", forwarded_headers(upstream.response_headers))
                self._accepted = True
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
                session_id=self.session_uuid,
                target=self.target_label,
                direction="outgoing",
                etype="connection",
                payload=f"Failed to reach upstream {target['host']}:{target['port']}: {e}",
                raw_size=0,
                latency=0,
                flag="suspicious",
            ))
            try:
                if self._accepted:
                    self.server_protocol.close_session(self.client_session_id)
                else:
                    self.server_protocol.send_session_response(self.client_session_id, b"502")
            except Exception:
                pass

    async def client_datagram_received(self, data: bytes):
        if api.capture_mode != "capturing":
            return
        # Wait for the upstream to be ready so a datagram the client fires the instant it
        # connects isn't lost to the dial race (mirrors the stream path below).
        if not self._upstream_ready.is_set():
            try:
                await asyncio.wait_for(self._upstream_ready.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                return
        t0 = int(time.time() * 1000)
        tampered, payload, encoding = inspect_payload(data)
        decision = await self._manual_decision(
            direction="incoming",
            message_type="datagram",
            payload=payload,
            raw_size=len(data),
            payload_encoding=encoding,
        )
        if decision["action"] == "drop":
            await self._log_intercept_drop(
                direction="incoming",
                message_type="datagram",
                raw_size=len(data),
                intercept_id=decision.get("interceptId"),
            )
            return
        manually_changed = decision["payload"] != payload
        payload = decision["payload"]
        out = payload_bytes(data, payload, encoding, tampered or manually_changed)
        if self.upstream:
            self.upstream.send_datagram(out)
        latency = int(time.time() * 1000) - t0
        flag = "tampered" if (tampered or manually_changed) else ("suspicious" if is_suspicious(payload) else "normal")
        await broadcast_async(make_event(
            session_id=self.session_uuid,
            target=self.target_label,
            direction="incoming", etype="datagram",
            payload=payload, raw_size=len(data), latency=latency, flag=flag,
            payload_encoding=encoding,
        ))

    async def _upstream_datagram_received(self, data: bytes):
        if api.capture_mode != "capturing":
            return
        t0 = int(time.time() * 1000)
        tampered, payload, encoding = inspect_payload(data)
        decision = await self._manual_decision(
            direction="outgoing",
            message_type="datagram",
            payload=payload,
            raw_size=len(data),
            payload_encoding=encoding,
        )
        if decision["action"] == "drop":
            await self._log_intercept_drop(
                direction="outgoing",
                message_type="datagram",
                raw_size=len(data),
                intercept_id=decision.get("interceptId"),
            )
            return
        manually_changed = decision["payload"] != payload
        payload = decision["payload"]
        out = payload_bytes(data, payload, encoding, tampered or manually_changed)
        try:
            self.server_protocol.send_datagram(self.client_session_id, out)
            self.server_protocol.transmit()
        except BufferError:
            self.overloaded("client transport buffer")
            return
        except Exception:
            pass
        latency = int(time.time() * 1000) - t0
        flag = "tampered" if (tampered or manually_changed) else ("suspicious" if is_suspicious(payload) else "normal")
        await broadcast_async(make_event(
            session_id=self.session_uuid,
            target=self.target_label,
            direction="outgoing", etype="datagram",
            payload=payload, raw_size=len(data), latency=latency, flag=flag,
            payload_encoding=encoding,
        ))

    def _sid_for(self, client_stream_id: int) -> tuple[str, bool]:
        """Return (short UI id, is_new) for a client stream, creating it if unseen."""
        sid = self._stream_sid.get(client_stream_id)
        if sid is not None:
            return sid, False
        sid = str(uuid.uuid4())[:8]
        self._stream_sid[client_stream_id] = sid
        return sid, True

    async def _manual_decision(
        self,
        *,
        direction: str,
        message_type: str,
        payload: str,
        raw_size: int,
        stream_id: str | None = None,
        payload_encoding: str = "utf8",
    ) -> dict:
        return await api.await_manual_intercept(
            session_id=self.session_uuid,
            direction=direction,
            message_type=message_type,
            payload=payload,
            raw_size=raw_size,
            stream_id=stream_id,
            payload_encoding=payload_encoding,
        )

    async def _log_intercept_drop(
        self,
        *,
        direction: str,
        message_type: str,
        raw_size: int,
        intercept_id: str | None,
        stream_id: str | None = None,
    ) -> None:
        await broadcast_async(make_event(
            session_id=self.session_uuid, replayable=False,
            target=self.target_label,
            direction=direction,
            etype=message_type,
            payload=f"Intercept dropped {direction} {message_type}"
                    + (f" ({intercept_id[:8]})" if intercept_id else ""),
            raw_size=raw_size,
            latency=0,
            flag="normal",
            stream_id=stream_id,
        ))

    async def client_stream_data_received(self, client_stream_id: int, data: bytes, ended: bool):
        if not self.admit_stream("incoming", client_stream_id):
            return
        async with self._stream_locks.setdefault(("incoming", client_stream_id), asyncio.Lock()):
            forwarded = await self._forward_client_stream(client_stream_id, data, ended)
        if ended and forwarded:
            self._stream_finished(client_stream_id, "incoming")

    def _stream_finished(self, client_stream_id: int, direction: str):
        # A bidi FIN closes only one half. Keep its pairing (and raw-reply routing)
        # until both forwarding queues have finished, including capture/intercept work.
        finished = self._stream_fin.setdefault(client_stream_id, set())
        finished.add(direction)
        if not (client_stream_id & 2) and len(finished) < 2:
            return
        upstream_stream_id = self._c2u.pop(client_stream_id, None)
        self._stream_fin.pop(client_stream_id, None)
        self._stream_sid.pop(client_stream_id, None)
        self._stream_locks.pop(("incoming", client_stream_id), None)
        self.server_protocol._local_stream_sessions.pop(client_stream_id, None)
        if upstream_stream_id is not None:
            self._u2c.pop(upstream_stream_id, None)
            self._stream_locks.pop(("outgoing", upstream_stream_id), None)
            if self.upstream:
                self.upstream._wt_data_streams.discard(upstream_stream_id)

    async def _forward_client_stream(self, client_stream_id: int, data: bytes, ended: bool):
        # client → upstream. Each client stream is paired with exactly ONE upstream
        # stream (created on first sight), so multi-chunk streams forward in order.
        if api.capture_mode != "capturing":
            return
        sid, is_new = self._sid_for(client_stream_id)
        if is_new:
            await broadcast_async(make_event(
                session_id=self.session_uuid,
                target=self.target_label,
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
        tampered, payload, encoding = (False, "", "utf8")
        if data:
            tampered, payload, encoding = inspect_payload(data)
            decision = await self._manual_decision(
                direction="incoming",
                message_type="stream",
                payload=payload,
                raw_size=len(data),
                stream_id=sid,
                payload_encoding=encoding,
            )
            if decision["action"] == "drop":
                await self._log_intercept_drop(
                    direction="incoming",
                    message_type="stream",
                    raw_size=len(data),
                    intercept_id=decision.get("interceptId"),
                    stream_id=sid,
                )
                if ended:
                    return await self._forward_client_stream(client_stream_id, b"", True)
                return
            manually_changed = decision["payload"] != payload
            payload = decision["payload"]
        else:
            manually_changed = False

        up_id = self._c2u.get(client_stream_id)
        if up_id is None and self.upstream and self.upstream.get_session_id() is not None:
            try:
                up_id = self.upstream._http.create_webtransport_stream(
                    self.upstream.get_session_id(), is_unidirectional=bool(client_stream_id & 2))
                self._c2u[client_stream_id] = up_id
                self._u2c[up_id] = client_stream_id
                # So the upstream client forwards replies on this stream back to us.
                if not (client_stream_id & 2):
                    self.upstream.register_wt_data_stream(up_id)
            except Exception:
                up_id = None
        if up_id is not None:
            try:
                require_transport_capacity(self.upstream._quic, len(data) + len(payload.encode("utf-8")))
                self.upstream._quic.send_stream_data(
                    up_id, payload_bytes(data, payload, encoding, tampered or manually_changed), end_stream=ended)
                self.upstream.transmit()
            except BufferError:
                self.overloaded("upstream transport buffer")
                return
            except Exception:
                return
        else:
            return

        if data:
            latency = int(time.time() * 1000) - t0
            flag = "tampered" if (tampered or manually_changed) else ("suspicious" if is_suspicious(payload) else "normal")
            await broadcast_async(make_event(
                session_id=self.session_uuid,
                target=self.target_label,
                direction="incoming", etype="stream",
                payload=payload, raw_size=len(data), latency=latency, flag=flag, stream_id=sid,
                payload_encoding=encoding,
            ))
        return True

    async def _upstream_stream_data_received(self, upstream_stream_id: int, data: bytes, ended: bool):
        if not self.admit_stream("outgoing", upstream_stream_id):
            return
        async with self._stream_locks.setdefault(("outgoing", upstream_stream_id), asyncio.Lock()):
            forwarded = await self._forward_upstream_stream(upstream_stream_id, data, ended)
        if ended and forwarded:
            self._stream_finished(self._u2c[upstream_stream_id], "outgoing")

    async def _forward_upstream_stream(self, upstream_stream_id: int, data: bytes, ended: bool):
        # upstream → client. Maps the upstream stream back to its paired client stream
        # and writes the reply on it. If the target opened the stream itself, open a
        # matching client stream so the data still reaches the client.
        if api.capture_mode != "capturing":
            return
        client_stream_id = self._u2c.get(upstream_stream_id)
        if client_stream_id is None:
            try:
                client_stream_id = self.server_protocol._http.create_webtransport_stream(
                    self.client_session_id, is_unidirectional=bool(upstream_stream_id & 2))
                self._u2c[upstream_stream_id] = client_stream_id
                self._c2u[client_stream_id] = upstream_stream_id
                if not (client_stream_id & 2):
                    self.server_protocol._local_stream_sessions[client_stream_id] = self
            except Exception:
                return
        sid, is_new = self._sid_for(client_stream_id)
        if is_new:
            await broadcast_async(make_event(
                session_id=self.session_uuid,
                target=self.target_label,
                direction="outgoing", etype="stream",
                payload=f"Stream {sid} opened", raw_size=0, latency=0,
                flag="normal", stream_id=sid,
            ))

        t0 = int(time.time() * 1000)
        tampered, payload, encoding = (False, "", "utf8")
        if data:
            tampered, payload, encoding = inspect_payload(data)
            decision = await self._manual_decision(
                direction="outgoing",
                message_type="stream",
                payload=payload,
                raw_size=len(data),
                stream_id=sid,
                payload_encoding=encoding,
            )
            if decision["action"] == "drop":
                await self._log_intercept_drop(
                    direction="outgoing",
                    message_type="stream",
                    raw_size=len(data),
                    intercept_id=decision.get("interceptId"),
                    stream_id=sid,
                )
                if ended:
                    return await self._forward_upstream_stream(upstream_stream_id, b"", True)
                return
            manually_changed = decision["payload"] != payload
            payload = decision["payload"]
        else:
            manually_changed = False

        try:
            require_transport_capacity(self.server_protocol._quic, len(data) + len(payload.encode("utf-8")))
            self.server_protocol._quic.send_stream_data(
                client_stream_id, payload_bytes(data, payload, encoding, tampered or manually_changed), end_stream=ended)
            self.server_protocol.transmit()
        except BufferError:
            self.overloaded("client transport buffer")
            return
        except Exception:
            return

        if data:
            latency = int(time.time() * 1000) - t0
            flag = "tampered" if (tampered or manually_changed) else ("suspicious" if is_suspicious(payload) else "normal")
            await broadcast_async(make_event(
                session_id=self.session_uuid,
                target=self.target_label,
                direction="outgoing", etype="stream",
                payload=payload, raw_size=len(data), latency=latency, flag=flag, stream_id=sid,
                payload_encoding=encoding,
            ))
        return True

    def close(self):
        if self._closed:
            return
        self._closed = True
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        if self.upstream is None and self._lifecycle_task and self._lifecycle_task is not current:
            self._lifecycle_task.cancel()
        if self.upstream:
            self.upstream.close_session()


# ---------- proxy server protocol ----------

class ProxyServerProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._sessions: dict[int, ProxySession] = {}
        self._local_stream_sessions: dict[int, ProxySession] = {}

    def quic_event_received(self, event: QuicEvent):
        if transport_over_budget(self._quic):
            broadcast({"type": "resource", "message": "Client connection closed: QUIC transport resource limit."})
            self._teardown_all()
            self.close(error_code=0x100, reason_phrase="Transport resource limit")
            return
        # In server mode every QUIC connection shares one UDP transport, so asyncio's
        # connection_lost does NOT fire per connection. ConnectionTerminated is the
        # reliable per-connection close signal — use it to tear sessions down.
        if isinstance(event, ConnectionTerminated):
            self._teardown_all()
            return
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        if isinstance(event, StreamDataReceived):
            session = self._local_stream_sessions.get(event.stream_id)
            if session:
                session.submit(session.client_stream_data_received,
                    event.stream_id, event.data, event.end_stream, size=len(event.data))
                return
        for h3_event in self._http.handle_event(event):
            self._h3_event_received(h3_event)

    def _teardown_all(self):
        for session in list(self._sessions.values()):
            session.close()
            _unregister(session)
        self._sessions.clear()
        self._local_stream_sessions.clear()

    def _h3_event_received(self, event):
        if isinstance(event, HeadersReceived):
            headers = {k: v for k, v in event.headers}
            if (headers.get(b":method") == b"CONNECT"
                    and headers.get(b":protocol") == b"webtransport"):
                path = headers.get(b":path", b"/").decode()
                self._accept_session(event.stream_id, path, event.headers)

        elif isinstance(event, DatagramReceived):
            session = self._sessions.get(event.stream_id)
            if session:
                session.submit(session.client_datagram_received, event.data, size=len(event.data))

        elif isinstance(event, WebTransportStreamDataReceived):
            session = self._sessions.get(event.session_id)
            if session:
                # Forward even an empty final chunk so the stream's FIN propagates.
                session.submit(session.client_stream_data_received,
                    event.stream_id, event.data, event.stream_ended, size=len(event.data))

    def send_session_response(self, stream_id: int, status: bytes,
                              headers: list[tuple[bytes, bytes]] | None = None):
        # WebTransport is negotiated via H3 SETTINGS (ENABLE_WEBTRANSPORT / H3_DATAGRAM /
        # ENABLE_CONNECT_PROTOCOL), which aioquic emits automatically from
        # enable_webtransport=True. A bare 200 is all a modern peer (Chrome, aioquic) needs.
        self._http.send_headers(
            stream_id=stream_id,
            headers=[(b":status", status)] + (headers or []),
            end_stream=status != b"200",
        )
        self.transmit()

    def _accept_session(self, stream_id: int, path: str, headers: list[tuple[bytes, bytes]]):
        if sum(len(k) + len(v) for k, v in headers) > 16384:
            self.send_session_response(stream_id, b"431")
            return
        if stream_id in self._sessions:
            return
        if len(_budget_sessions) >= MAX_SESSIONS:
            self.send_session_response(stream_id, b"503")
            return
        session_uuid = str(uuid.uuid4())
        session = ProxySession(self, stream_id, session_uuid)
        self._sessions[stream_id] = session

        log_info("Incoming WebTransport session", {"sessionId": session_uuid, "path": path})
        broadcast(make_event(
            session_id=session_uuid,
            direction="incoming", etype="connection",
            payload=f"Session {session_uuid[:8]} connected via {path}",
            raw_size=0, latency=0, flag="normal",
        ))

        _register(session)
        _budget_sessions.add(session)

        async def run_session():
            try:
                await session.start(path, headers)
            finally:
                session.close()
                # Upstream closed (or connect failed) → drop this session everywhere.
                _unregister(session)
                self._sessions.pop(stream_id, None)
                self._local_stream_sessions = {sid: s for sid, s in self._local_stream_sessions.items() if s is not session}

        session._lifecycle_task = asyncio.create_task(run_session())
        def finished(_):
            session.close()
            _unregister(session)
            self._sessions.pop(stream_id, None)
            session.release_budget()
        session._lifecycle_task.add_done_callback(finished)

    def send_datagram(self, session_id: int, data: bytes):
        if self._http:
            require_transport_capacity(self._quic, len(data) + 16)
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


async def replay_message(direction: str, message_type: str, payload: str, session_id: str) -> dict:
    """Repeater: inject an (edited) message into the live proxied session.

    direction "incoming" → send to the server as the client;
    direction "outgoing" → send to the client as the server.
    Reuses the existing session send paths; the target's echo/response flows back through
    the normal logging path and appears in the traffic log on its own.
    """
    if direction not in ("incoming", "outgoing"):
        return {"ok": False, "error": "invalid replay direction"}
    if message_type != "datagram":
        return {"ok": False, "error": "only datagram replay is supported"}

    if not session_id:
        return {"ok": False, "error": "select a session before replaying"}
    session = next((s for s in LIVE_SESSIONS if s.session_uuid == session_id), None)
    if session is None:
        return {"ok": False, "error": "selected session is closed or unknown; select a live session"}
    if not session.ready_for_replay:
        return {"ok": False, "error": "selected session is not ready for replay"}

    data = payload.encode()
    try:
        if direction == "incoming":
            if session.upstream is None:
                return {"ok": False, "error": "upstream not connected yet"}
            session.upstream.send_datagram(data)  # self-transmits
        else:  # outgoing
            session.server_protocol.send_datagram(session.client_session_id, data)
            session.server_protocol.transmit()
    except Exception as e:
        log_error("Replay failed", e)
        return {"ok": False, "error": str(e)}

    # Surface the injected message in the UI (its own REPLAY flag).
    await broadcast_async(make_event(
        session_id=session.session_uuid,
        target=session.target_label,
        direction=direction, etype="datagram",
        payload=payload, raw_size=len(data), latency=0, flag="replay",
    ))
    return {"ok": True, "sessionId": session.session_uuid}


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
    tasks = [s._lifecycle_task for s in _budget_sessions if s._lifecycle_task and not s._lifecycle_task.done()]
    if tasks:
        await asyncio.wait(tasks, timeout=5)


async def start_proxy(port: int = 4433):
    certs_dir = get_certs_dir()

    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=False,
        max_datagram_frame_size=65536,
        idle_timeout=30.0,  # abandoned client connections self-clean within 30s
    )
    from certs import load_cert_chain
    load_cert_chain(config, certs_dir)

    api.register_disconnect_fn(disconnect_all)
    api.register_replay_fn(replay_message)

    server = await serve(
        "127.0.0.1",
        port,
        configuration=config,
        create_protocol=ProxyServerProtocol,
    )
    # Windows: keep the UDP listener alive when a browser reloads (see udp_fix).
    hardened = harden_udp_server(server)
    log_info("MITM proxy started", {"port": port, "udpConnresetFix": hardened})
    return server
