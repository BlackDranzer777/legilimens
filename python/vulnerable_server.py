"""
Deliberately vulnerable WebTransport server on :4434.

Intentional vulnerabilities (replicated from vulnerable-server.js):
  1. No authentication — every connection accepted
  2. Sensitive data leak — session_token + email in every heartbeat datagram
  3. Trust-the-client — echoes client data without validation
  4. No input validation, no rate limiting, no nonce on scoring events
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import (
    DatagramReceived,
    HeadersReceived,
    WebTransportStreamDataReceived,
)
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent

from logger import log_info, log_error

CERTS_DIR = Path(__file__).parent / "certs"


class VulnerableSession:
    """One WebTransport session with all four vulnerabilities active."""

    def __init__(self, protocol: "VulnerableServerProtocol", session_id: int):
        self.protocol = protocol
        self.session_id = session_id
        self._running = True
        self._heartbeat_task: asyncio.Task | None = None

    def start(self):
        # VULN 1: anonymous connection silently accepted, no credentials checked
        print(json.dumps({"level": "warn", "msg": "Anonymous connection accepted", "vuln": "NO_AUTH"}), flush=True)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def stop(self):
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

    async def _heartbeat_loop(self):
        """VULN 2: session_token in cleartext every 2 s to ALL connected sessions."""
        player_id = f"p-{uuid.uuid4().hex[:6]}"
        while self._running:
            heartbeat = json.dumps({
                "type": "move",
                "playerId": player_id,
                "playerName": "Seeker",
                "email": "seeker@hogwarts.test",
                "session_token": f"sess_secret_{uuid.uuid4().hex[:16]}",
                "x": 0,
                "y": 0,
                "speed": 20,
                "score": 0,
            })
            try:
                self.protocol.send_datagram(self.session_id, heartbeat.encode())
                self.protocol.transmit()
            except Exception:
                break
            await asyncio.sleep(2.0)

    def datagram_received(self, data: bytes):
        payload = data.decode(errors="replace")
        # VULN 3: no size check, no JSON validation, no injection filtering
        print(json.dumps({
            "level": "warn",
            "msg": "Echoing unvalidated datagram",
            "vuln": "NO_VALIDATION",
            "preview": payload[:80],
        }), flush=True)
        echo = json.dumps({"type": "echo", "original": payload, "server": "vulnerable-v1"})
        try:
            self.protocol.send_datagram(self.session_id, echo.encode())
            self.protocol.transmit()
        except Exception:
            pass  # VULN 4: errors silently ignored

    def stream_data_received(self, stream_id: int, data: bytes, ended: bool):
        if not data:
            return
        payload = data.decode(errors="replace")
        print(json.dumps({"level": "warn", "msg": "Unauthenticated stream accepted", "vuln": "NO_AUTH"}), flush=True)
        echo = json.dumps({"type": "stream_echo", "original": payload})
        try:
            self.protocol.send_stream(self.session_id, stream_id, echo.encode(), ended)
            self.protocol.transmit()
        except Exception:
            pass


class VulnerableServerProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._sessions: dict[int, VulnerableSession] = {}

    def quic_event_received(self, event: QuicEvent):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for h3_event in self._http.handle_event(event):
            self._h3_event_received(h3_event)

    def _h3_event_received(self, event):
        if isinstance(event, HeadersReceived):
            headers = {k: v for k, v in event.headers}
            if (headers.get(b":method") == b"CONNECT"
                    and headers.get(b":protocol") == b"webtransport"):
                self._accept_session(event.stream_id)

        elif isinstance(event, DatagramReceived):
            session = self._sessions.get(event.stream_id)
            if session:
                session.datagram_received(event.data)

        elif isinstance(event, WebTransportStreamDataReceived):
            session = self._sessions.get(event.session_id)
            if session:
                session.stream_data_received(event.stream_id, event.data, event.stream_ended)

    def _accept_session(self, stream_id: int):
        self._http.send_headers(
            stream_id=stream_id,
            headers=[
                (b":status", b"200"),
                (b"sec-webtransport-http3-draft", b"draft02"),
            ],
        )
        self.transmit()
        session = VulnerableSession(self, stream_id)
        self._sessions[stream_id] = session
        session.start()

        async def cleanup():
            try:
                await self._quic._streams.get(stream_id, asyncio.Future()).wait_closed()
            except Exception:
                pass
            session.stop()
            self._sessions.pop(stream_id, None)

    def send_datagram(self, session_id: int, data: bytes):
        self._http.send_datagram(stream_id=session_id, data=data)

    def send_stream(self, session_id: int, stream_id: int, data: bytes, end: bool = False):
        self._quic.send_stream_data(stream_id, data, end_stream=end)


async def start_vulnerable_server(port: int = 4434):
    certs_dir = Path(__file__).parent / "certs"
    cert_file = str(certs_dir / "cert.pem")
    key_file = str(certs_dir / "key.pem")

    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=False,
        max_datagram_frame_size=65536,
    )
    config.load_cert_chain(cert_file, key_file)

    server = await serve(
        "0.0.0.0",
        port,
        configuration=config,
        create_protocol=VulnerableServerProtocol,
    )
    log_info("Vulnerable WebTransport server started", {"port": port})
    return server
