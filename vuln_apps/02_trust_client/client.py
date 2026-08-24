"""Client for the coin-wallet server. Honestly reports collecting 1 coin at a time.

    # direct, honest: balance goes up by 1 each collect
    .venv\\Scripts\\python.exe vuln_apps\\02_trust_client\\client.py

    # a CHEATING client can just claim a huge amount — the server trusts it
    .venv\\Scripts\\python.exe vuln_apps\\02_trust_client\\client.py --amount 1000000

    # honest client THROUGH Legilimens (:4433): set a tamper rule on 'amount' and the
    # server credits whatever Legilimens rewrites it to — proof the server trusts input.
    #   (first: POST http://localhost:4436/target {"host":"127.0.0.1","port":4452})
    .venv\\Scripts\\python.exe vuln_apps\\02_trust_client\\client.py --port 4433
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


class WalletClient(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._session_id: int | None = None
        self._request_stream_id: int | None = None
        self.ready = asyncio.Event()
        self.reply = asyncio.Event()
        self.last = None

    def quic_event_received(self, event):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for h3_event in self._http.handle_event(event):
            if isinstance(h3_event, HeadersReceived) and h3_event.stream_id == self._request_stream_id:
                if dict(h3_event.headers).get(b":status") == b"200":
                    self._session_id = h3_event.stream_id
                    self.ready.set()
            elif isinstance(h3_event, DatagramReceived) and h3_event.stream_id == self._session_id:
                self.last = h3_event.data.decode(errors="replace")
                self.reply.set()

    async def open(self, authority: str) -> bool:
        stream_id = self._quic.get_next_available_stream_id(is_unidirectional=False)
        self._request_stream_id = stream_id
        self._http.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"CONNECT"), (b":scheme", b"https"),
                (b":authority", authority.encode()), (b":path", b"/wallet"),
                (b":protocol", b"webtransport"), (b"origin", f"https://{authority}".encode()),
            ],
        )
        self.transmit()
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            return False
        return self._session_id is not None

    async def collect(self, amount: int):
        self.reply.clear()
        self._http.send_datagram(stream_id=self._session_id,
                                 data=json.dumps({"cmd": "collect", "amount": amount}).encode())
        self.transmit()
        try:
            await asyncio.wait_for(self.reply.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4452, help="4452 = wallet direct, 4433 = via Legilimens")
    p.add_argument("--amount", type=int, default=1, help="coins the client CLAIMS per collect")
    p.add_argument("--count", type=int, default=3, help="how many collects to send")
    args = p.parse_args()

    authority = f"{args.host}:{args.port}"
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=True,
                               max_datagram_frame_size=65536, verify_mode=ssl.CERT_NONE)

    print(f"[client] connecting to {authority}/wallet  (claiming {args.amount} coin/collect)")
    async with connect(args.host, args.port, configuration=config, create_protocol=WalletClient) as c:
        if not await c.open(authority):
            print("[client] CONNECT failed (is the wallet running / proxy in capturing mode?)")
            return
        for i in range(args.count):
            await c.collect(args.amount)
            print(f"[client] collect #{i + 1}: server says {c.last}")
    print("[client] done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
