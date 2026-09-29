# videocall.rs Interoperability Preparation

Status (2026-09-28): **authenticated real UI-to-backend WebTransport connectivity verified direct/proxied; full video application compatibility not confirmed**.
Local services have been started and cleaned up. No public meeting service was tested.
The original UI-load verifier did attempt public analytics; that isolation defect is corrected below.

## Latest Experimental Media Result (2026-09-28)

The separately labelled lifecycle-patched real UI now passes simultaneous decoded
video and both participants' video-off/resume controls directly and through
Legilimens in one paired run. Audio decoding/remote mute remain **BLOCKED**, and
the original unpatched client lifecycle failures remain preserved. Proxy stream
retirement was fixed without raising limits; early teardown of an already-closed
waiting-room peer and native metadata retention still need work. Cleanup verified.
See [exact fixture provenance and results](VIDEOCALL_LIFECYCLE_DIAGNOSIS.md).
The following sections retain the earlier unpatched and connectivity results.

## Media Harness Review (2026-09-28)

The initial two-participant experiment's direct-PASS verdict is superseded. Both
saved direct runs contained WASM errors, and the old gate did not require sustained
simultaneous video or enforce subprocess failure. This does not invalidate the
separately scoped authenticated-connectivity results below.

The corrected harness verifies simultaneous native decoded-video progression,
run/participant identity, remote canvas/server attribution, live pinned transport,
and both participants' video-off/resume controls. In the latest real direct run,
video and video-control observations passed, but client WASM errors correctly made
the overall route FAIL. Audio remains BLOCKED and the corrected proxy comparison
is NOT_RUN until the direct baseline is clean. No proxy or upstream source changes.
See [current evidence and next steps](VIDEOCALL_MEDIA_RESULTS.md).

## Authenticated UI Connectivity (2026-09-28)

`connect_ui.py` starts the owned local NATS/PostgreSQL/meeting API/transport stack
and Legilimens, serves the existing real Dioxus build with actual dynamic ports,
and drives Chrome through the upstream meeting workflow. Exact UI-origin CORS,
COOP/COEP and local-only CSP remain enabled. Fresh disposable `session` cookies
bootstrap two signed local identities; the real meeting API issues their room
tokens. This is not OAuth login coverage.

The initial driver stopped after the home form and omitted the second **Start
Meeting** gesture required by a newly created room. It also treated inactivity or
exceptions as successful negative tests and ignored the browser's failed exit.
Those earlier verdicts are superseded. The claimed worker-observation blocker was
not reproduced: this pinned UI constructs WebTransport in the main-page realm,
and the corrected recorder observes its native readiness/rejection directly.

The test-only constructor adapter preserves native instances, prototypes, static
members, options and full URL/token, adding `serverCertificateHashes` only for the
exact configured transport origin. No global TLS bypass or upstream source edits.
Recorded transport URLs omit queries so room tokens are not written to evidence.
The upstream client also attempts a media WebSocket; it points to an unused local
port, never the Legilimens capture socket. Attempts are recorded as rejected and
any successful WebSocket connection fails the gate. No WS media server is supplied.

Each route checks five cases in separate browser contexts and unique rooms:

- Identity A and identity B: real API admission/token, native pinned WebTransport
  readiness and an attributed transport-server room join while the page stays open.
- Invalid session signature and missing session: explicit join API HTTP 401,
  no native transport attempt, and no server join.
- Wrong endpoint pin: successful API admission followed by native WebTransport
  rejection and no server join. Timeout, navigation errors or a broken UI fail.

Three complete runs passed all ten cases in Chrome 153.0.8010.53. Evidence is in
`build/videocall-connect-fixed-1/`, `build/videocall-connect-fixed-2/`, and
`build/videocall-connect-fixed-3/`:
`report.json`, `direct.json`, `proxied.json`, driver logs and sanitized container
logs. All runs verified owned-resource cleanup and released ports. Eighteen JS
policy/adapter tests and three Python UI tests passed; one existing opt-in browser
fault-injection test was skipped in this follow-up.

```powershell
# Existing pinned backend image and build/videocall-frontend/dist required.
.venv\Scripts\python.exe interop/videocall/connect_ui.py --node node --output build/videocall-connect
node --test interop/tests/connect-policy.test.mjs interop/tests/verify-policy.test.mjs
.venv\Scripts\python.exe -m unittest discover -s interop/tests -p test_videocall_ui.py -v
```

Requires Docker Linux engine, Python environment, Node, Playwright and Chrome.
`PLAYWRIGHT_MODULE` overrides Playwright module discovery. Per-case waits and a
240-second driver deadline bound execution. Stale reports are removed; browser
exit status, per-case evidence and cleanup must all pass. Artifacts remain outside
the runtime/installer. This automated runner cleans up; it is not a persistent
interactive demo launcher.

