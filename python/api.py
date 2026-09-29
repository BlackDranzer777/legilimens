"""
FastAPI control API on :4436.
API routes require a bearer token; static UI assets remain public.
"""

import asyncio
import base64
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from certs import get_cert_hash
from paths import ui_dir
from logger import log_info, log_error, broadcast_async
from control_security import BIND_HOST, ControlBoundary, security
import lifecycle

# ---------- shared state (imported and mutated by proxy.py) ----------

tamper_rule: dict = {
    "enabled": False,
    "field": "score",
    "value": "99999",
    "matchField": "",
    "matchValue": "",
}

capture_mode: str = "paused"

# active_sessions holds dicts {"client": ..., "server": ...}
# proxy.py registers/deregisters entries here
active_sessions: set = set()

# target_config is read by proxy.py to pick the upstream server
target_config: dict = {"host": "127.0.0.1", "port": 4434, "certHash": ""}

# The bundled practice target uses our own certificate, so its pin must follow
# certificate renewal. An explicitly chosen external target is user-configured and
# its pin is never overwritten by renewal.
bundled_target_port: int = 4434
target_user_configured: bool = False

# proxy.py sets this once it reads the cert hash on startup
_cert_hash_cache: str | None = None

# Manual intercept is separate from capture_mode:
#   capture_mode="paused" drops traffic at the proxy boundary.
#   manual_intercept["enabled"]=True holds individual messages for a decision.
manual_intercept: dict = {
    "enabled": False,
    "directions": ["incoming", "outgoing"],
    "types": ["datagram", "stream"],
    "timeoutMs": 30000,
}

# interceptId -> public item dict. Futures are stored separately so API responses never
# try to serialize asyncio internals.
pending_intercepts: dict[str, dict] = {}
_pending_futures: dict[str, asyncio.Future] = {}
MAX_PENDING_INTERCEPTS = 256
MAX_PENDING_BYTES = 4 * 1024 * 1024
MAX_INTERCEPT_PAYLOAD_BYTES = 256 * 1024


def set_cert_hash(h: str) -> None:
    global _cert_hash_cache
    _cert_hash_cache = h
    # Keep the bundled target's pin current across renewal; leave an external
    # user-configured target's pin untouched.
    if not target_user_configured:
        target_config["certHash"] = h


def _is_bundled_target(host: str, port: int) -> bool:
    return host.strip() in ("127.0.0.1", "localhost") and port == bundled_target_port


# Backend registers its coordinated-shutdown entry point so an authenticated
# launcher can request a graceful stop (cross-platform; Windows has no graceful
# signal for a console child).
_shutdown_fn = None


def register_shutdown_fn(fn) -> None:
    global _shutdown_fn
    _shutdown_fn = fn


# ---------- manual intercept helpers (called by proxy.py) ----------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _public_intercept(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "future" and not k.startswith("_")}


def _pending_payload_bytes():
    return sum(len(item["payload"].encode("utf-8")) + item.get("_decisionBytes", 0)
               for item in pending_intercepts.values())


def _manual_config_response() -> dict:
    return {
        **manual_intercept,
        "pending": len(pending_intercepts),
    }


def should_manual_intercept(direction: str, message_type: str) -> bool:
    return (
        bool(manual_intercept["enabled"])
        and direction in manual_intercept["directions"]
        and message_type in manual_intercept["types"]
    )


