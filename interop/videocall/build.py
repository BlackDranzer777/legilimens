"""Build unchanged pinned upstream sources in an isolated Linux image."""
import json
from pathlib import Path
import shutil
import subprocess

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
    subprocess.run(["docker", "build", "--progress=plain", "--target", "backend",
                    "-t", "legilimens-videocall:31a8b207-backend", str(context)], check=True)


if __name__ == "__main__":
    main()
