"""
Test client for Legilimens — stands in for a real game client (e.g. Vault713).

Connects to the proxy (:4433) over WebTransport, periodically sends datagrams that
look like game state (score / move for player "Seeker"), and prints every datagram
it receives back. Use this to verify the full pipe:

    client → proxy(:4433) → vulnerable_server(:4434) → proxy → client

While this runs you should see:
  - traffic appear in the React UI / WebSocket log
  - the vulnerable server's heartbeat datagrams (with session_token) flagged suspicious
  - your "score" rewritten if you enable a tamper rule (field=score, value=99999)

Note: the proxy starts in 'paused' mode — press START in the UI, or run:
    curl -X POST http://localhost:4436/intercept -H 'Content-Type: application/json' -d '{"action":"start"}'

Usage:
    python python/test_client.py                 # connect to proxy :4433 (default)
    python python/test_client.py --port 4434     # connect straight to the target
"""

import argparse
import asyncio
import json
import ssl
import uuid

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent


class TestClientProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._session_id: int | None = None
        self._request_stream_id: int | None = None
        self._ready = asyncio.Event()

    def quic_event_received(self, event: QuicEvent):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for h3_event in self._http.handle_event(event):
            if isinstance(h3_event, HeadersReceived):
                if h3_event.stream_id == self._request_stream_id:
                    headers = {k: v for k, v in h3_event.headers}
                    if headers.get(b":status") == b"200":
                        self._session_id = h3_event.stream_id
                        self._ready.set()
            elif isinstance(h3_event, DatagramReceived):
                if h3_event.stream_id == self._session_id:
                    payload = h3_event.data.decode(errors="replace")
                    print(f"  ← received: {payload[:120]}", flush=True)

    async def open_session(self, path: str, authority: str) -> bool:
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
            await asyncio.wait_for(self._ready.wait(), timeout=10.0)
            return True
        except asyncio.TimeoutError:
            return False

    def send_game_datagram(self, score: int, x: int, y: int):
        if self._session_id is None:
            return
        msg = json.dumps({
            "type": "move",
            "playerId": "p-test01",
            "playerName": "Seeker",
            "email": "seeker@hogwarts.test",
            "session_token": f"sess_secret_{uuid.uuid4().hex[:12]}",
            "x": x, "y": y, "speed": 20, "score": score,
        })
        self._http.send_datagram(stream_id=self._session_id, data=msg.encode())
        self.transmit()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4433, help="4433 = proxy, 4434 = direct to target")
    p.add_argument("--path", default="/")
    p.add_argument("--count", type=int, default=30, help="how many datagrams to send (0 = forever)")
    args = p.parse_args()

    authority = f"{args.host}:{args.port}"
    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=True,
        max_datagram_frame_size=65536,
        verify_mode=ssl.CERT_NONE,  # local self-signed cert — skip verification for the test client
    )

    print(f"[test-client] Connecting to {authority}{args.path} ...", flush=True)
    async with connect(args.host, args.port, configuration=config,
                       create_protocol=TestClientProtocol) as client:
        ok = await client.open_session(args.path, authority)
        if not ok:
            print("[test-client] WebTransport CONNECT failed (timed out). "
                  "Is the proxy in 'capturing' mode? Press START in the UI.", flush=True)
            return
        print("[test-client] Session established. Sending game datagrams...", flush=True)

        seq = 0
        score = 0
        while args.count == 0 or seq < args.count:
            score += 10
            client.send_game_datagram(score=score, x=seq, y=seq * 2)
            print(f"  → sent:     move score={score} x={seq}", flush=True)
            seq += 1
            await asyncio.sleep(1.0)

        # give late echoes a moment to arrive
        await asyncio.sleep(1.0)
        print(f"[test-client] Done — sent {seq} datagrams.", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