async def await_manual_intercept(
    *,
    session_id: str,
    direction: str,
    message_type: str,
    payload: str,
    raw_size: int,
    stream_id: str | None = None,
    payload_encoding: str = "utf8",
) -> dict:
    """Hold one message until the UI/API decides to forward/drop it.

    Returns {"action": "forward"|"drop", "payload": str, "status": str, "interceptId": str|None}.
    """
    if not should_manual_intercept(direction, message_type):
        return {"action": "forward", "payload": payload, "status": "bypassed", "interceptId": None}

    payload_bytes = len(payload.encode("utf-8"))
    if (len(pending_intercepts) >= MAX_PENDING_INTERCEPTS
            or payload_bytes > MAX_INTERCEPT_PAYLOAD_BYTES
            or _pending_payload_bytes() + payload_bytes > MAX_PENDING_BYTES):
        await broadcast_async({"type": "resource", "message": "Intercept capacity exceeded: matching message dropped, not forwarded."})
        return {"action": "drop", "payload": payload, "status": "capacity", "interceptId": None}

    intercept_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    item = {
        "id": intercept_id,
        "timestamp": _now_ms(),
        "sessionId": session_id,
        "direction": direction,
        "messageType": message_type,
        "payload": payload,
        "payloadEncoding": payload_encoding,
        "rawSize": raw_size,
        "streamId": stream_id,
    }
    pending_intercepts[intercept_id] = item
    _pending_futures[intercept_id] = fut

    await broadcast_async({
        "id": str(uuid.uuid4()),
        "timestamp": _now_ms(),
        "type": "intercept",
        "status": "pending",
        "interceptId": intercept_id,
        "direction": direction,
        "messageType": message_type,
        "payload": payload,
        "payloadEncoding": payload_encoding,
        "rawSize": raw_size,
        "size": raw_size,
        "latency": 0,
        "flag": "normal",
        **({"streamId": stream_id} if stream_id else {}),
    })

    timeout_s = max(1, int(manual_intercept["timeoutMs"])) / 1000
    try:
        decision = await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        decision = {"action": "forward", "payload": payload, "status": "timeout"}
    finally:
        pending_intercepts.pop(intercept_id, None)
        _pending_futures.pop(intercept_id, None)

    action = decision.get("action", "forward")
    resolved_payload = str(decision.get("payload", payload))
    status = decision.get("status") or ("dropped" if action == "drop" else "forwarded")
    await broadcast_async({
        "id": str(uuid.uuid4()),
        "timestamp": _now_ms(),
        "type": "intercept",
        "status": status,
        "interceptId": intercept_id,
        "direction": direction,
        "messageType": message_type,
        "payload": resolved_payload,
        "rawSize": raw_size,
        "size": raw_size,
        "latency": 0,
        "flag": "tampered" if resolved_payload != payload else "normal",
        **({"streamId": stream_id} if stream_id else {}),
    })
    return {
        "action": action,
        "payload": resolved_payload,
        "status": status,
        "interceptId": intercept_id,
    }


def _resolve_pending(intercept_id: str, action: str, payload: str | None = None) -> bool:
    fut = _pending_futures.get(intercept_id)
    item = pending_intercepts.get(intercept_id)
    if fut is None or item is None or fut.done():
        return False
    if payload is not None and len(payload.encode("utf-8")) > MAX_INTERCEPT_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="edited intercept payload exceeds size limit")
    decision_bytes = len(payload.encode("utf-8")) if payload is not None else 0
    if _pending_payload_bytes() + decision_bytes > MAX_PENDING_BYTES:
        raise HTTPException(status_code=413, detail="edited payload exceeds pending intercept byte budget")
    if action == "forward" and payload is not None and item.get("payloadEncoding") == "base64":
        try:
            base64.b64decode(payload, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="binary payload must be valid base64")
    item["_decisionBytes"] = decision_bytes
    fut.set_result({
        "action": action,
        "payload": item["payload"] if payload is None else payload,
        "status": "dropped" if action == "drop" else "forwarded",
    })
    return True


def _forward_all_pending() -> int:
    count = 0
    for intercept_id in list(pending_intercepts.keys()):
        if _resolve_pending(intercept_id, "forward"):
            count += 1
    return count


# ---------- disconnect callback (set by proxy.py) ----------

_disconnect_all_fn = None


def register_disconnect_fn(fn) -> None:
    global _disconnect_all_fn
    _disconnect_all_fn = fn


# ---------- replay callback (set by proxy.py) ----------

_replay_fn = None


def register_replay_fn(fn) -> None:
    global _replay_fn
    _replay_fn = fn


# ---------- app ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=security.origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(ControlBoundary)


# ---------- /health ----------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "legilimens", "instanceId": lifecycle.INSTANCE_ID,
            "certHash": _cert_hash_cache or "",
            "wsPort": security.ws_port, "proxyPort": security.proxy_port}


# ---------- /cert-hash ----------

@app.post("/shutdown")
async def shutdown_endpoint():
    """Authenticated graceful stop, used by the launcher on quit."""
    if _shutdown_fn is None:
        raise HTTPException(status_code=503, detail="shutdown is not available")
    await _shutdown_fn("control API shutdown request")
    return {"status": "stopping"}


@app.get("/cert-hash")
async def cert_hash():
    h = _cert_hash_cache or get_cert_hash()
    if h is None:
        raise HTTPException(status_code=503, detail="Certificate not generated yet. Run: python certs.py")
    return {"hash": h}


# ---------- /tamper ----------

@app.get("/tamper")
async def get_tamper():
    return tamper_rule


class TamperBody(BaseModel):
    enabled: bool | None = None
    field: str | None = None
    value: str | None = None
    matchField: str | None = None
    matchValue: str | None = None


@app.post("/tamper")
async def post_tamper(body: TamperBody):
    global tamper_rule
    tamper_rule = {
        "enabled": bool(body.enabled) if body.enabled is not None else tamper_rule["enabled"],
        "field": body.field.strip() if isinstance(body.field, str) else tamper_rule["field"],
        "value": str(body.value) if body.value is not None else tamper_rule["value"],
        "matchField": body.matchField.strip() if isinstance(body.matchField, str) else tamper_rule["matchField"],
        "matchValue": str(body.matchValue) if body.matchValue is not None else tamper_rule["matchValue"],
    }
    log_info("Tamper rule changed", tamper_rule)
    return tamper_rule


