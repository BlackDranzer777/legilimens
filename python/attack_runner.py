"""
Attack lifecycle manager.

Runs each attack from attacks/ as a background asyncio task, tracks its state, and
broadcasts progress/terminal events to the logger WebSocket (:4435) so the UI sees
live updates on the same channel as traffic logs.

Attack WS event shape (type:"attack"):
{
  "id": uuid, "timestamp": ms, "type": "attack",
  "attackId": uuid, "attackType": "flooding",
  "status": "running" | "complete" | "failed" | "stopped",
  "progress": {"current": int, "total": int, "message": str},   # while running
  "result": {...},   # on complete
  "error": str       # on failed
}
"""

import asyncio
import time
import uuid
import json
import math
from urllib.parse import urlsplit

from attacks import encapsulation, flooding, loris
from logger import broadcast, broadcast_async, log_error, log_info

# attack type -> async run(target_url, params, progress_callback) -> dict
_RUNNERS = {
    "flooding": flooding.run,
    "loris": loris.run,
    "encapsulation": encapsulation.run,
}

ATTACK_TYPES = set(_RUNNERS.keys())

# attackId -> record dict
_attacks: dict[str, dict] = {}
_TERMINAL = {"complete", "failed", "stopped"}
MAX_ACTIVE_ATTACKS = 4
MAX_ATTACK_HISTORY = 100
STOP_TIMEOUT = 5
MAX_RUN_SECONDS = 300
MAX_RESULT_BYTES = 65536
PARAM_LIMITS = {
    "flooding": {"connections": (1, 128, 100)},
    "loris": {"connections": (1, 128, 100), "cycles": (1, 10, 3), "cycleDelay": (0, 60, 30)},
    "encapsulation": {"packets": (1, 1000, 100)},
}


def validate_run(attack_type, target, params):
    url = urlsplit(target)
    if (len(target) > 2048 or url.scheme != "https" or not url.hostname or url.username
            or url.password or url.fragment or (url.port is not None and not 1 <= url.port <= 65535)):
        raise ValueError("target must be an HTTPS URL without credentials or fragment")
    limits = PARAM_LIMITS.get(attack_type, {})
    if set(params) - set(limits):
        raise ValueError("unsupported attack parameter")
    values = {}
    for key, (minimum, maximum, default) in limits.items():
        value = params.get(key, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or not minimum <= value <= maximum or (key != "cycleDelay" and not isinstance(value, int))):
            raise ValueError(f"{key} must be {'a number' if key == 'cycleDelay' else 'an integer'} between {minimum} and {maximum}")
        values[key] = value
    if attack_type == "loris" and values["connections"] * values["cycles"] > 1024:
        raise ValueError("loris total connections must not exceed 1024")
    return values


class AttackCapacityError(ValueError):
    pass


def _now() -> int:
    return int(time.time() * 1000)


def _emit(event: dict) -> None:
    broadcast(event)


def _make_progress_cb(attack_id: str, attack_type: str):
    def cb(current: int, total: int, message: str) -> None:
        message = str(message)[:512]
        rec = _attacks.get(attack_id)
        if rec is None or rec["status"] in _TERMINAL or rec.get("stopRequested"):
            return
        rec["progress"] = {"current": current, "total": total, "message": message}
        _emit({
            "id": str(uuid.uuid4()),
            "timestamp": _now(),
            "type": "attack",
            "attackId": attack_id,
            "attackType": attack_type,
            "status": "running",
            "progress": {"current": current, "total": total, "message": message},
        })
    return cb


def _finish(attack_id: str, status: str, *, result=None, error=None):
    rec = _attacks[attack_id]
    if rec["status"] in _TERMINAL:
        return
    if result is not None:
        size = 0
        for chunk in json.JSONEncoder().iterencode(result):
            size += len(chunk)
            if size > MAX_RESULT_BYTES:
                result = {"omitted": True, "reason": "Result size limit exceeded"}
                break
    error = str(error)[:1024] if error is not None else None
    rec.update(status=status, completedAt=_now(), result=result, error=error)
    _emit({
        "id": str(uuid.uuid4()), "timestamp": _now(), "type": "attack",
        "attackId": attack_id, "attackType": rec["type"], "status": status,
        "result": result, "error": error,
    })


