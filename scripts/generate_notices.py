"""
Regenerate THIRD_PARTY_NOTICES.md for the Windows desktop build.

Covers what the installer ships besides Electron/Chromium (whose notices electron-builder
already places next to Legilimens.exe): the Python runtime and PyInstaller bootloader,
the locked Python runtime packages frozen into the backend, and the production npm
packages bundled into the React UI.

Run from the repo root with the project environment (packages must be installed):
    .venv\\Scripts\\python.exe scripts\\generate_notices.py
"""

import json
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD_PARTY_NOTICES.md"
# Mirrors the `excludes` in legilimens-backend.spec: these are never frozen into the app.
SPEC_EXCLUDES = {"scapy"}
LICENSE_NAME = re.compile(r"^(licen[cs]e|copying|notice|authors)([.\-_].*)?$", re.I)


def python_requirements() -> list[str]:
    names = []
    for line in (ROOT / "python" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==", line)
        if match and match.group(1).lower() not in SPEC_EXCLUDES:
            names.append(match.group(1))
    return names


def license_id(meta) -> str:
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    declared = (meta.get("License") or "").strip()
    # Some packages put the whole license text in this field; keep only a short label.
    if declared and "\n" not in declared and len(declared) < 80:
        return declared
    return ", ".join(classifiers) or "see license text"


def dist_license_texts(dist) -> list[tuple[str, str]]:
    texts = []
    for file in dist.files or []:
        parts = [p.lower() for p in file.parts]
        if ".dist-info" not in str(file.parts[0]):
            continue
        if "licenses" in parts or LICENSE_NAME.match(file.name):
            try:
                texts.append((file.name, file.read_text(encoding="utf-8").strip()))
            except (OSError, UnicodeDecodeError):
                pass
    return texts


def python_entries() -> list[dict]:
    entries = []
    for name in python_requirements():
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            sys.exit(f"{name} is not installed in this environment; install python/requirements.txt first")
        meta = dist.metadata
        entries.append({
            "name": meta["Name"], "version": dist.version, "license": license_id(meta),
            "url": meta.get("Home-page") or next((u.split(",", 1)[1].strip() for u in meta.get_all("Project-URL") or []
                                                  if u.lower().startswith(("homepage", "source", "repository"))), ""),
            "texts": dist_license_texts(dist),
        })
    return entries


def runtime_entries() -> list[dict]:
    entries = []
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    entries.append({
        "name": "Python", "version": sys.version.split()[0], "license": "PSF-2.0",
        "url": "https://www.python.org/",
        "texts": [("LICENSE.txt", python_license.read_text(encoding="utf-8").strip())] if python_license.exists() else [],
    })
    try:
        dist = metadata.distribution("pyinstaller")
        entries.append({
            "name": "PyInstaller bootloader", "version": dist.version,
            "license": "GPL-2.0-or-later WITH Bootloader-exception",
            "url": "https://pyinstaller.org/", "texts": dist_license_texts(dist),
        })
    except metadata.PackageNotFoundError:
        entries.append({
            "name": "PyInstaller bootloader", "version": "", "url": "https://pyinstaller.org/",
            "license": "GPL-2.0-or-later WITH Bootloader-exception", "texts": [],
        })
    return entries


def npm_entries() -> list[dict]:
    lock = json.loads((ROOT / "client" / "package-lock.json").read_text(encoding="utf-8"))
    entries = []
    for key, info in sorted(lock.get("packages", {}).items()):
        if not key.startswith("node_modules/") or info.get("dev") or info.get("link"):
            continue
        folder = ROOT / "client" / key
        manifest = json.loads((folder / "package.json").read_text(encoding="utf-8")) if (folder / "package.json").exists() else {}
        name = key.rsplit("node_modules/", 1)[1]
        if name == "legilimens":  # the project itself (file:..), not third-party
            continue
        texts = []
        for file in sorted(folder.iterdir()) if folder.exists() else []:
            if file.is_file() and LICENSE_NAME.match(file.name):
                texts.append((file.name, file.read_text(encoding="utf-8", errors="replace").strip()))
        repo = manifest.get("repository")
        entries.append({
            "name": name, "version": info.get("version", ""),
            "license": info.get("license") or manifest.get("license") or "see license text",
            "url": manifest.get("homepage") or (repo.get("url") if isinstance(repo, dict) else repo) or "",
            "texts": texts,
        })
    return entries


def render(sections: list[tuple[str, list[dict]]]) -> str:
    lines = [
        "# Third-party notices",
        "",
        "Legilimens is MIT-licensed (see [LICENSE](LICENSE)). The Windows desktop build also",
        "includes the third-party software listed below, under their own licenses.",
        "",
        "Electron and Chromium notices ship next to `Legilimens.exe` as `LICENSE.electron.txt`",
        "and `LICENSES.chromium.html`. Scapy is not bundled (the optional `encapsulation`",
        "attack reports it as missing in the desktop app).",
        "",
        "This file is generated by `scripts/generate_notices.py`; regenerate it after dependency changes.",
        "",
    ]
    for title, entries in sections:
        lines += [f"## {title}", "", "| Package | Version | License |", "|---|---|---|"]
        lines += [f"| {e['name']} | {e['version']} | {e['license']} |" for e in entries]
        lines.append("")
    lines += ["---", "", "## License texts", ""]
    for _, entries in sections:
        for e in entries:
            lines += [f"### {e['name']} {e['version']}".rstrip(), ""]
            lines.append(f"License: {e['license']}" + (f" · {e['url']}" if e["url"] else ""))
            lines.append("")
            if not e["texts"]:
                lines += ["_No license file is included in the installed package; see the project URL above._", ""]
            for filename, text in e["texts"]:
                lines += [f"`{filename}`", "", "```text", text, "```", ""]
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    sections = [
        ("Python runtime and bootloader", runtime_entries()),
        ("Python packages (frozen backend)", python_entries()),
        ("npm packages (bundled UI)", npm_entries()),
    ]
    OUT.write_text(render(sections), encoding="utf-8")
    counts = ", ".join(f"{title}: {len(entries)}" for title, entries in sections)
    print(f"Wrote {OUT.relative_to(ROOT)} ({counts})")
