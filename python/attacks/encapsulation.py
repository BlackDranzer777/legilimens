"""
QUIC-encapsulation: craft raw packets with Scapy to probe server behavior.

Techniques:
  1. Encapsulate TCP-like data inside UDP sent to the QUIC port
  2. Encapsulate UDP inside UDP (nested datagrams)
  3. Send UDP datagrams with crafted IP options

REQUIRES: root/CAP_NET_RAW privileges (raises PermissionError if not root).
"""

import asyncio
import os
from urllib.parse import urlparse


def _parse_target(target_url: str, default_port: int = 4434) -> tuple[str, int]:
    u = urlparse(target_url if "://" in target_url else f"//{target_url}")
    return (u.hostname or "127.0.0.1"), (u.port or default_port)


def _require_root():
    # os.geteuid only exists on POSIX. On Windows there's no euid; raw packet crafting
    # needs Administrator + Npcap, which we can't reliably probe — so surface a clear
    # message instead of crashing with AttributeError.
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        raise PermissionError(
            "QUIC-encapsulation needs raw-socket privileges. On Windows, run the backend "
            "as Administrator with Npcap installed."
        )
    if geteuid() != 0:
        raise PermissionError(
            "QUIC-encapsulation requires root/CAP_NET_RAW privileges. "
            "Re-run the backend with: sudo python python/backend.py"
        )


def _import_scapy():
    try:
        from scapy.all import IP, TCP, UDP, Raw, send, sr1
        return IP, TCP, UDP, Raw, send, sr1
    except ImportError:
        raise ImportError("scapy is required: pip install scapy")


async def encapsulation_attack(
    host: str = "127.0.0.1",
    port: int = 4434,
    on_result=None,
) -> dict:
    """
    Send raw encapsulated packets to the target QUIC port.
    Must be run as root.
    """
    _require_root()
    IP, TCP, UDP, Raw, send, sr1 = _import_scapy()

    results: list[dict] = []
    print(f"[encapsulation] Probing {host}:{port} with encapsulated packets (Scapy)", flush=True)

    # Probe 1: TCP data encapsulated inside UDP to the QUIC port
    # Server expects QUIC/UDP; this sends what looks like a TCP segment inside UDP.
    print("[encapsulation] Probe 1: TCP-in-UDP", flush=True)
    try:
        tcp_payload = bytes(TCP(sport=12345, dport=port, flags="S"))
        pkt = IP(dst=host) / UDP(sport=54321, dport=port) / Raw(load=tcp_payload)
        send(pkt, verbose=False)
        results.append({"probe": "tcp_in_udp", "sent": True})
    except Exception as e:
        results.append({"probe": "tcp_in_udp", "error": str(e)})

    await asyncio.sleep(0.2)

    # Probe 2: UDP-in-UDP (nested datagrams)
    print("[encapsulation] Probe 2: UDP-in-UDP", flush=True)
    try:
        inner_udp = bytes(UDP(sport=11111, dport=port) / Raw(load=b"INNER_PAYLOAD"))
        pkt = IP(dst=host) / UDP(sport=54322, dport=port) / Raw(load=inner_udp)
        send(pkt, verbose=False)
        results.append({"probe": "udp_in_udp", "sent": True})
    except Exception as e:
        results.append({"probe": "udp_in_udp", "error": str(e)})

    await asyncio.sleep(0.2)

    # Probe 3: UDP with IP options (loose source routing — often dropped by routers but
    # interesting locally to see if server/OS rejects or processes)
    print("[encapsulation] Probe 3: UDP with IP options", flush=True)
    try:
        from scapy.all import IPOption_LSRR
        pkt = (
            IP(dst=host, options=[IPOption_LSRR(routers=[host])])
            / UDP(sport=54323, dport=port)
            / Raw(load=b"PROBE_IP_OPTIONS")
        )
        send(pkt, verbose=False)
        results.append({"probe": "ip_options_udp", "sent": True})
    except ImportError:
        results.append({"probe": "ip_options_udp", "skipped": "IPOption_LSRR not available"})
    except Exception as e:
        results.append({"probe": "ip_options_udp", "error": str(e)})

    await asyncio.sleep(0.2)

    # Probe 4: Oversized UDP that forces IP fragmentation
    print("[encapsulation] Probe 4: Oversized UDP (IP fragmentation)", flush=True)
    try:
        big_payload = b"X" * 3000
        pkt = IP(dst=host) / UDP(sport=54324, dport=port) / Raw(load=big_payload)
        send(pkt, verbose=False, fragment=True)
        results.append({"probe": "fragmented_udp", "sent": True, "size": len(big_payload)})
    except Exception as e:
        results.append({"probe": "fragmented_udp", "error": str(e)})

    summary = {"probes": len(results), "results": results}
    print(f"[encapsulation] Done: {len(results)} probes", flush=True)
    for r in results:
        print(f"  {r}", flush=True)
    if on_result:
        on_result(summary)
    return summary


async def run(target_url: str, params: dict, progress_callback) -> dict:
    """
    QUIC-encapsulation: craft raw encapsulated packets with Scapy. Cycles the 4
    techniques `packets` times. Requires root — fails fast with a clear error if not.
    Result: {"packetsSent": N, "requiresRoot": True}

    NOTE: send-only — server responses are not captured (known limitation).
    """
    host, port = _parse_target(target_url)
    packets = int(params.get("packets", 100))

    # Fail fast & loud if we lack privileges — this message propagates to the UI.
    _require_root()
    IP, TCP, UDP, Raw, send, sr1 = _import_scapy()

    def _craft(i: int):
        technique = i % 4
        if technique == 0:
            return IP(dst=host) / UDP(sport=54321, dport=port) / Raw(load=bytes(TCP(sport=12345, dport=port, flags="S")))
        if technique == 1:
            inner = bytes(UDP(sport=11111, dport=port) / Raw(load=b"INNER_PAYLOAD"))
            return IP(dst=host) / UDP(sport=54322, dport=port) / Raw(load=inner)
        if technique == 2:
            return IP(dst=host) / UDP(sport=54324, dport=port) / Raw(load=b"X" * 3000)
        return IP(dst=host) / UDP(sport=54323, dport=port) / Raw(load=b"PROBE_ENCAP")

    sent = 0
    step = max(1, packets // 20)
    progress_callback(0, packets, f"Sending {packets} encapsulated packets (Scapy) → {host}:{port}")
    for i in range(packets):
        try:
            send(_craft(i), verbose=False, fragment=(i % 4 == 2))
            sent += 1
        except Exception:
            pass
        if (i + 1) % step == 0 or i == packets - 1:
            progress_callback(i + 1, packets, f"Packets sent: {sent}/{packets}")
        await asyncio.sleep(0)  # yield so the task can be cancelled

    progress_callback(packets, packets, f"Done: {sent} packets sent (send-only, no response capture)")
    return {"packetsSent": sent, "requiresRoot": True}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="https://127.0.0.1:4434")
    p.add_argument("--count", type=int, default=100)
    args = p.parse_args()
    asyncio.run(run(args.target, {"packets": args.count},
                    lambda c, t, m: print(f"[encapsulation {c}/{t}] {m}", flush=True)))
