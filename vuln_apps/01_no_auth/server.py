"""Vulnerable app #1 — NO AUTHENTICATION on the WebTransport CONNECT.

A tiny "vault" that serves a private account balance over WebTransport.

The ONLY flaw here is that the server reads the credential off the CONNECT request and
then never checks it, so every peer gets in. Everything else is deliberately done right
(no secrets in the payloads, no client-supplied state trusted) so that when you point
Legilimens at this app, the only thing you are looking at is unauthenticated access.

Run:
    .venv\\Scripts\\python.exe vuln_apps\\01_no_auth\\server.py      # listens on :4451
"""

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# reuse the shared cert + the Windows UDP listener fix from the main backend
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration

from udp_fix import harden_udp_server

CERTS = Path(__file__).resolve().parents[2] / "python" / "certs"
PORT = 4451

# The one credential that *should* be able to open the vault.
VALID_TOKENS = {"tok-hermione-granger"}

# Private data. Note: no "token"/"secret"/"password" words — so Legilimens will log this as
# a NORMAL datagram, not SUSPICIOUS. The bug is not that we leak it; it's WHO we hand it to.
ACCOUNT = {"account": "ACC-4417", "owner": "H. Potter", "balance": 10450, "currency": "GAL"}


class VaultProtocol(QuicConnectionProtocol):
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
                path = headers.get(b":path", b"/").decode()
                token = parse_qs(urlparse(path).query).get("token", [None])[0]

                # ------------------------- THE VULNERABILITY -------------------------
                # The token is parsed above and then completely ignored. Every CONNECT is
                # answered with 200, so an anonymous peer gets a live session.
                #
                # The fix is to reject before opening the session:
                #
                #     if token not in VALID_TOKENS:
                #         self._http.send_headers(
                #             stream_id=event.stream_id, headers=[(b":status", b"401")]
                #         )
                #         self.transmit()
                #         return
                # ---------------------------------------------------------------------
                self._http.send_headers(stream_id=event.stream_id, headers=[(b":status", b"200")])
                self._sessions.add(event.stream_id)
                self.transmit()

                verdict = "valid" if token in VALID_TOKENS else "INVALID/MISSING → should have been 401"
                print(f"[vault] ACCEPTED session  token={token or '<none>'}  ({verdict})", flush=True)

        elif isinstance(event, DatagramReceived) and event.stream_id in self._sessions:
            try:
                cmd = json.loads(event.data).get("cmd")
            except Exception:
                cmd = None
            if cmd == "balance":
                self._http.send_datagram(stream_id=event.stream_id, data=json.dumps(ACCOUNT).encode())
                self.transmit()
                print("[vault] served the private balance", flush=True)


async def main():
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=False, max_datagram_frame_size=65536)
    config.load_cert_chain(str(CERTS / "cert.pem"), str(CERTS / "key.pem"))
    server = await serve("0.0.0.0", PORT, configuration=config, create_protocol=VaultProtocol)
    harden_udp_server(server)  # Windows: keep the UDP listener alive across browser reloads
    print(f"[vault] no-auth vault on :{PORT}  (the token it should demand: {sorted(VALID_TOKENS)[0]})", flush=True)
    print("READY", flush=True)
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
