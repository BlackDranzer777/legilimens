"""Windows-only workaround for an aioquic asyncio UDP-server wedge.

On Windows, a UDP socket's ``recvfrom`` raises ``ConnectionResetError`` (WSAECONNRESET)
after the OS receives an ICMP "port unreachable" — which happens whenever we send a
datagram to a peer that has just gone away. A browser does exactly this on a page reload:
it reuses its pooled QUIC connection for a second WebTransport session, then tears the old
one down, and our next send to it draws an ICMP unreachable. On the Proactor event loop
that reset kills aioquic's datagram receive loop, so the whole QUIC listener silently stops
accepting ANY new connections (browser or headless) until the process restarts.

Applying SIO_UDP_CONNRESET=0 to the listening socket disables that reset reporting, so the
receive loop survives. CPython's ``socket.ioctl`` does not expose this control code, so we
issue it through ``WSAIoctl`` via ctypes. No-op on non-Windows platforms.
"""

import sys

# SIO_UDP_CONNRESET = _WSAIOW(IOC_VENDOR, 12) = 0x80000000 | 0x18000000 | 12
_SIO_UDP_CONNRESET = 0x9800000C


def harden_udp_server(server) -> bool:
    """Disable Windows UDP connection-reset reporting on an aioquic server's socket.

    ``server`` is the object returned by :func:`aioquic.asyncio.serve`. Returns True if the
    workaround was applied, False if it was skipped (non-Windows) or could not be applied.
    """
    if sys.platform != "win32":
        return False

    sock = None
    try:
        transport = getattr(server, "_transport", None)
        if transport is not None:
            sock = transport.get_extra_info("socket")
    except Exception:
        sock = None
    if sock is None:
        return False

    try:
        import ctypes
        from ctypes import wintypes

        ws2 = ctypes.windll.ws2_32
        ws2.WSAIoctl.argtypes = [
            ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        ws2.WSAIoctl.restype = ctypes.c_int

        inbuf = ctypes.c_ulong(0)  # 0 => do NOT raise on ICMP port-unreachable
        returned = wintypes.DWORD(0)
        ret = ws2.WSAIoctl(
            ctypes.c_void_p(sock.fileno()), _SIO_UDP_CONNRESET,
            ctypes.byref(inbuf), ctypes.sizeof(inbuf),
            None, 0,
            ctypes.byref(returned),
            None, None,
        )
        return ret == 0
    except Exception:
        return False
