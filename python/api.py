"""
FastAPI control API on :4436.
Preserves the exact HTTP contract that the React UI expects.
"""

import asyncio
import base64
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from certs import get_cert_hash
from logger import log_info, log_error, broadcast_async

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


def set_cert_hash(h: str) -> None:
    global _cert_hash_cache, target_config
    _cert_hash_cache = h
    if not target_config["certHash"]:
        target_config["certHash"] = h


# ---------- manual intercept helpers (called by proxy.py) ----------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _public_intercept(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "future"}


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
) -> dict:
    """Hold one message until the UI/API decides to forward/drop it.

    Returns {"action": "forward"|"drop", "payload": str, "status": str, "interceptId": str|None}.
    """
    if not should_manual_intercept(direction, message_type):
        return {"action": "forward", "payload": payload, "status": "bypassed", "interceptId": None}

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


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- /health ----------

@app.get("/health")
async def health():
    return {"status": "ok", "proxy": "active", "certHash": _cert_hash_cache or ""}


# ---------- /cert-hash ----------

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
        "matchField": body.matchField.strip() if isinstance(body.matchField, str) else "",
        "matchValue": str(body.matchValue) if body.matchValue is not None else "",
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

    if body.directions is not None:
        directions = [d for d in body.directions if d in valid_directions]
        if not directions:
            raise HTTPException(status_code=400, detail="directions must include incoming and/or outgoing")
        manual_intercept["directions"] = directions

    if body.types is not None:
        types = [t for t in body.types if t in valid_types]
        if not types:
            raise HTTPException(status_code=400, detail="types must include datagram and/or stream")
        manual_intercept["types"] = types

    if body.timeoutMs is not None:
        if not (1000 <= body.timeoutMs <= 300000):
            raise HTTPException(status_code=400, detail="timeoutMs must be between 1000 and 300000")
        manual_intercept["timeoutMs"] = int(body.timeoutMs)

    forwarded = 0
    if body.enabled is not None:
        manual_intercept["enabled"] = bool(body.enabled)
        if not manual_intercept["enabled"]:
            # Avoid leaving protocol coroutines frozen if the user disables intercept.
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
            raw = base64.b64decode(h)
        except Exception:
            raise HTTPException(status_code=400, detail="cert hash is not valid base64")
        if len(raw) != 32:
            raise HTTPException(status_code=400, detail="cert hash must be a base64 SHA-256 (32 bytes)")
        cert_hash_val = h

    target_config = {"host": body.host.strip(), "port": body.port, "certHash": cert_hash_val}
    log_info("Upstream target changed", {"host": target_config["host"], "port": target_config["port"], "pinned": bool(cert_hash_val)})
    return target_config


# ---------- /replay (Repeater: resend an edited message) ----------

class ReplayBody(BaseModel):
    payload: str
    direction: str
    messageType: str


@app.post("/replay")
async def post_replay(body: ReplayBody):
    if body.direction not in ("incoming", "outgoing"):
        raise HTTPException(status_code=400, detail="direction must be 'incoming' or 'outgoing'")
    if body.messageType not in ("datagram", "stream"):
        raise HTTPException(status_code=400, detail="messageType must be 'datagram' or 'stream'")
    if _replay_fn is None:
        raise HTTPException(status_code=503, detail="proxy not ready")

    result = await _replay_fn(body.direction, body.messageType, body.payload)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "replay failed"))
    return result


# ---------- /attack (lifecycle of the attacks/ modules) ----------

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
    attack_id = attack_runner.start_attack(body.type, body.target, body.params or {})
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
    ok = await attack_runner.stop_attack_task(attack_id)
    if not ok:
        raise HTTPException(status_code=404, detail="unknown attackId")
    return {"status": "stopped"}


async def start_api(port: int = 4436):
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    log_info("HTTP API server started", {"port": port})
    await server.serve()
