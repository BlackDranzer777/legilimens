"""Build a separately labelled upstream lifecycle experiment; never replace the baseline."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from preflight import ROOT, MANIFEST, inspect_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/videocall-frontend-lifecycle")
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to((ROOT / "build").resolve())
    if output.exists():
        parser.error("Use a new output directory; existing evidence is never replaced")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    inspect_source(ROOT / manifest["checkout"], manifest)
    context = Path(__file__).with_name("patches")
    patch = context / "lifecycle.patch"
    base_tag = "legilimens-videocall:31a8b207-frontend"
    base = subprocess.check_output(["docker", "image", "inspect", base_tag,
                                    "--format", "{{.Id}}"], text=True, timeout=20).strip()
    image = "legilimens-videocall:31a8b207-lifecycle"
    digests = {}
    for key, relative in (("TRANSPORT_SHA", "videocall-transport/src/webtransport.rs"),
                          ("WAITING_ROOM_SHA", "dioxus-ui/src/components/waiting_room.rs")):
        source = (ROOT / manifest["checkout"] / relative).read_bytes().replace(b"\r\n", b"\n")
        digests[key] = hashlib.sha256(source).hexdigest()
    subprocess.run(["docker", "build", "--progress=plain", "--build-arg", "BASE_IMAGE=" + base_tag,
                    *[arg for key, value in digests.items() for arg in ("--build-arg", f"{key}={value}")],
                    "-t", image, str(context)], check=True, timeout=1800)
    image_id = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                                       text=True, timeout=20).strip()
    output.mkdir(parents=True)
    cid = subprocess.check_output(["docker", "create", "--label", "org.legilimens.purpose=lifecycle-export",
                                   image_id], text=True, timeout=20).strip()
    try:
        subprocess.run(["docker", "cp", f"{cid}:/src/dioxus-ui/dist", str(output)], check=True, timeout=120)
    finally:
        subprocess.run(["docker", "rm", "-f", cid], check=True, timeout=30)
    record = {"variant": "experimental-upstream-lifecycle-patch", "commit": manifest["commit"],
              "baseImage": base, "image": image_id, "baseSourceSha256": digests,
              "patchSha256": hashlib.sha256(patch.read_bytes()).hexdigest()}
    (output / "fixture-build.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    print("Experimental frontend:", output / "dist")


if __name__ == "__main__":
    main()
