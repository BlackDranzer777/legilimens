"""Build the real upstream Dioxus/WASM UI (+ WebCodecs/NetEq workers) with Trunk in
an isolated Linux image, and export only the built static assets to the host.

Upstream source stays unchanged (built from a git archive of the pinned commit, not
the working checkout). Output: build/videocall-frontend/dist/ (gitignored).

    python interop/videocall/build_frontend.py
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from preflight import ROOT, MANIFEST, inspect_source


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checkout = ROOT / manifest["checkout"]
    inspect_source(checkout, manifest)

    context = ROOT / "build/videocall-build"
    context.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-c", f"safe.directory={checkout.as_posix()}",
                    "-C", str(checkout), "archive", "--format=tar",
                    f"--output={context / 'source.tar'}", manifest["commit"]], check=True)
    shutil.copyfile(Path(__file__).with_name("Dockerfile"), context / "Dockerfile")

    env = {**os.environ, "DOCKER_BUILDKIT": "1"}
    image = "legilimens-videocall:31a8b207-frontend"
    # Build the frontend stage as a tagged image (layers cache), then copy the built
    # dist/ out with docker cp — more robust than a BuildKit host export to OneDrive.
    subprocess.run(["docker", "build", "--progress=plain", "--target", "frontend",
                    "-t", image, str(context)], check=True, env=env)

    dist = ROOT / "build/videocall-frontend/dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True, exist_ok=True)
    cid = subprocess.run(["docker", "create", image], capture_output=True, text=True,
                         check=True).stdout.strip()
    try:
        subprocess.run(["docker", "cp", f"{cid}:/src/dioxus-ui/dist/.", str(dist)], check=True)
    finally:
        subprocess.run(["docker", "rm", "-f", cid], check=False)
    print(f"dist exported to: {dist}")
    print("index.html present:", (dist / "index.html").exists())


if __name__ == "__main__":
    main()