**Scope:** identities/rooms are exercised sequentially, not as two participants
exchanging media. Synthetic devices permit the upstream connection gesture, but
no audio/video fidelity, decoding, room isolation, reconnect or capture roundtrip
is claimed. Existing build artifacts were reused. Core Legilimens runtime code
was unchanged; full backend/frontend suites were not rerun.

## UI Startup Review and Fixes

The real Dioxus build artifacts contain the application WASM and both media-worker
modules. The initial verifier incorrectly reported no external requests: it only
counted responses and missed a COEP-blocked request to `matomo.videocall.rs/matomo.js`.
Its HTTP-200 loader checks also did not prove worker startup. Those original
`evidence/ui.json` and `review/ui.json` verdicts are superseded for isolation/startup.

The local serving overlay now validates endpoint origins and sends a restrictive
CSP that blocks the hardcoded analytics before dispatch, including for manual use.
COOP/COEP remain enabled. Inline scripts/eval are allowed only as needed by this
upstream fixture (NetEq embeds an eval-based Opus decoder); this server is not a
general-purpose hardened production host. A separate verifier context guard blocks
non-allowlisted HTTP requests and WebSockets, with service workers disabled.

The verifier requires a visible meeting form, correct local configuration,
cross-origin isolation, actual unchanged worker startup, both worker WASM responses
with the correct MIME type, and NetEq's real `workerReady` message. Unexpected HTTP
failures, runtime errors, CSP violations and external responses fail the verdict.
Known blocked analytics is recorded explicitly, not misreported as no attempt.
The exact unavailable local meeting-list endpoint is expected in this load-only test.
An absent favicon gets an explicit 204; other missing assets remain 404 failures.
The runner has a 90-second bound, removes stale reports and checks cleanup.

Verified in Chrome 153: mounted form, both worker startup checks, analytics blocked
by CSP, no unexpected startup errors, and released server/temp resources. Four
Python tests passed, including a real-browser negative test with worker WASM removed
from a disposable copy; 14 JavaScript policy tests passed. Evidence:
`build/videocall-frontend/fixed/ui.json` and `ui.png`.

Trunk is pinned to 0.21.14, confirmed from the existing builder image; wasm-bindgen
remains 0.2.108. The existing build was exercised, not rebuilt from scratch in this
fix. Upstream source is unchanged, and these test artifacts remain outside packaging.

```powershell
.venv\Scripts\python.exe interop/videocall/verify_frontend.py --node node
node --test interop/tests/verify-policy.test.mjs
$env:VIDEOCALL_UI_BROWSER_TEST='1'
$env:VIDEOCALL_NODE='node'
.venv\Scripts\python.exe -m unittest discover -s interop/tests -p test_videocall_ui.py -v
```

Live authenticated wiring is now verified above. The next step is two-participant
media testing. Worker startup does not prove media receipt or decoding.

## Verified Admission Milestone

- Built unchanged pinned source with Rust 1.93.1 and `cargo build --locked`:
  `webtransport_server` and `meeting-api`. The Dockerfile pins the Rust base digest;
  Debian build packages come from apt repositories, so this is not a completely
  hermetic build or a new dependency-security audit.
- Ran disposable NATS 2.10.26 and PostgreSQL 18.2 images pinned by digest. Loaded
  upstream's database schema, enabled `FEATURE_MEETING_MANAGEMENT=true`, and used
  fresh short-lived P-256 certificates with exact browser and proxy pins.
- Bootstrapped a disposable signed local session identity, then obtained a room
  token through the real meeting API join endpoint. This does not test OAuth login.
- Chrome 153.0.8010.53 passed eight native WebTransport cases: valid token accepted,
  invalid signature rejected, expired token rejected, missing token rejected, each
  directly and through Legilimens. No WebSocket fallback or certificate bypass.
- Fixed a real proxy issue: upstream connection closure or CONNECT stream abort
  before response headers left admission waiting for a timeout. These events now
  wake the pending request and return a downstream 502; ordinary upstream HTTP
  rejection statuses continue to be forwarded. Three new QUIC integration tests
  cover termination, RESET_STREAM and STOP_SENDING; all 23 focused proxy tests pass.

The initial Docker `--internal` network exposed no published ports on this setup,
despite healthy services inside containers. A dedicated bridge fixes readiness;
only application ports are published, bound to `127.0.0.1`. NATS and PostgreSQL
are unpublished. The bridge is not an outbound-network deny policy.

Reproduce (Docker Linux engine, Python environment, Node and Playwright/Chrome required):

```powershell
.venv\Scripts\python.exe interop/videocall/build.py
# Ensure the NATS/PostgreSQL digests listed in run_smoke.py are available locally.
.venv\Scripts\python.exe interop/videocall/run_smoke.py --node node
.venv\Scripts\python.exe -m unittest discover -s python/tests -p test_proxy_compatibility.py -q
```