# ---------- /intercept ----------

@app.get("/intercept")
async def get_intercept():
    return {"captureMode": capture_mode, "activeSessions": len(active_sessions)}


class InterceptBody(BaseModel):
    action: str


@app.post("/intercept")
async def post_intercept(body: InterceptBody):
    global capture_mode
    action = body.action

    if action in ("start", "resume"):
        capture_mode = "capturing"
    elif action == "pause":
        capture_mode = "paused"
    elif action == "disconnect":
        if _disconnect_all_fn is not None:
            await _disconnect_all_fn()
        active_sessions.clear()
        capture_mode = "paused"
    else:
        raise HTTPException(status_code=400, detail="action must be 'start', 'pause', or 'disconnect'")

    log_info("Intercept changed", {"action": action, "captureMode": capture_mode, "active": len(active_sessions)})
    return {"captureMode": capture_mode, "activeSessions": len(active_sessions)}


# ---------- /intercept/manual ----------

@app.get("/intercept/manual")
async def get_manual_intercept():
    return _manual_config_response()


class ManualInterceptBody(BaseModel):
    enabled: bool | None = None
    directions: list[str] | None = None
    types: list[str] | None = None
    timeoutMs: int | None = None


@app.post("/intercept/manual")
async def post_manual_intercept(body: ManualInterceptBody):
    valid_directions = {"incoming", "outgoing"}
    valid_types = {"datagram", "stream"}
    next_config = dict(manual_intercept)

    if body.directions is not None:
        directions = list(dict.fromkeys(body.directions))
        if not directions or any(d not in valid_directions for d in directions):
            raise HTTPException(status_code=400, detail="directions must include incoming and/or outgoing")
        next_config["directions"] = directions

    if body.types is not None:
        types = list(dict.fromkeys(body.types))
        if not types or any(t not in valid_types for t in types):
            raise HTTPException(status_code=400, detail="types must include datagram and/or stream")
        next_config["types"] = types

    if body.timeoutMs is not None:
        if not (1000 <= body.timeoutMs <= 300000):
            raise HTTPException(status_code=400, detail="timeoutMs must be between 1000 and 300000")
        next_config["timeoutMs"] = int(body.timeoutMs)

    forwarded = 0
    if body.enabled is not None:
        next_config["enabled"] = bool(body.enabled)

    # Commit only after every field passes validation; no await splits this update.
    manual_intercept.update(next_config)
    if body.enabled is False:
        forwarded = _forward_all_pending()

    log_info("Manual intercept changed", {**manual_intercept, "autoForwarded": forwarded})
    return {**_manual_config_response(), "autoForwarded": forwarded}


@app.get("/intercept/queue")
async def get_intercept_queue():
    return {
        "items": sorted(
            (_public_intercept(item) for item in pending_intercepts.values()),
            key=lambda item: item["timestamp"],
        )
    }


class InterceptDecisionBody(BaseModel):
    action: str
    payload: str | None = None


@app.post("/intercept/{intercept_id}/decision")
async def post_intercept_decision(intercept_id: str, body: InterceptDecisionBody):
    action = body.action.strip().lower()
    if action not in ("forward", "drop"):
        raise HTTPException(status_code=400, detail="action must be 'forward' or 'drop'")
    if not _resolve_pending(intercept_id, action, body.payload):
        raise HTTPException(status_code=404, detail="unknown or already resolved interceptId")
    return {"interceptId": intercept_id, "status": "accepted", "action": action}


# ---------- /target ----------

@app.get("/target")
async def get_target():
    return target_config


class TargetBody(BaseModel):
    host: str
    port: int
    certHash: str | None = None


@app.post("/target")
async def post_target(body: TargetBody):
    global target_config

    if not body.host.strip():
        raise HTTPException(status_code=400, detail="host is required")
    if not (1 <= body.port <= 65535):
        raise HTTPException(status_code=400, detail="port must be an integer 1–65535")

    cert_hash_val = ""
    if body.certHash and body.certHash.strip():
        h = body.certHash.strip()
        try:
            raw = base64.b64decode(h, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="cert hash is not valid base64")
        if len(raw) != 32:
            raise HTTPException(status_code=400, detail="cert hash must be a base64 SHA-256 (32 bytes)")
        cert_hash_val = h

    global target_user_configured
    if _is_bundled_target(body.host, body.port) and not cert_hash_val:
        # Returning to the bundled practice target: re-enable automatic pinning so
        # its pin follows certificate renewal.
        target_user_configured = False
        cert_hash_val = _cert_hash_cache or ""
    else:
        target_user_configured = True

    target_config = {"host": body.host.strip(), "port": body.port, "certHash": cert_hash_val}
    log_info("Upstream target changed", {"host": target_config["host"], "port": target_config["port"],
                                         "pinned": bool(cert_hash_val), "userConfigured": target_user_configured})
    return target_config


