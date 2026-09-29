# Legilimens Windows Research Preview

Electron provides the desktop window and launches a bundled PyInstaller backend.
The end-user bundle does not require Python, Node, Go, Docker, or the videocall test
stack. A clean Windows machine must still be tested before claiming those runtime
prerequisites are independently verified.

## Local Build

Use Windows x64, Node >=22.12 (Node 24 recommended), and the project's Python 3.12
virtual environment. Install the hash-locked build dependencies into `.venv`:

```powershell
.venv/Scripts/python.exe -m pip install --require-hashes -r python/requirements-dev.txt
npm --prefix client ci
npm --prefix desktop ci
npm run package-win
```

The build script uses `.venv/Scripts/python.exe`, rebuilds the UI before freezing
the backend, uses the installed Electron distribution, disables automatic signing
identity discovery, and passes `--publish never`. It does not install the result.
The first build may download electron-builder's NSIS utilities. If a build fails,
do not treat partial or previously existing output as a successful new package.

Outputs for `1.0.0-preview.1`:

- `desktop/release/preview/Legilimens-Setup-1.0.0-preview.1.exe`: NSIS installer.
- `desktop/release/preview/win-unpacked/Legilimens.exe`: unpacked application;
  keep the ENTIRE `win-unpacked` directory together, not just this executable.
- `desktop/release/preview/build-manifest.json`: artifact sizes and SHA-256 hashes.

The installer is unsigned. Windows may display unknown-publisher or SmartScreen
warnings. Signing, public publishing, and Microsoft Store submission are separate
steps; none is performed by this build. A developer account is not needed to make
this installer. Do not disable Windows security to make the build or app run.

## Runtime

The shell generates a per-launch control token, starts its own backend, and waits
for an authenticated health response matching that exact backend instance. The UI
loads from loopback; the credential fragment is removed before React mounts.
The renderer uses sandboxing, context isolation, and no Node integration.

Writable data and generated certificates live under
`%APPDATA%/Legilimens/data`, not inside the installation directory. For isolated
testing, `LEGILIMENS_DESKTOP_DATA_DIR` selects a separate desktop profile.
Do not share that profile: it contains private certificate keys and browser data.

On normal quit the app requests authenticated graceful shutdown, waits, and uses
bounded forced process-tree cleanup only as a fallback. Certificate renewal can
request a controlled backend restart; unexpected exits offer Restart or Quit.

The default ports are UDP 4433/4434 and TCP 4435/4436, all on loopback. Close your
source-mode Legilimens instance before launching the preview. Do not kill unrelated
processes to free these ports. The bundled vulnerable target is a local demonstration
fixture, not a production service. External applications need deliberate endpoint
routing and certificate trust; installing Legilimens does not intercept arbitrary apps.

## Packaged Smoke Test

Provide Playwright as a test-only dependency (or an absolute module URL through
`PLAYWRIGHT_MODULE`), then from the repository root:

```powershell
node desktop/tests/packaged-smoke.mjs
# Optional explicit unpacked executable:
node desktop/tests/packaged-smoke.mjs C:/path/to/Legilimens.exe
```

The test refuses occupied ports, creates an isolated profile under
`build/desktop-smoke/`, and uses the real packaged shell/backend/UI. It verifies
authentication and rejected invalid credentials, renderer isolation, pinned native
WebTransport datagram echo, visible capture, normal quit/port release, relaunch,
certificate reuse, and rejection of the previous launch's token. No TLS bypass or
public target is used. The test disables background throttling only in the automated
window. Reports and screenshots are retained locally; profiles must not be published.

This is not installer, clean-machine, sustained-load, full-feature, or independent
application compatibility testing. See [preview release checks](../doc/WINDOWS_PREVIEW.md).

## Development

`npm --prefix desktop start` prefers source Python from `.venv`. To deliberately
test a frozen development backend, set `LEGILIMENS_USE_FROZEN=1`. Installed/unpacked
packages always use their bundled backend. Rebuild after source changes.

## Limitations

- Research preview, not production-ready or arbitrary-app compatibility certified.
- Browser empty/post-empty datagram limitations remain unresolved.
- Videocall audio/media-tampering evidence is incomplete and not a release feature.
- Encapsulation depends on Scapy/Npcap and is excluded from the frozen backend;
  invoking it can report a missing dependency. Simulator effectiveness is not proven.
- Long-running native resource retention and early closed-peer teardown need follow-up.
- Capture files may contain sensitive application data; review before sharing them.
- The current package uses the default Electron application icon.
