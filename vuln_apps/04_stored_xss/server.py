"""Vulnerable app #4 — NO INPUT VALIDATION -> STORED XSS.

A guestbook server. Clients POST a message; the server stores it verbatim and serves it back
to anyone who LISTs. It never validates, limits, or escapes the text — so a message containing
"<script>...</script>" is stored as-is and handed to every viewer. Any client UI that renders
the guestbook as HTML will execute that script: stored cross-site scripting.

The ONLY flaw here is unvalidated / unescaped input. Auth, trust, and leakage are apps #1-#3.

Run:
    .venv\\Scripts\\python.exe vuln_apps\\04_stored_xss\\server.py      # listens on :4454
"""

import asyncio
import json
import sys
from pathlib import Path

# reuse the shared cert + the Windows UDP listener fix from the main backend
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration

from udp_fix import harden_udp_server

CERTS = Path(__file__).resolve().parents[2] / "python" / "certs"
PORT = 4454

# The shared guestbook. Anything posted here is later served to EVERY viewer.
MESSAGES: list[str] = []


class GuestbookProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._sessions: set[int] = set()

    def quic_event_received(self, event):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for h3_event in self._http.handle_event(event):
            self._on(h3_event)

    def _on(self, event):
        if isinstance(event, HeadersReceived):
            headers = {k: v for k, v in event.headers}
            if (headers.get(b":method") == b"CONNECT"
                    and headers.get(b":protocol") == b"webtransport"):
                self._http.send_headers(stream_id=event.stream_id, headers=[(b":status", b"200")])
                self._sessions.add(event.stream_id)
                self.transmit()

        elif isinstance(event, DatagramReceived) and event.stream_id in self._sessions:
            try:
                msg = json.loads(event.data)
            except Exception:
                return
            cmd = msg.get("cmd")

            if cmd == "post":
                # ------------------------- THE VULNERABILITY -------------------------
                # The text is stored VERBATIM — no length check, no validation, no HTML
                # escaping. "<script>steal()</script>" is kept as-is and later handed to
                # every viewer, so any UI that renders it as HTML runs the script.
                #
                # The fix is to validate + escape on the way IN:
                #
                #     import html
                #     text = str(msg.get("text", ""))[:280]     # length limit
                #     text = html.escape(text)                  # <script> -> &lt;script&gt;
                # ---------------------------------------------------------------------
                text = msg.get("text", "")
                MESSAGES.append(text)
                reply = {"type": "ack", "stored": len(MESSAGES)}
                self._http.send_datagram(stream_id=event.stream_id, data=json.dumps(reply).encode())
                self.transmit()
                print(f"[guestbook] stored message #{len(MESSAGES)}: {text[:60]}", flush=True)

            elif cmd == "list":
                reply = {"type": "guestbook", "messages": MESSAGES}
                self._http.send_datagram(stream_id=event.stream_id, data=json.dumps(reply).encode())
                self.transmit()


async def main():
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=False, max_datagram_frame_size=65536)
    config.load_cert_chain(str(CERTS / "cert.pem"), str(CERTS / "key.pem"))
    server = await serve("0.0.0.0", PORT, configuration=config, create_protocol=GuestbookProtocol)
    harden_udp_server(server)  # Windows: keep the UDP listener alive across browser reloads
    print(f"[guestbook] no-validation guestbook on :{PORT}", flush=True)
    print("READY", flush=True)
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