# ---------- /replay (Repeater: resend an edited message) ----------

@app.get("/sessions")
async def get_sessions():
    return {"items": sorted(
        ({"id": s.session_uuid, "target": s.target_label} for s in active_sessions if s.ready_for_replay),
        key=lambda item: item["id"],
    )}

class ReplayBody(BaseModel):
    payload: str
    direction: str
    messageType: str
    sessionId: str


@app.post("/replay")
async def post_replay(body: ReplayBody):
    if len(body.payload.encode("utf-8")) > 1024:
        raise HTTPException(status_code=413, detail="Replay datagram exceeds the 1024-byte safety limit")
    if not body.sessionId.strip():
        raise HTTPException(status_code=400, detail="sessionId is required")
    if body.direction not in ("incoming", "outgoing"):
        raise HTTPException(status_code=400, detail="direction must be 'incoming' or 'outgoing'")
    if body.messageType not in ("datagram", "stream"):
        raise HTTPException(status_code=400, detail="messageType must be 'datagram' or 'stream'")
    if _replay_fn is None:
        raise HTTPException(status_code=503, detail="proxy not ready")

    result = await _replay_fn(body.direction, body.messageType, body.payload, body.sessionId)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "replay failed"))
    return result


# ---------- /attack (lifecycle of the attacks/ modules) ----------

@app.get("/state")
async def get_state():
    import attack_runner
    from logger import cursor
    # No await: the snapshot and cursor are read in one event-loop turn.
    attacks = attack_runner.list_attacks()
    running = [a for a in attacks if a["status"] not in attack_runner._TERMINAL]
    completed = sorted((a for a in attacks if a["status"] in attack_runner._TERMINAL),
                       key=lambda a: a["completedAt"] or 0, reverse=True)[:20]
    return {**cursor(), "captureMode": capture_mode, "manualIntercept": dict(manual_intercept),
            "tamperEnabled": tamper_rule["enabled"],
            "pendingIntercepts": [{**_public_intercept(item), "interceptId": item["id"]} for item in pending_intercepts.values()],
            "attacks": [{**a, "attackType": a["type"]} for a in running + completed]}

class AttackBody(BaseModel):
    type: str
    target: str = "https://127.0.0.1:4434"
    params: dict = {}


@app.post("/attack")
async def post_attack(body: AttackBody):
    # Imported lazily so api.py stays importable even if an attack dep is missing.
    import attack_runner
    if body.type not in attack_runner.ATTACK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown attack type '{body.type}'. Valid: {sorted(attack_runner.ATTACK_TYPES)}",
        )
    try:
        attack_id = attack_runner.start_attack(body.type, body.target, body.params or {})
    except attack_runner.AttackCapacityError as error:
        raise HTTPException(status_code=429, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"attackId": attack_id, "status": "started", "type": body.type}


@app.get("/attack/{attack_id}/status")
async def get_attack(attack_id: str):
    import attack_runner
    status = attack_runner.get_attack_status(attack_id)
    if status is None:
        raise HTTPException(status_code=404, detail="unknown attackId")
    return status


@app.post("/attack/{attack_id}/stop")
async def stop_attack(attack_id: str):
    import attack_runner
    try:
        ok = await attack_runner.stop_attack_task(attack_id)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Cancellation cleanup is still pending; run remains tracked")
    if not ok:
        raise HTTPException(status_code=404, detail="unknown attackId")
    return attack_runner.get_attack_status(attack_id)


# ---------- static UI ----------
# Serve the built React app at "/" so the packaged Electron shell can load the
# inspector from http://localhost:<api-port> (a secure context, which WebTransport
# requires). Mounted LAST so every API route above still takes precedence. In dev,
# client/dist may not exist — the Vite dev server serves the UI instead, so skip.
_ui = ui_dir()
if (_ui / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_ui), html=True), name="ui")
    log_info("Serving bundled UI", {"dir": str(_ui)})


def make_api_server(port: int = 4436) -> "uvicorn.Server":
    """Build the uvicorn Server without starting it, so the caller can supervise
    its lifecycle (Server.started for readiness, Server.should_exit for shutdown)."""
    # lifespan="off": the app's lifespan is a no-op, and skipping the ASGI lifespan
    # task avoids a benign CancelledError traceback from uvicorn during shutdown.
    config = uvicorn.Config(app, host=BIND_HOST, port=port, log_level="warning", proxy_headers=False,
                            lifespan="off", limit_concurrency=64, backlog=128, timeout_keep_alive=5)
    return uvicorn.Server(config)


async def start_api(port: int = 4436):
    server = make_api_server(port)
    log_info("HTTP API server started", {"port": port})
    await server.serve()
