"""Minimal WebTransport client (aioquic) for interoperability testing.

Supports datagrams and client-opened bidirectional/unidirectional streams, and
collects server-opened streams. Certificate verification is enabled: the caller
supplies the exact loopback certificate PEM to trust (no CERT_NONE, no global
trust changes). Used to drive an independent quic-go target directly and through
the Legilimens proxy.
"""

import asyncio
import ssl

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.h3.events import DatagramReceived, HeadersReceived, WebTransportStreamDataReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import HandshakeCompleted, StreamDataReceived


def client_config(cafile: str, server_name: str = "localhost") -> QuicConfiguration:
    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=True,
        max_datagram_frame_size=65536,
        verify_mode=ssl.CERT_REQUIRED,   # real verification against the trusted PEM
    )
    config.server_name = server_name
    config.load_verify_locations(cafile=cafile)
    return config


class WTClient(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http = None
        self._alpn = None
        self._session_id = None
        self._req_stream = None
        self._connect_status = None
        self._ready = asyncio.Event()
        self._datagrams = asyncio.Queue()
        self._created = set()            # bidi streams I opened (replies arrive raw)
        self._reply = {}                 # stream_id -> {'buf','fin','ev'}
        self._server_streams = asyncio.Queue()   # completed server-opened streams (bytes)
        self._server_partial = {}        # stream_id -> bytearray

    # ---- event handling ----
    def _reply_rec(self, sid):
        rec = self._reply.get(sid)
        if rec is None:
            rec = {"buf": bytearray(), "fin": False, "ev": asyncio.Event()}
            self._reply[sid] = rec
        return rec

    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            self._alpn = event.alpn_protocol
        if self._http is None:
            self._http = H3Connection(self._quic, enable_webtransport=True)
        # Replies on streams WE opened arrive as raw QUIC stream data, not as H3 events.
        if isinstance(event, StreamDataReceived) and event.stream_id in self._created:
            rec = self._reply_rec(event.stream_id)
            rec["buf"] += event.data
            if event.end_stream:
                rec["fin"] = True
                rec["ev"].set()
            return
        for e in self._http.handle_event(event):
            if isinstance(e, HeadersReceived) and e.stream_id == self._req_stream:
                headers = {k: v for k, v in e.headers}
                self._connect_status = headers.get(b":status")
                if self._connect_status == b"200":
                    self._session_id = e.stream_id
                self._ready.set()
            elif isinstance(e, DatagramReceived) and e.stream_id == self._session_id:
                self._datagrams.put_nowait(bytes(e.data))
            elif isinstance(e, WebTransportStreamDataReceived):
                buf = self._server_partial.setdefault(e.stream_id, bytearray())
                buf += e.data
                if e.stream_ended:
                    self._server_streams.put_nowait(bytes(buf))
                    self._server_partial.pop(e.stream_id, None)

    # ---- client API ----
    async def connect_session(self, path, authority, origin=None, timeout=10.0) -> bool:
        if self._alpn not in H3_ALPN:
            raise RuntimeError("TLS did not negotiate HTTP/3 ALPN")
        sid = self._quic.get_next_available_stream_id(is_unidirectional=False)
        self._req_stream = sid
        headers = [
            (b":method", b"CONNECT"), (b":scheme", b"https"),
            (b":authority", authority.encode()), (b":path", path.encode()),
            (b":protocol", b"webtransport"),
        ]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        self._http.send_headers(stream_id=sid, headers=headers)
        self.transmit()
        await asyncio.wait_for(self._ready.wait(), timeout)
        return self._connect_status == b"200"

    def send_datagram(self, data: bytes):
        self._http.send_datagram(stream_id=self._session_id, data=data)
        self.transmit()

    async def recv_datagram(self, timeout=2.0) -> bytes:
        return await asyncio.wait_for(self._datagrams.get(), timeout)

    async def bidi_echo(self, data: bytes, timeout=5.0, chunk_size=None) -> bytes:
        sid = self._http.create_webtransport_stream(self._session_id, is_unidirectional=False)
        self._created.add(sid)
        rec = self._reply_rec(sid)
        chunk_size = chunk_size or max(1, len(data))
        for offset in range(0, len(data), chunk_size):
            self._quic.send_stream_data(sid, data[offset:offset + chunk_size])
            self.transmit()
            await asyncio.sleep(0.01)
        self._quic.send_stream_data(sid, b"", end_stream=True)
        self.transmit()
        await asyncio.wait_for(rec["ev"].wait(), timeout)
        return bytes(rec["buf"])

    def open_uni(self, data: bytes):
        sid = self._http.create_webtransport_stream(self._session_id, is_unidirectional=True)
        self._quic.send_stream_data(sid, data, end_stream=True)
        self.transmit()

    async def next_server_stream(self, timeout=5.0) -> bytes:
        return await asyncio.wait_for(self._server_streams.get(), timeout)


def open_session(host, port, cafile, server_name="localhost"):
    """Return an async context manager yielding a connected WTClient protocol."""
    return connect(host, port, configuration=client_config(cafile, server_name),
                   create_protocol=WTClient)
