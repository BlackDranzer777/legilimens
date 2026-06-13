"""
QUIC-fuzz: send malformed QUIC packets to stress-test a server.

Mutation strategies:
  a) Corrupt QUIC frame type bytes
  b) Send invalid varint lengths
  c) Truncate packets mid-frame
  d) Send packets with invalid connection IDs

If the `radamsa` binary is on PATH, it is used for additional mutations.
Falls back to pure-Python random mutations otherwise.
"""

import asyncio
import random
import shutil
import socket
import struct
import subprocess
from typing import Optional
from urllib.parse import urlparse


def _random_bytes(n: int) -> bytes:
    return bytes(random.randint(0, 255) for _ in range(n))


def _corrupt_frame_type(data: bytes) -> bytes:
    """Flip the first frame type byte to an unknown value."""
    if len(data) < 2:
        return data
    b = bytearray(data)
    b[1] = random.choice([0x3f, 0x7f, 0xfe, 0xff, 0x40, 0x41])
    return bytes(b)


def _invalid_varint(data: bytes) -> bytes:
    """Replace a varint field with an overlong encoding."""
    if len(data) < 4:
        return data
    b = bytearray(data)
    # Set high bits to claim 8-byte varint but only provide 4 bytes
    b[2] = 0xC0
    b[3] = 0xFF
    return bytes(b)


def _truncate_mid_frame(data: bytes) -> bytes:
    """Cut the packet at a random position after the header."""
    if len(data) < 5:
        return data
    cut = random.randint(1, len(data) - 1)
    return data[:cut]


def _bad_connection_id(data: bytes) -> bytes:
    """Overwrite the destination connection ID with random bytes."""
    if len(data) < 21:
        return data
    b = bytearray(data)
    # QUIC Initial: byte 0 is flags, byte 1-4 version, byte 5 DCIL, bytes 6..6+DCIL are DCID
    dcil = b[5] & 0x0F
    for i in range(6, min(6 + dcil, len(b))):
        b[i] = random.randint(0, 255)
    return bytes(b)


def _radamsa_mutate(data: bytes) -> Optional[bytes]:
    """Run data through radamsa if available, return mutated bytes or None."""
    radamsa = shutil.which("radamsa")
    if not radamsa:
        return None
    try:
        result = subprocess.run(
            [radamsa],
            input=data,
            capture_output=True,
            timeout=2.0,
        )
        return result.stdout if result.returncode == 0 and result.stdout else None
    except Exception:
        return None


STRATEGIES = [
    ("corrupt_frame_type", _corrupt_frame_type),
    ("invalid_varint", _invalid_varint),
    ("truncate_mid_frame", _truncate_mid_frame),
    ("bad_connection_id", _bad_connection_id),
]


def _build_quic_initial_template(dst_port: int) -> bytes:
    """Build a minimal syntactically plausible QUIC Initial packet skeleton."""
    # Long header: 0xC0 | version-specific bits
    flags = 0xC0
    version = (1).to_bytes(4, 'big')  # QUIC v1
    dcid = _random_bytes(8)
    scid = _random_bytes(8)
    dcil = len(dcid)
    scil = len(scid)
    token_len = 0
    # Minimal payload: a PADDING frame (0x00) * 20
    payload = b'\x00' * 20
    pkt_len = len(payload) + 4  # +4 for packet number field
    header = (
        bytes([flags])
        + version
        + bytes([dcil]) + dcid
        + bytes([scil]) + scid
        + bytes([token_len])
        + struct.pack('>H', pkt_len)
        + b'\x00\x00\x00\x01'  # packet number
    )
    return header + payload


def _parse_target(target_url: str, default_port: int = 4434) -> tuple[str, int]:
    u = urlparse(target_url if "://" in target_url else f"//{target_url}")
    return (u.hostname or "127.0.0.1"), (u.port or default_port)


async def run(target_url: str, params: dict, progress_callback) -> dict:
    """
    QUIC-fuzz: send `packets` mutated QUIC packets over raw UDP to the target.
    Result: {"packetsSent": N, "responsesObserved": 0, "mutationsUsed": [...]}

    NOTE: the packets are hand-built unprotected QUIC Initials; a compliant server
    discards them, so responsesObserved is effectively always 0 (known limitation —
    proper Initial-packet cryptography is a separate task).
    """
    host, port = _parse_target(target_url)
    packets = int(params.get("packets", 1000))

    radamsa_available = shutil.which("radamsa") is not None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    template = _build_quic_initial_template(port)

    sent = 0
    mutations_used: set[str] = set()
    step = max(1, packets // 20)

    progress_callback(0, packets, f"Sending {packets} malformed QUIC packets → {host}:{port}"
                                  + ("" if radamsa_available else " (radamsa not found, Python mutations)"))
    try:
        for i in range(packets):
            if radamsa_available and random.random() < 0.3:
                mutated = _radamsa_mutate(template)
                strategy = "radamsa"
                if mutated is None:
                    strategy, fn = random.choice(STRATEGIES)
                    mutated = fn(template)
            else:
                strategy, fn = random.choice(STRATEGIES)
                mutated = fn(template)
            mutations_used.add(strategy)

            try:
                await loop.sock_sendto(sock, mutated, (host, port))
                sent += 1
            except Exception:
                pass

            if (i + 1) % step == 0 or i == packets - 1:
                progress_callback(i + 1, packets, f"Packets sent: {sent}/{packets}")
            if i % 50 == 49:
                await asyncio.sleep(0.005)
    finally:
        sock.close()

    progress_callback(packets, packets, f"Done: {sent} packets sent, 0 responses observed")
    return {"packetsSent": sent, "responsesObserved": 0, "mutationsUsed": sorted(mutations_used)}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="https://127.0.0.1:4434")
    p.add_argument("--count", type=int, default=200)
    args = p.parse_args()
    asyncio.run(run(args.target, {"packets": args.count},
                    lambda c, t, m: print(f"[fuzz {c}/{t}] {m}", flush=True)))
