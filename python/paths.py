"""
Path resolution that works in three modes:

  1. dev            — running from source (python python/backend.py)
  2. PyInstaller    — frozen backend .exe (sys.frozen / sys._MEIPASS set)
  3. Electron shell — Electron passes explicit dirs via env vars

Writable data (certs) must NOT live inside the install folder, which is
read-only once packaged (e.g. Program Files). Read-only data (the built UI)
is bundled next to the frozen exe.
"""

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def data_dir() -> Path:
    """User-writable directory for runtime data (certs, future exports)."""
    override = os.environ.get("LEGILIMENS_DATA_DIR")
    if override:
        d = Path(override)
    elif _is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        d = Path(base) / "Legilimens"
    else:
        # dev: keep the historical location (python/) so nothing changes locally
        d = Path(__file__).resolve().parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def certs_dir() -> Path:
    """Where cert.pem / key.pem / cert-hash.txt live. Always writable."""
    override = os.environ.get("LEGILIMENS_CERTS_DIR")
    d = Path(override) if override else data_dir() / "certs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ui_dir() -> Path:
    """Directory of the built React UI (index.html + assets), read-only."""
    override = os.environ.get("LEGILIMENS_UI_DIR")
    if override:
        return Path(override)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "ui"
    # dev / repo layout: client/dist next to python/
    return Path(__file__).resolve().parent.parent / "client" / "dist"
