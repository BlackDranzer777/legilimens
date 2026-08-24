"""Vulnerable app #3 — SENSITIVE DATA EXPOSURE (secret leakage).

A leaderboard server. When a client asks for the leaderboard, the server serializes its WHOLE
internal user record for every player — including email, session_token, and role — instead of
just the public {name, score}. So anyone who connects can read everyone's secrets.

The ONLY flaw here is over-sharing internal fields. Auth and validation are not the point
(those are apps #1 and #2); the focus is what ends up in the payload.

Run:
    .venv\\Scripts\\python.exe vuln_apps\\03_secret_leak\\server.py      # listens on :4453
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
PORT = 4453

# The server's INTERNAL user records. name + score are public; email, session_token, role
# are secrets that should never leave the server.
USERS = [
    {"name": "Hermione", "score": 9001, "email": "hermione@hogwarts.test",
     "session_token": "sess_secret_a1b2c3d4", "role": "admin"},
    {"name": "Harry", "score": 8500, "email": "harry@hogwarts.test",
     "session_token": "sess_secret_e5f6g7h8", "role": "user"},
    {"name": "Ron", "score": 7200, "email": "ron@hogwarts.test",
     "session_token": "sess_secret_i9j0k1l2", "role": "user"},
]


class LeaderboardProtocol(QuicConnectionProtocol):
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
                cmd = json.loads(event.data).get("cmd")
            except Exception:
                cmd = None
            if cmd == "leaderboard":
                # ------------------------- THE VULNERABILITY -------------------------
                # The whole internal record for every user is serialized to the client,
                # leaking email / session_token / role that should never leave the server.
                #
                # The fix is to send only a PUBLIC projection — the fields the client needs:
                #
                #     public = [{"name": u["name"], "score": u["score"]} for u in USERS]
                #     board = {"type": "leaderboard", "players": public}
                # ---------------------------------------------------------------------
                board = {"type": "leaderboard", "players": USERS}
                self._http.send_datagram(stream_id=event.stream_id, data=json.dumps(board).encode())
                self.transmit()
                print("[leaderboard] served full user records (secrets included)", flush=True)


async def main():
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=False, max_datagram_frame_size=65536)
    config.load_cert_chain(str(CERTS / "cert.pem"), str(CERTS / "key.pem"))
    server = await serve("0.0.0.0", PORT, configuration=config, create_protocol=LeaderboardProtocol)
    harden_udp_server(server)  # Windows: keep the UDP listener alive across browser reloads
    print(f"[leaderboard] secret-leaking leaderboard on :{PORT}", flush=True)
    print("READY", flush=True)
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
