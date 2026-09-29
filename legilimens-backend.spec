# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — freezes the Legilimens Python backend into a one-folder app
(dist/legilimens-backend/). The Electron shell spawns the produced .exe and
waits for its "READY" stdout line before opening the window.

Build from the repo root:
    .venv\\Scripts\\python.exe -m PyInstaller legilimens-backend.spec --noconfirm
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Packages with C extensions / data files PyInstaller's default analysis can miss.
for pkg in ("aioquic", "pylsqpack", "pydantic", "pydantic_core"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("websockets")
hiddenimports += ["anyio", "h11", "click"]

# Ship the built React UI; FastAPI serves it at runtime (paths.ui_dir -> _MEIPASS/ui).
datas += [(os.path.join("client", "dist"), "ui")]

a = Analysis(
    [os.path.join("python", "backend.py")],
    pathex=["python"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # scapy is imported lazily (only the encapsulation attack). Excluding it keeps
    # the bundle small and avoids Npcap/libpcap packaging; that one attack simply
    # reports its missing dependency at runtime, as it already does without Npcap.
    excludes=["scapy", "tkinter", "matplotlib", "PyQt5", "PySide2", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="legilimens-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console app: its stdout carries the "READY" handshake to Electron. Electron
    # spawns it with windowsHide:true so no console window appears to the user.
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="legilimens-backend",
)
