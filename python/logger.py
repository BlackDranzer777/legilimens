"""
WebSocket broadcaster on :4435.
Provides broadcast(), log_info(), log_error() for other modules.
"""

import asyncio
import json
import time
from typing import Set

import websockets
from websockets.server import WebSocketServerProtocol

_clients: Set[WebSocketServerProtocol] = set()
_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return _loop


def broadcast(event: dict) -> None:
    message = json.dumps(event)
    loop = _get_loop()
    if loop is None:
        return
    for client in list(_clients):
        asyncio.run_coroutine_threadsafe(_safe_send(client, message), loop)


async def broadcast_async(event: dict) -> None:
    message = json.dumps(event)
    for client in list(_clients):
        asyncio.create_task(_safe_send(client, message))


async def _safe_send(client: WebSocketServerProtocol, message: str) -> None:
    try:
        await client.send(message)
    except Exception:
        _clients.discard(client)


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


async def _ws_handler(websocket: WebSocketServerProtocol) -> None:
    _clients.add(websocket)
    log_info("UI client connected", {"clientCount": len(_clients)})
    try:
        async for _ in websocket:
            pass
    except Exception:
        pass
    finally:
        _clients.discard(websocket)
        log_info("UI client disconnected", {"clientCount": len(_clients)})


async def start_logger(port: int = 4435):
    global _loop
    _loop = asyncio.get_running_loop()
    server = await websockets.serve(_ws_handler, "0.0.0.0", port)
    log_info("WebSocket log broadcaster started", {"port": port})
    return server
