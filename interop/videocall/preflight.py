"""Read-only source/runtime preflight. Never installs tools or starts services."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("target.json")


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=20, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} check failed (exit {result.returncode})")
    return result.stdout.strip()


def inspect_source(checkout, manifest):
    checkout = checkout.resolve()
    git = ["git", "-c", f"safe.directory={checkout.as_posix()}", "-C", str(checkout)]
    commit = command([*git, "rev-parse", "HEAD"])
    if commit != manifest["commit"]:
        raise ValueError(f"Wrong source revision: expected {manifest['commit']}, got {commit}")
    if command([*git, "status", "--porcelain", "--untracked-files=normal"]):
        raise ValueError("Target checkout has changes; preserve them and inspect before running")
    missing = [name for name in manifest["requiredFiles"] if not (checkout / name).is_file()]
    if missing:
        raise ValueError("Missing target source files: " + ", ".join(missing))
    return {"verified": True, "commit": commit, "checkout": str(checkout)}


def inspect_docker():
    try:
        server = json.loads(command(["docker", "version", "--format", "{{json .Server}}"] ))
        if not isinstance(server, dict) or server.get("Os") != "linux":
            return {"available": False, "reason": "Linux Docker engine not available"}
        return {"available": True, "version": server.get("Version"), "os": server["Os"]}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--check-docker", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report = {"target": "videocall.rs", "runtimeVerified": False,
              "compatibility": "not confirmed", "servicesStarted": False}
    try:
        report["source"] = inspect_source(args.checkout or ROOT / manifest["checkout"], manifest)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        report["source"] = {"verified": False, "error": str(exc)}
    if args.check_docker:
        report["docker"] = inspect_docker()
    report["pending"] = ["isolated Linux runtime and pinned dependencies", "local certificates and auth",
                         "direct baseline", "proxied transport with fallback rejected", "two-browser media workflow"]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    # Success means source preflight only, never application compatibility.
    return 0 if report["source"]["verified"] and (not args.check_docker or report["docker"]["available"]) else 1


if __name__ == "__main__":
    sys.exit(main())
