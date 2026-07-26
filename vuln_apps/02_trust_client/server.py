"""Vulnerable app #2 — TRUST-THE-CLIENT (no server-side validation).

A coin-wallet game server. The client reports how many coins it "collected" and the server
just adds that number to the balance — it treats a value the client fully controls as the
source of truth for money. A cheating client (or a MITM like Legilimens) can claim it
collected a million coins, and the server believes it.

The ONLY flaw here is trusting client-supplied state. Authentication is left open on purpose
to keep the focus on this one bug (that's app #1's lesson).

Run:
    .venv\\Scripts\\python.exe vuln_apps\\02_trust_client\\server.py      # listens on :4452
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
PORT = 4452


class WalletProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._sessions: set[int] = set()
        self.balance = 0            # this client's coin wallet

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
                # auth omitted on purpose (that is app #1's lesson) — just open the session
                self._http.send_headers(stream_id=event.stream_id, headers=[(b":status", b"200")])
                self._sessions.add(event.stream_id)
                self.transmit()

        elif isinstance(event, DatagramReceived) and event.stream_id in self._sessions:
            try:
                msg = json.loads(event.data)
            except Exception:
                return
            if msg.get("cmd") == "collect":
                # ------------------------- THE VULNERABILITY -------------------------
                # The server ADDS whatever "amount" the client claims. The client is the
                # source of truth for money, so a forged amount is trusted blindly.
                #
                # The fix is to let the SERVER decide the reward, never the client:
                #
                #     COIN_PER_COLLECT = 1
                #     self.balance += COIN_PER_COLLECT     # ignore msg["amount"] entirely
                # ---------------------------------------------------------------------
                amount = int(msg.get("amount", 0))
                self.balance += amount
                reply = {"balance": self.balance, "credited": amount}
                self._http.send_datagram(stream_id=event.stream_id, data=json.dumps(reply).encode())
                self.transmit()
                print(f"[wallet] credited {amount}  ->  balance = {self.balance}", flush=True)


async def main():
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=False, max_datagram_frame_size=65536)
    config.load_cert_chain(str(CERTS / "cert.pem"), str(CERTS / "key.pem"))
    server = await serve("0.0.0.0", PORT, configuration=config, create_protocol=WalletProtocol)
    harden_udp_server(server)  # Windows: keep the UDP listener alive across browser reloads
    print(f"[wallet] trust-the-client wallet on :{PORT}", flush=True)
    print("READY", flush=True)
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
