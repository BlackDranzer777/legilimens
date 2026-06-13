"""
Attack lifecycle manager.

Runs each attack from attacks/ as a background asyncio task, tracks its state, and
broadcasts progress/terminal events to the logger WebSocket (:4435) so the UI sees
live updates on the same channel as traffic logs.

Attack WS event shape (type:"attack"):
{
  "id": uuid, "timestamp": ms, "type": "attack",
  "attackId": uuid, "attackType": "flooding",
  "status": "running" | "complete" | "failed",
  "progress": {"current": int, "total": int, "message": str},   # while running
  "result": {...},   # on complete
  "error": str       # on failed
}
"""

import asyncio
import time
import uuid

from attacks import encapsulation, flooding, fuzz, loris, out_of_joint
from logger import broadcast_async, log_error, log_info

# attack type -> async run(target_url, params, progress_callback) -> dict
_RUNNERS = {
    "flooding": flooding.run,
    "loris": loris.run,
    "fuzz": fuzz.run,
    "out_of_joint": out_of_joint.run,
    "encapsulation": encapsulation.run,
}

ATTACK_TYPES = set(_RUNNERS.keys())

# attackId -> record dict
_attacks: dict[str, dict] = {}


def _now() -> int:
    return int(time.time() * 1000)


def _emit(event: dict) -> None:
    """Schedule a WebSocket broadcast from sync context (progress callbacks)."""
    try:
        asyncio.get_running_loop()
        asyncio.create_task(broadcast_async(event))
    except RuntimeError:
        pass  # no running loop — drop


def _make_progress_cb(attack_id: str, attack_type: str):
    def cb(current: int, total: int, message: str) -> None:
        rec = _attacks.get(attack_id)
        if rec is not None:
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


async def _run_attack(attack_id: str, attack_type: str, run_fn, target: str, params: dict):
    rec = _attacks[attack_id]
    rec["status"] = "running"
    cb = _make_progress_cb(attack_id, attack_type)
    await broadcast_async({
        "id": str(uuid.uuid4()), "timestamp": _now(), "type": "attack",
        "attackId": attack_id, "attackType": attack_type, "status": "running",
        "progress": {"current": 0, "total": 0, "message": "starting"},
    })
    try:
        result = await run_fn(target, params, cb)
        rec["status"] = "complete"
        rec["result"] = result
        rec["completedAt"] = _now()
        await broadcast_async({
            "id": str(uuid.uuid4()), "timestamp": _now(), "type": "attack",
            "attackId": attack_id, "attackType": attack_type, "status": "complete",
            "result": result,
        })
        log_info("Attack complete", {"attackId": attack_id, "type": attack_type})
    except asyncio.CancelledError:
        rec["status"] = "stopped"
        rec["completedAt"] = _now()
        await broadcast_async({
            "id": str(uuid.uuid4()), "timestamp": _now(), "type": "attack",
            "attackId": attack_id, "attackType": attack_type, "status": "failed",
            "error": "stopped by user",
        })
        log_info("Attack stopped", {"attackId": attack_id, "type": attack_type})
        # Swallow — cancellation is intentional.
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = str(e)
        rec["completedAt"] = _now()
        await broadcast_async({
            "id": str(uuid.uuid4()), "timestamp": _now(), "type": "attack",
            "attackId": attack_id, "attackType": attack_type, "status": "failed",
            "error": str(e),
        })
        log_error(f"Attack failed ({attack_type})", e)


def start_attack(attack_type: str, target: str, params: dict) -> str:
    """Create + schedule an attack. Returns its attackId immediately (non-blocking)."""
    if attack_type not in _RUNNERS:
        raise ValueError(f"unknown attack type: {attack_type}")
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


async def stop_attack_task(attack_id: str) -> bool:
    rec = _attacks.get(attack_id)
    if rec is None:
        return False
    task = rec.get("task")
    if task is not None and not task.done():
        task.cancel()
    return True
