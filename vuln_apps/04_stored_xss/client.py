"""Client for the guestbook server. Posts a message, then lists the guestbook back.

    # direct: posts a <script> payload and shows it stored UNescaped
    .venv\\Scripts\\python.exe vuln_apps\\04_stored_xss\\client.py

    # post your own text
    .venv\\Scripts\\python.exe vuln_apps\\04_stored_xss\\client.py --text "hello"

    # through Legilimens (:4433): watch the payload in the log — or inject one with the Repeater
    #   (first: POST http://localhost:4436/target {"host":"127.0.0.1","port":4454})
    .venv\\Scripts\\python.exe vuln_apps\\04_stored_xss\\client.py --port 4433
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

MARKUP_SIGNS = ("<script", "<img", "onerror", "javascript:")


class GuestbookClient(QuicConnectionProtocol):
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
                (b":authority", authority.encode()), (b":path", b"/guestbook"),
                (b":protocol", b"webtransport"), (b"origin", f"https://{authority}".encode()),
            ],
        )
        self.transmit()
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            return False
        return self._session_id is not None

    async def send(self, obj) -> str | None:
        self.reply.clear()
        self._http.send_datagram(stream_id=self._session_id, data=json.dumps(obj).encode())
        self.transmit()
        try:
            await asyncio.wait_for(self.reply.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            return None
        return self.last


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4454, help="4454 = guestbook direct, 4433 = via Legilimens")
    p.add_argument("--text", default="<script>alert('xss')</script>", help="message to post")
    args = p.parse_args()

    authority = f"{args.host}:{args.port}"
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=True,
                               max_datagram_frame_size=65536, verify_mode=ssl.CERT_NONE)

    print(f"[client] connecting to {authority}/guestbook ...")
    async with connect(args.host, args.port, configuration=config, create_protocol=GuestbookClient) as c:
        if not await c.open(authority):
            print("[client] CONNECT failed (is the server running / proxy in capturing mode?)")
            return

        print(f"[client] posting: {args.text}")
        await c.send({"cmd": "post", "text": args.text})

        listed = await c.send({"cmd": "list"})
        if not listed:
            print("[client] no list reply.")
            return
        messages = json.loads(listed).get("messages", [])
        print(f"[client] guestbook now has {len(messages)} message(s):")
        stored_raw = False
        for m in messages:
            flag = "  <-- RAW MARKUP (would execute in a browser)" if any(s in m.lower() for s in MARKUP_SIGNS) else ""
            if flag:
                stored_raw = True
            print(f"    {m}{flag}")
        if stored_raw:
            print("[client] ^ the server stored script/markup verbatim, un-escaped. "
                  "Any UI that renders this as HTML runs it. That's stored XSS.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
