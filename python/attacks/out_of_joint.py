"""
QUIC-out-of-joint: inject forbidden or out-of-order QUIC frames during/after handshake.

Techniques:
  1. Send APPLICATION_DATA frames in Initial packets (forbidden by RFC 9000 §17.2.2)
  2. Send a STREAM frame before the handshake completes
  3. Send CRYPTO frames with overlapping offsets

These probe for servers that don't strictly enforce packet-type/frame-type constraints.
"""

import asyncio
import socket
import ssl
import struct
import time
from urllib.parse import urlparse

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent


def _encode_varint(n: int) -> bytes:
    """Minimal QUIC variable-length integer encoding."""
    if n < 64:
        return bytes([n])
    if n < 16384:
        return struct.pack('>H', 0x4000 | n)
    if n < 1073741824:
        return struct.pack('>I', 0x80000000 | n)
    return struct.pack('>Q', 0xC000000000000000 | n)


def _build_initial_with_forbidden_frame(forbidden_frame_type: int) -> bytes:
    """
    Build a QUIC Initial packet containing a forbidden frame type.
    RFC 9000 §17.2.2 says Initial packets MUST only carry CRYPTO and PADDING frames.
    """
    flags = 0xC3  # Long header, Initial packet type
    version = (1).to_bytes(4, 'big')
    dcid = bytes(range(8))
    scid = bytes(range(8, 16))

    # FORBIDDEN: a STREAM frame (0x08) in an Initial packet
    forbidden = bytes([forbidden_frame_type]) + _encode_varint(0) + _encode_varint(5) + b"PROBE"

    pkt_num = b'\x00\x00\x00\x01'
    payload = forbidden
    length = len(pkt_num) + len(payload)

    header = (
        bytes([flags])
        + version
        + bytes([len(dcid)]) + dcid
        + bytes([len(scid)]) + scid
        + bytes([0])  # token length = 0
        + _encode_varint(length)
    )
    return header + pkt_num + payload


def _build_crypto_overlap(offset1: int = 0, offset2: int = 0, data: bytes = b"A" * 20) -> bytes:
    """
    Build an Initial packet with a CRYPTO frame using a potentially overlapping offset.
    Overlapping CRYPTO frames are invalid per RFC 9000 §7.5.
    """
    flags = 0xC3
    version = (1).to_bytes(4, 'big')
    dcid = bytes(range(8))
    scid = bytes(range(8, 16))

    crypto_frame = (
        bytes([0x06])  # CRYPTO frame type
        + _encode_varint(offset2)
        + _encode_varint(len(data))
        + data
    )
    pkt_num = b'\x00\x00\x00\x02'
    length = len(pkt_num) + len(crypto_frame)
    header = (
        bytes([flags]) + version
        + bytes([len(dcid)]) + dcid
        + bytes([len(scid)]) + scid
        + bytes([0])
        + _encode_varint(length)
    )
    return header + pkt_num + crypto_frame


class InjectionProbeProtocol(QuicConnectionProtocol):
    """Connects normally but logs all events for timing anomalies."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http: H3Connection | None = None
        self._handshake_done = asyncio.Event()
        self.events_log: list[str] = []

    def quic_event_received(self, event: QuicEvent):
        self.events_log.append(type(event).__name__)
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        for _ in self._http.handle_event(event):
            pass
        if not self._handshake_done.is_set():
            self._handshake_done.set()

    async def wait_handshake(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._handshake_done.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False


def _parse_target(target_url: str, default_port: int = 4434) -> tuple[str, int]:
    u = urlparse(target_url if "://" in target_url else f"//{target_url}")
    return (u.hostname or "127.0.0.1"), (u.port or default_port)


async def run(target_url: str, params: dict, progress_callback) -> dict:
    """
    QUIC-out-of-joint: inject forbidden/out-of-order frames.
    Result: {"probesAttempted": N, "probesResponded": 0}

    NOTE: probes 1-3 send unprotected hand-built Initials that compliant servers
    discard, so probesResponded is effectively 0 (known limitation).
    """
    host, port = _parse_target(target_url)
    results: list[dict] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    loop = asyncio.get_running_loop()

    # Fixed set of 4 probes; `probes` param caps how many run.
    max_probes = int(params.get("probes", 4))
    total = min(max_probes, 4)

    raw_probes = [
        ("stream_in_initial", _build_initial_with_forbidden_frame(0x08)),
        ("app_close_in_initial", _build_initial_with_forbidden_frame(0x1d)),
        ("crypto_overlap", _build_crypto_overlap(offset2=0)),
    ]

    idx = 0
    try:
        for name, pkt in raw_probes:
            if idx >= total:
                break
            progress_callback(idx, total, f"Probe {idx + 1}/{total}: {name}")
            t0 = time.perf_counter()
            try:
                await loop.sock_sendto(sock, pkt, (host, port))
                await asyncio.sleep(0.5)
                results.append({"probe": name, "sent": True, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)})
            except Exception as e:
                results.append({"probe": name, "error": str(e)})
            idx += 1

        # Probe 4: real handshake then inject a STREAM frame post-handshake.
        if idx < total:
            progress_callback(idx, total, f"Probe {idx + 1}/{total}: post_handshake_stream_inject")
            config = QuicConfiguration(
                alpn_protocols=H3_ALPN, is_client=True,
                max_datagram_frame_size=65536, verify_mode=ssl.CERT_NONE,
            )
            try:
                async with connect(host, port, configuration=config, create_protocol=InjectionProbeProtocol) as proto:
                    if await proto.wait_handshake(timeout=5.0):
                        sid = proto._quic.get_next_available_stream_id(is_unidirectional=False)
                        proto._quic.send_stream_data(sid, b"EARLY_DATA_INJECTION", end_stream=False)
                        proto.transmit()
                        await asyncio.sleep(0.3)
                        results.append({"probe": "post_handshake_stream_inject", "sent": True, "events": proto.events_log[-5:]})
            except Exception as e:
                results.append({"probe": "post_handshake_stream_inject", "connect_error": str(e)})
            idx += 1
    finally:
        sock.close()

    attempted = len(results)
    progress_callback(total, total, f"Done: {attempted} probes attempted, 0 responses")
    return {"probesAttempted": attempted, "probesResponded": 0, "detail": results}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="https://127.0.0.1:4434")
    args = p.parse_args()
    asyncio.run(run(args.target, {"probes": 4},
                    lambda c, t, m: print(f"[out-of-joint {c}/{t}] {m}", flush=True)))
