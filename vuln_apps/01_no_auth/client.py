"""Tiny client for the no-auth vault. Asks for the private balance, with or without a token.

    # no credential at all — the vault hands over the balance anyway (that's the bug)
    .venv\\Scripts\\python.exe vuln_apps\\01_no_auth\\client.py

    # with the token the vault *should* be demanding
    .venv\\Scripts\\python.exe vuln_apps\\01_no_auth\\client.py --token tok-hermione-granger

    # through Legilimens, so you can watch it in the traffic log
    # (first: POST http://localhost:4436/target {"host":"127.0.0.1","port":4451})
    .venv\\Scripts\\python.exe vuln_apps\\01_no_auth\\client.py --port 4433
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


class VaultClient(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._session_id: int | None = None
        self._request_stream_id: int | None = None
        self.status: bytes | None = None
        self.ready = asyncio.Event()
        self.got_reply = asyncio.Event()
        self.replies: list[str] = []

    def quic_event_received(self, event):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for h3_event in self._http.handle_event(event):
            if isinstance(h3_event, HeadersReceived) and h3_event.stream_id == self._request_stream_id:
                self.status = dict(h3_event.headers).get(b":status")
                if self.status == b"200":
                    self._session_id = h3_event.stream_id
                self.ready.set()
            elif isinstance(h3_event, DatagramReceived) and h3_event.stream_id == self._session_id:
                self.replies.append(h3_event.data.decode(errors="replace"))
                self.got_reply.set()

    async def open(self, path: str, authority: str) -> bool:
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
            ],
        )
        self.transmit()
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            return False
        return self._session_id is not None

    def ask_balance(self):
        self._http.send_datagram(stream_id=self._session_id, data=json.dumps({"cmd": "balance"}).encode())
        self.transmit()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4451, help="4451 = vault direct, 4433 = via Legilimens")
    p.add_argument("--token", default=None, help="omit to present NO credential at all")
    args = p.parse_args()

    path = f"/vault?token={args.token}" if args.token else "/vault"
    authority = f"{args.host}:{args.port}"
    config = QuicConfiguration(
        alpn_protocols=H3_ALPN, is_client=True,
        max_datagram_frame_size=65536, verify_mode=ssl.CERT_NONE,
    )

    print(f"[client] connecting to {authority}{path}  (token: {args.token or '<none>'})")
    async with connect(args.host, args.port, configuration=config, create_protocol=VaultClient) as c:
        if not await c.open(path, authority):
            print(f"[client] REFUSED — status {c.status!r}  (this is what a fixed vault does)")
            return
        print("[client] session GRANTED — asking for the private balance...")
        # Through the MITM proxy there's a brief warm-up before it links upstream, and
        # datagrams sent in that window get dropped — so retry until the vault answers.
        for _ in range(6):
            c.ask_balance()
            try:
                await asyncio.wait_for(c.got_reply.wait(), timeout=0.6)
                break
            except asyncio.TimeoutError:
                continue
        for r in c.replies:
            print(f"[client] got: {r}")
        if c.replies and not args.token:
            print("[client] ^ read a private balance with NO credential. That's the vulnerability.")
        elif not c.replies:
            print("[client] no balance reply — vault may be down, or the proxy never linked upstream.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
