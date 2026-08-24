"""Vulnerable app #5 — NO RATE LIMITING (brute-force / DoS).

A vault PIN-pad. A client sends a 4-digit PIN guess; the server says right or wrong. It
processes EVERY guess instantly — no per-client rate limit, no lockout after repeated
failures — so a client can try all 10,000 combinations in seconds and brute-force the secret.
(App #1's vault had no lock at all; this one has a PIN lock, but nothing stops you guessing.)

The ONLY flaw here is the missing rate limit. A 4-digit PIN would be fine WITH throttling.

Run:
    .venv\\Scripts\\python.exe vuln_apps\\05_no_rate_limit\\server.py      # listens on :4455
"""

import asyncio
import json
import random
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
PORT = 4455

# The secret the vault door protects. A random 4-digit PIN, chosen once at startup.
SECRET_PIN = f"{random.randint(0, 9999):04d}"


class PinPadProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._sessions: set[int] = set()
        self._attempts = 0  # guesses on this connection — counted, but never LIMITED

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
            if msg.get("cmd") == "guess":
                # ------------------------- THE VULNERABILITY -------------------------
                # Every guess is processed instantly — no per-client rate limit and no
                # lockout after repeated failures. A client can try all 10,000 PINs in
                # seconds and brute-force the secret.
                #
                # The fix is to throttle / lock out, e.g.:
                #     import time
                #     self._recent = [t for t in self._recent if time.time() - t < 60]
                #     if len(self._recent) >= 5:                 # max 5 guesses / minute
                #         reply = {"result": "rate_limited", "retry_after": 60}
                #         ...send and return...
                #     self._recent.append(time.time())
                # ---------------------------------------------------------------------
                self._attempts += 1
                pin = str(msg.get("pin", ""))
                if pin == SECRET_PIN:
                    reply = {"result": "correct", "pin": SECRET_PIN, "attempts": self._attempts}
                    print(f"[pinpad] cracked after {self._attempts} guesses (pin was {SECRET_PIN})", flush=True)
                else:
                    reply = {"result": "wrong", "attempts": self._attempts}
                self._http.send_datagram(stream_id=event.stream_id, data=json.dumps(reply).encode())
                self.transmit()


async def main():
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=False, max_datagram_frame_size=65536)
    config.load_cert_chain(str(CERTS / "cert.pem"), str(CERTS / "key.pem"))
    server = await serve("0.0.0.0", PORT, configuration=config, create_protocol=PinPadProtocol)
    harden_udp_server(server)  # Windows: keep the UDP listener alive across browser reloads
    print(f"[pinpad] no-rate-limit vault pin-pad on :{PORT}  (secret PIN chosen at random)", flush=True)
    print("READY", flush=True)
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
