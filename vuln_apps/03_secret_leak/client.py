"""Client for the leaderboard server. Asks for the leaderboard and prints what came back.

    # direct: see the full records (secrets included) the server sends
    .venv\\Scripts\\python.exe vuln_apps\\03_secret_leak\\client.py

    # through Legilimens (:4433): watch the reply get auto-flagged SUSPICIOUS in the log
    #   (first: POST http://localhost:4436/target {"host":"127.0.0.1","port":4453})
    .venv\\Scripts\\python.exe vuln_apps\\03_secret_leak\\client.py --port 4433
"""

import argparse
import asyncio
import json
import ssl
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration


class LeaderboardClient(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._session_id: int | None = None
        self._request_stream_id: int | None = None
        self.ready = asyncio.Event()
        self.reply = asyncio.Event()
        self.board = None

    def quic_event_received(self, event):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for h3_event in self._http.handle_event(event):
            if isinstance(h3_event, HeadersReceived) and h3_event.stream_id == self._request_stream_id:
                if dict(h3_event.headers).get(b":status") == b"200":
                    self._session_id = h3_event.stream_id
                    self.ready.set()
            elif isinstance(h3_event, DatagramReceived) and h3_event.stream_id == self._session_id:
                self.board = h3_event.data.decode(errors="replace")
                self.reply.set()

    async def open(self, authority: str) -> bool:
        stream_id = self._quic.get_next_available_stream_id(is_unidirectional=False)
        self._request_stream_id = stream_id
        self._http.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"CONNECT"), (b":scheme", b"https"),
                (b":authority", authority.encode()), (b":path", b"/leaderboard"),
                (b":protocol", b"webtransport"), (b"origin", f"https://{authority}".encode()),
            ],
        )
        self.transmit()
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            return False
        return self._session_id is not None

    async def ask_leaderboard(self):
        self._http.send_datagram(stream_id=self._session_id, data=json.dumps({"cmd": "leaderboard"}).encode())
        self.transmit()
        try:
            await asyncio.wait_for(self.reply.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4453, help="4453 = leaderboard direct, 4433 = via Legilimens")
    args = p.parse_args()

    authority = f"{args.host}:{args.port}"
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=True,
                               max_datagram_frame_size=65536, verify_mode=ssl.CERT_NONE)

    print(f"[client] connecting to {authority}/leaderboard ...")
    async with connect(args.host, args.port, configuration=config, create_protocol=LeaderboardClient) as c:
        if not await c.open(authority):
            print("[client] CONNECT failed (is the server running / proxy in capturing mode?)")
            return
        await c.ask_leaderboard()
        if not c.board:
            print("[client] no reply.")
            return
        data = json.loads(c.board)
        print("[client] leaderboard received:")
        leaked = False
        for pl in data.get("players", []):
            secret_fields = [k for k in ("email", "session_token", "role") if k in pl]
            if secret_fields:
                leaked = True
            print(f"    {pl.get('name'):<10} score={pl.get('score'):<6} "
                  + "  ".join(f"{k}={pl[k]}" for k in secret_fields))
        if leaked:
            print("[client] ^ the server sent private fields (email / session_token / role) it never should. "
                  "That's the vulnerability.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
