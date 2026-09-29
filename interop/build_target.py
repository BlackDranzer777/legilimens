"""Build the independent Go WebTransport target (interop/target) reproducibly.

Uses a portable Go under .tooling/go if present, otherwise `go` on PATH. Module
and build caches go to a temp directory so nothing lands in the repo. The pinned
dependency versions live in interop/target/go.mod / go.sum.

    python interop/build_target.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WIN = os.name == "nt"


def find_go():
    portable = ROOT / ".tooling" / "go" / "bin" / ("go.exe" if IS_WIN else "go")
    if portable.exists():
        return str(portable), str(ROOT / ".tooling" / "go")
    exe = shutil.which("go")
    return (exe, None) if exe else (None, None)


def main():
    go, goroot = find_go()
    if not go:
        print("Go toolchain not found (.tooling/go/bin or PATH). See doc/INTEROPERABILITY.md "
              "for how to obtain a pinned Go release.", file=sys.stderr)
        return 1
    cache = Path(os.environ.get("INTEROP_GOCACHE", tempfile.gettempdir())) / "legilimens-interop-go"
    env = {**os.environ, "GOPATH": str(cache / "gopath"), "GOMODCACHE": str(cache / "mod"),
           "GOCACHE": str(cache / "build"), "GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": "local"}
    if goroot:
        env["GOROOT"] = goroot
    tdir = ROOT / "interop" / "target"
    out = tdir / ("interop-target.exe" if IS_WIN else "interop-target")
    print(f"go: {go}\nbuilding: {out}")
    # This test fixture is versioned by go.mod/go.sum, not the parent app's Git state.
    rc = subprocess.run([go, "build", "-buildvcs=false", "-o", str(out), "."], cwd=str(tdir), env=env).returncode
    print("build ok" if rc == 0 else "build failed")
    return rc


if __name__ == "__main__":
    sys.exit(main())