async def _run_attack(attack_id: str, attack_type: str, run_fn, target: str, params: dict):
    rec = _attacks[attack_id]
    rec["status"] = "running"
    cb = _make_progress_cb(attack_id, attack_type)
    try:
        await broadcast_async({
            "id": str(uuid.uuid4()), "timestamp": _now(), "type": "attack",
            "attackId": attack_id, "attackType": attack_type, "status": "running",
            "progress": {"current": 0, "total": 0, "message": "starting"},
        })
        async with asyncio.timeout(MAX_RUN_SECONDS):
            result = await run_fn(target, params, cb)
        _finish(attack_id, "complete", result=result)
        log_info("Attack complete", {"attackId": attack_id, "type": attack_type})
    except asyncio.CancelledError:
        _finish(attack_id, "stopped")
        log_info("Attack stopped", {"attackId": attack_id, "type": attack_type})
        # Swallow — cancellation is intentional.
    except TimeoutError:
        _finish(attack_id, "failed", error="Run exceeded its execution deadline")
    except Exception as e:
        _finish(attack_id, "failed", error=str(e))
        log_error(f"Attack failed ({attack_type})", e)


def start_attack(attack_type: str, target: str, params: dict) -> str:
    """Create + schedule an attack. Returns its attackId immediately (non-blocking)."""
    if attack_type not in _RUNNERS:
        raise ValueError(f"unknown attack type: {attack_type}")
    params = validate_run(attack_type, target, params)
    if sum(1 for rec in _attacks.values() if not rec["task"].done()) >= MAX_ACTIVE_ATTACKS:
        raise AttackCapacityError("concurrent attack limit reached")
    # Retain bounded terminal history without evicting tasks still cleaning up.
    for aid in list(_attacks):
        if len(_attacks) < MAX_ATTACK_HISTORY:
            break
        if _attacks[aid]["task"].done():
            del _attacks[aid]
    attack_id = str(uuid.uuid4())
    _attacks[attack_id] = {
        "attackId": attack_id,
        "type": attack_type,
        "status": "started",
        "startedAt": _now(),
        "completedAt": None,
        "result": None,
        "error": None,
        "progress": None,
        "task": None,
    }
    task = asyncio.create_task(_run_attack(attack_id, attack_type, _RUNNERS[attack_type], target, params or {}))
    _attacks[attack_id]["task"] = task
    # Cancellation before the coroutine's first step cannot enter its try/except.
    task.add_done_callback(lambda done: _finish(attack_id, "stopped") if done.cancelled() else None)
    log_info("Attack started", {"attackId": attack_id, "type": attack_type, "target": target})
    return attack_id


def get_attack_status(attack_id: str) -> dict | None:
    rec = _attacks.get(attack_id)
    if rec is None:
        return None
    return {
        "attackId": rec["attackId"],
        "type": rec["type"],
        "status": rec["status"],
        "startedAt": rec["startedAt"],
        "completedAt": rec["completedAt"],
        "progress": rec["progress"],
        "result": rec["result"],
        "error": rec["error"],
    }


def list_attacks() -> list[dict]:
    return [get_attack_status(aid) for aid in _attacks]


async def cancel_all() -> None:
    """Cancel every still-running attack (used during coordinated shutdown)."""
    running = [aid for aid, rec in list(_attacks.items())
               if rec.get("task") is not None and not rec["task"].done()]
    for aid in running:
        rec = _attacks.get(aid)
        if rec and not rec["task"].done() and not rec.get("stopRequested"):
            rec["stopRequested"] = True
            rec["task"].cancel()
    for aid in running:
        rec = _attacks.get(aid)
        task = rec.get("task") if rec else None
        if task is None:
            continue
        try:
            await asyncio.wait_for(asyncio.shield(task), STOP_TIMEOUT)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
        except asyncio.TimeoutError:
            pass


async def stop_attack_task(attack_id: str) -> bool:
    rec = _attacks.get(attack_id)
    if rec is None:
        return False
    task = rec.get("task")
    if task is not None and rec["status"] not in _TERMINAL:
        if not task.done() and not rec.get("stopRequested"):
            rec["stopRequested"] = True
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), STOP_TIMEOUT)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            _finish(attack_id, "stopped")
    return True
