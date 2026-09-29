# Windows Preview Release Checks

Scope: local unsigned Windows x64 research preview, `1.0.0-preview.1`.
Building an installer does not imply public-release approval or production readiness.

## Local Verification (2026-09-29)

- Production frontend, PyInstaller backend, and NSIS installer built successfully
  with `scripts/build-windows.mjs`. Installer Authenticode status: `NotSigned`.
- Installer size: 144,508,476 bytes. SHA-256:
  `6ce6b67e74e4dbe9f50b6e5a406b828d3b6e7e92ec6336d6e3d90e0255fe94d0`.
- 70 frontend/launcher tests passed. Backend discovery: 108 tests, 107 passed and
  one opt-in soak skipped. Existing unclosed-transport ResourceWarnings appeared;
  this is not evidence that the pending resource-retention work is complete.
- Final instrumented packaged smoke: `build/desktop-smoke/run-kr2YDI/report.json`,
  PASS across first launch and relaunch, with authentication, renderer sandboxing,
  pinned native datagram echo to the bundled target, displayed capture, certificate
  reuse, and rejection of the old launch token. Both backends exited with code 0;
  measured quit waits were 1,199 and 1,203 ms. Ports released; no app/backend left running.
- Screenshots were retained; the traffic view was visually inspected. The ASAR
  contains only `main.cjs`, `process-lifecycle.cjs`, and package metadata. The desktop
  dependency on the repository root was removed. No generated `key.pem`, `cert.pem`,
  `cert-hash.txt`, or `.env` was found in the unpacked bundle.

Preserved attempts: `run-HYiljQ` failed hidden-window click stability; disabling
background throttling only in the test corrected it (`run-Dc6llt` passed).
`run-JmXEhF` passed both traffic checks but timed out waiting for desktop exit at
15 seconds, before detailed exit diagnostics were added. The harness now budgets
25 seconds for the production shutdown sequence (up to 20 seconds including forced
fallback) AND requires an explicit clean backend exit, so a forced kill cannot pass.
The final instrumented run passed well below either deadline, but the earlier timeout's
root cause is NOT established. Repeatability remains an acceptance check, not a claimed
production fix. The installer itself has not been installed/uninstalled in this run.

## Installed-Build Check on the Development Machine (2026-09-29)

The unchanged installer (SHA-256 `6ce6b67e…fe94d0`, re-verified) was installed and
uninstalled on the Windows 11 development machine. This is NOT a clean-machine result:
Python, Node and prior Legilimens app data were present.

- Silent per-user install (`/S`): exit 0 in 19 s, no elevation. Installed to
  `%LOCALAPPDATA%\Programs\Legilimens` (459.6 MB); `Legilimens.exe` and
  `legilimens-backend.exe` hashes match `build-manifest.json`. Desktop and Start Menu
  shortcuts and an HKCU uninstall entry (`Legilimens 1.0.0-preview.1`) were created.
- `desktop/tests/packaged-smoke.mjs` against the installed executable: PASS on both
  launches (`build/desktop-smoke/run-erEerA`), with clean backend exits (quit 1,185 / 1,079 ms).
- `scripts/record-demo.mjs` against the installed executable: PASS with no page errors
  (`build/demo-video/run-S2Dp5m`). Covered capture, secret flagging, conditional tamper with
  the target echoing the forged value, intercept edit/forward/drop, selected-session replay.
- Silent uninstall: exit 0. Program folder, both shortcuts and the uninstall entry were
  removed; no Legilimens processes remained and ports 4433-4436 were free.
- Both test runs used isolated profiles. The pre-existing `%APPDATA%\Legilimens` folder
  (78 items) was unchanged afterwards; the default uninstaller keeps app data.

Not covered: installer UI pages (silent mode), SmartScreen/Mark-of-the-Web behavior
(the file was built locally, not downloaded), first run with the default profile,
capture export/import, renewal, port conflict, upgrade and a clean machine.

## Candidate Scope

Traffic inspection, datagram hold/edit/forward/drop, conditional JSON tampering,
session-selected text-datagram replay, stream inspection, and capture export/offline
import. Source/independent-fixture verification is recorded in INTEROPERABILITY.md;
that evidence is distinct from testing this packaged artifact.

Videocall receiver audio and media tampering are not claimed. Simulator effectiveness,
arbitrary application compatibility, and broad performance claims are excluded.

## Before Public Distribution

- [ ] Install on a clean supported Windows x64 machine without Python/Node/Docker.
- [ ] Exercise every advertised workflow against the installed build, including
  tamper target receipt, selected-session replay, intercept decisions, capture export
  and offline import. Retain exact build hashes and results.
- [ ] Verify installed restart, backend failure/recovery, certificate renewal,
  occupied-port error handling, upgrade, and uninstall. Confirm owned processes exit
  and document whether user data is retained. Do not remove unrelated installations.
- [ ] Repeat packaged/installed quit under active traffic and investigate any recurrence
  of the earlier desktop-exit timeout; keep backend exit diagnostics enabled in tests.
- [ ] Complete the outstanding teardown/native memory investigation and bounded
  sustained-load checks, or explicitly restrict the preview's supported workload.
- [ ] Document supported browsers and the empty/post-empty datagram limitation.
- [ ] Review project licensing and bundled third-party notices before distribution.
  Package metadata says MIT; that alone is not a complete licensing review.
- [ ] Choose publisher identity/signing and replace the default application icon.
- [ ] Approve the release notes and distribution location. Nothing is uploaded by
  the build script; signing credentials must not be put in the repository or chat.

## Evidence Handling

`desktop/release/preview/build-manifest.json` records the generated artifacts.
`build/desktop-smoke/run-*/report.json` records each packaged smoke attempt and
the executable/archive hashes, alongside screenshots. Failed attempts stay retained.
The isolated profile contains private keys and must not be distributed.

Keep a candidate local when a required check fails. Never replace a published build
silently under the same version. For a future release rollback, retain the previous
installer/hash and explicitly instruct users which version to reinstall; user data
and capture files must not be deleted as a rollback shortcut.