`PLAYWRIGHT_MODULE` can name an installed Playwright module. The runner uses random
loopback ports and temporary credentials and cleans up owned containers, network
and processes. Reusable Docker images/build caches remain. Local sanitized evidence:
`build/videocall-diagnostic/report.json` (original network diagnosis),
`build/videocall-smoke-bridge/browser.json` (three proxied auth timeouts), and
`build/videocall-smoke-fixed/{report,browser}.json` (eight passing cases and cleanup).

**Remaining:** verify two-participant synthetic media
direct/proxied, room isolation/reconnect, and capture export/import for that traffic.
This admission test sends no media and cannot establish audio/video fidelity,
application usability, performance, or production readiness. Full backend/frontend
suites were not rerun in this bounded follow-up; the independent empty-datagram
browser issue and packaged/clean-machine validation remain open.

## Pinned Source

- Repository: https://github.com/security-union/videocall-rs
- Revision: `31a8b2076e7228f1b846fe4ed75dc70ffc27eb44`
- Local ignored checkout: `.tooling/videocall-rs`
- Manifest: `interop/videocall/target.json`
- Upstream Nix toolchain: Rust `1.93.1` (`nix/rust.nix`).
- Transport workspace dependency: `web-transport-quinn = 0.8.1` (`Cargo.toml`).

Source was inspected, not patched. The preflight rejects another revision, missing
required source files, or a dirty checkout. It never fetches, installs, builds, or
starts anything. A successful source check is not a successful application test.

```powershell
.venv\Scripts\python.exe interop/videocall/preflight.py --report build/videocall-preflight/source.json
.venv\Scripts\python.exe interop/videocall/preflight.py --check-docker --report build/videocall-preflight/environment.json
```

## Environment Findings (2026-09-26)

- Windows has Rust 1.82.0, older than the upstream pinned toolchain.
- The server directly uses `tokio::signal::unix` without a Windows conditional in
  `actix-api/src/bin/webtransport_server.rs`. An unchanged native Windows build is
  not the chosen route.
- Docker CLI exists, but its engine was not running (`docker_engine` pipe absent).
- Ubuntu WSL exists. A login-shell PATH probe found none of `cargo`, `rustc`,
  `nix-shell`, `nats-server`, `psql`, or `trunk` there.
- No Linux toolchain was installed and no Docker engine settings were changed.

The upstream dev workflow now uses Nix and process-compose, not a ready-to-run
root Docker Compose stack. `make dev` can install Nix and launches more services
than this experiment needs. Do not execute it blindly. A Docker-based route needs
an explicitly reviewed Linux build/runtime definition with pinned images and deps.

## Required Test Setup

1. Prepare an isolated Linux environment after the environment choice is agreed.
   Keep compiler caches, state, and downloaded dependencies disposable. Build from
   the pinned revision with `Cargo.lock` enforced; do not alter upstream validation.
2. Start only owned local services: NATS, the WebTransport server, and the meeting
   API/database/UI needed for the actual browser meeting workflow. Avoid monitoring,
   marketing-site, and unrelated services. Publish Windows-accessible ports only on
   `127.0.0.1`; verify WSL/container UDP reachability before interpreting failures.
3. Generate fresh short-lived ECDSA certificates. Preserve verification using exact
   endpoint pins. Do not use the repository's bundled development private keys,
   global certificate bypass flags, or `INSECURE=true` test configurations.
4. Keep room authorization enabled. The inspected server accepts JWTs via
   `/lobby?token=...`; obtain local test tokens from the local meeting workflow.
   Reject expired/invalid tokens in both direct and proxied cases. Raw target logs
   may include the URL/token, so use disposable credentials and keep artifacts private.
5. Configure the browser UI's `apiBaseUrl`, `wsUrl`, and `webTransportHost` to owned
   local endpoints only. The checked-in UI defaults include OAuth and fallback
   assumptions that need deliberate local configuration. Do not contact public auth
   or media services. Use synthetic media, not the user's camera or microphone.

## Acceptance Sequence

1. Direct baseline: two local browser participants join a room through actual
   WebTransport, exchange synthetic media, reconnect, and remain room-isolated.
   Record negotiated transport and reject automatic media WebSocket fallback as a
   WebTransport success. Ordinary REST/control traffic is allowed.
2. Repeat unchanged through Legilimens, with its upstream certificate pinned and
   browser pins scoped to the proxy. Verify the same application-visible behavior.
3. Capture actual traffic, map session identities, and export/import it through the
   UI. Do not assume encrypted/compressed/binary media is editable JSON or replayable.
4. Exercise bounded manual operations only where the tool supports that payload
   type. A decoder rejecting modified media is not evidence of successful visual
   substitution. Binary/stream replay remains outside the current supported scope.
5. Stop only owned processes/containers, verify ports released, and remove generated
   credentials. Preserve a sanitized report naming exact versions, pass/fail cases,
   and limitations. No compatibility claim until both baseline and proxy pass.

The Chrome/Edge empty-datagram limitation remains a separate known failing gate.
This second application may not generate empty datagrams; a successful meeting
would not resolve or waive that issue.
