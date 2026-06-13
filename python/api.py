"""
FastAPI control API on :4436.
Preserves the exact HTTP contract that the React UI expects.
"""

import base64
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


def set_cert_hash(h: str) -> None:
    global _cert_hash_cache, target_config
    _cert_hash_cache = h
    if not target_config["certHash"]:
        target_config["certHash"] = h


# ---------- disconnect callback (set by proxy.py) ----------

_disconnect_all_fn = None


def register_disconnect_fn(fn) -> None:
    global _disconnect_all_fn
    _disconnect_all_fn = fn


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
