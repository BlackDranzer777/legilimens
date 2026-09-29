"""
WebSocket broadcaster on :4435.
Provides broadcast(), log_info(), log_error() for other modules.
"""

import asyncio
import json
import time
import uuid
from typing import Set
from http import HTTPStatus
from dataclasses import dataclass, field
from contextlib import suppress
from collections import deque
from threading import Lock

from websockets.asyncio.server import ServerConnection, serve
from control_security import BIND_HOST, security, valid_host

_clients: Set[ServerConnection] = set()
_loop: asyncio.AbstractEventLoop | None = None
AUTH_TIMEOUT = 5
MAX_SUBSCRIBERS = 16
MAX_PEERS = 32
_peers = 0
MAX_MESSAGES = 256
MAX_QUEUE_BYTES = 4 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
SEND_TIMEOUT = 5
EPOCH = str(uuid.uuid4())
_sequence = 0


def cursor():
    return {"epoch": EPOCH, "sequence": _sequence}


@dataclass
class Outbox:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(MAX_MESSAGES))
    bytes: int = 0
    overflow: bool = False


_outboxes: dict[ServerConnection, Outbox] = {}
_thread_events = deque()
_thread_lock = Lock()
_thread_bytes = 0
_thread_scheduled = False
_thread_overflow = False


def _message(event):
    message = json.dumps(event, ensure_ascii=True)
    if len(message) > MAX_EVENT_BYTES:
        return json.dumps({"type": "resource", "message": "Capture event omitted: event size limit exceeded."})
    return message


def _enqueue(message):
    global _sequence
    _sequence += 1
    # Sequence is assigned at dispatch, including cross-thread ingress, not creation.
    message = '{"epoch":' + json.dumps(EPOCH) + ',"sequence":' + str(_sequence) + ',"event":' + message + '}'
    for client, outbox in list(_outboxes.items()):
        if outbox.overflow:
            continue
        if outbox.queue.full() or outbox.bytes + len(message) > MAX_QUEUE_BYTES:
            outbox.overflow = True
            continue
        outbox.bytes += len(message)
        outbox.queue.put_nowait(message)


async def _sender(client, outbox):
    try:
        while not outbox.overflow:
            message = await outbox.queue.get()
            try:
                await asyncio.wait_for(client.send(message), SEND_TIMEOUT)
            finally:
                outbox.bytes -= len(message)
        await client.close(code=1013, reason="Capture queue overflow; capture is incomplete")
    except Exception:
        await client.close(code=1013, reason="Capture subscriber too slow or disconnected")


def _drain_thread_events():
    global _thread_bytes, _thread_scheduled, _thread_overflow
    for _ in range(MAX_MESSAGES):
        with _thread_lock:
            overflow = _thread_overflow
            _thread_overflow = False
            message = _thread_events.popleft() if _thread_events else None
            if message is None:
                _thread_scheduled = False
            else:
                _thread_bytes -= len(message)
        if overflow:
            _enqueue(json.dumps({"type": "resource", "message": "Capture events omitted: ingress limit exceeded."}))
        if message is None:
            return
        _enqueue(message)
    asyncio.get_running_loop().call_soon(_drain_thread_events)


def _get_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return _loop


def broadcast(event: dict) -> None:
    global _thread_bytes, _thread_scheduled, _thread_overflow
    loop = _get_loop()
    if loop is None:
        return
    message = _message(event)
    try:
        if asyncio.get_running_loop() is loop:
            _enqueue(message)
            return
    except RuntimeError:
        pass
    # Bound cross-thread ingress too; only one queued item schedules a drain.
    with _thread_lock:
        if len(_thread_events) >= MAX_MESSAGES or _thread_bytes + len(message) > MAX_QUEUE_BYTES:
            _thread_overflow = True
        else:
            _thread_events.append(message)
            _thread_bytes += len(message)
        if not _thread_scheduled:
            _thread_scheduled = True
            loop.call_soon_threadsafe(_drain_thread_events)


async def broadcast_async(event: dict) -> None:
    _enqueue(_message(event))


def log_info(msg: str, data: dict = {}) -> None:
    entry = {"level": "info", "msg": msg, "timestamp": int(time.time() * 1000), **data}
    print(json.dumps(entry), flush=True)


def log_error(msg: str, err: Exception | None = None) -> None:
    entry = {
        "level": "error",
        "msg": msg,
        "error": str(err) if err else None,
        "timestamp": int(time.time() * 1000),
    }
    print(json.dumps(entry), flush=True)


async def _ws_handler(websocket: ServerConnection) -> None:
    global _peers
    if _peers >= MAX_PEERS:
        await websocket.close(code=1013, reason="Capture connection capacity exceeded")
        return
    _peers += 1
    try:
        await _authenticated_handler(websocket)
    finally:
        _peers -= 1


async def _authenticated_handler(websocket: ServerConnection) -> None:
    # Never subscribe or emit captures before authentication. The token is a first
    # message rather than a query parameter, keeping it out of request logs.
    try:
        message = json.loads(await asyncio.wait_for(websocket.recv(), AUTH_TIMEOUT))
        if not isinstance(message, dict) or message.get("type") != "authenticate" or not security.valid_token(message.get("token")):
            raise ValueError("Invalid authentication")
    except Exception:
        await websocket.close(code=1008, reason="Authentication required")
        return
    if len(_outboxes) >= MAX_SUBSCRIBERS:
        await websocket.close(code=1013, reason="Capture subscriber limit reached")
        return
    outbox = Outbox()
    _outboxes[websocket] = outbox
    sender = None
    _clients.add(websocket)
    log_info("UI client connected", {"clientCount": len(_clients)})
    try:
        await asyncio.wait_for(websocket.send(json.dumps({"type": "authenticated", **cursor()})), SEND_TIMEOUT)
        sender = asyncio.create_task(_sender(websocket, outbox))
        async for _ in websocket:
            pass
    except Exception:
        pass
    finally:
        _outboxes.pop(websocket, None)
        _clients.discard(websocket)
        if sender:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender
        log_info("UI client disconnected", {"clientCount": len(_clients)})


def _check_handshake(connection, request):
    headers = request.headers
    if (len(headers.get_all("Host")) != 1 or not valid_host(headers.get("Host"))
            or len(headers.get_all("Origin")) > 1
            or not security.valid_origin(headers.get("Origin"))
            or request.path != "/"):
        return connection.respond(HTTPStatus.FORBIDDEN, "Control WebSocket request rejected\n")


async def start_logger(port: int = 4435):
    global _loop
    _loop = asyncio.get_running_loop()
    server = await serve(_ws_handler, BIND_HOST, port, process_request=_check_handshake,
                         max_size=4096, max_queue=4, compression=None, close_timeout=2)
    log_info("WebSocket log broadcaster started", {"port": port})
    return server
