# Release Readiness Assessment

## Windows Research Preview (2026-09-29)

Unsigned local installer `1.0.0-preview.1` now builds with the production UI and
frozen Python backend. Scoped unpacked Electron checks pass for first launch,
authenticated control, renderer sandboxing, pinned datagram echo/capture, relaunch,
certificate reuse, stale-token rejection, and clean shutdown/port release. Final
instrumented run requires backend exit 0, not merely disappearance of its process.
70 frontend/launcher tests and 107 backend tests passed; one optional soak skipped.

NOT publicly released or production-approved. Installed/full-feature acceptance,
clean-machine testing, licensing notices, branding/signing, and outstanding resource
and compatibility checks remain. One earlier desktop-exit timeout is retained for
repeatability follow-up; its root cause is unconfirmed. Exact artifact checksum,
passing/failed attempts, and release gates: [Windows preview](WINDOWS_PREVIEW.md).

## Latest videocall Follow-Up (2026-09-28)

**Full A/V verdict: BLOCKED.** A separately labelled lifecycle-patched upstream
fixture passes simultaneous receiver-decoded video and both senders' video controls
directly and through Legilimens in `lifecycle-stream-cleanup-2`. Audio decoding and
remote mute remain unverified. This is one paired video/control run, not a complete
call-compatibility, unchanged-upstream, or shipping-readiness claim.

Fixed callback lifetimes in the isolated fixture, the local AudioWorklet CSP,
proxy completed-stream mappings and pinned-library send-only stream retirement.
Limits were not raised. Cleanup verified. An already-closed waiting-room connection
still hit a transport cap before the passing windows; early teardown, native metadata
retention and longer repeatability testing remain pending. Exact provenance, failed
attempts and regressions: [lifecycle investigation](VIDEOCALL_LIFECYCLE_DIAGNOSIS.md).
The sections below retain historical milestones and their then-current verdicts.

## videocall Two-Participant Media (2026-09-28)

**Historical unpatched media verdict: FAIL, not a clean direct baseline.** The earlier direct-PASS
and proxy-specific-cause claims are superseded: both historical direct runs contained
client runtime errors that the old gate ignored, and its motion check could accept a
short burst or several frozen canvases. Child exits were not enforced.

The corrected harness uses simultaneous sustained windows, per-run/participant
markers, native decoded-frame draw observations, exact remote-user tiles and
server-session attribution. Video-off/resume checks run for both senders while
requiring the other video and native transport to stay live. Mic UI state is tested
but is not remote audio proof. Runtime errors, missing evidence, WS fallback,
inconsistent exits and cleanup failures cannot produce a complete PASS.

Latest real run (`build/videocall-media/harness-review-fixed-2/`) observed both
video directions throughout the shared 15-second window (28/28 samples each), and
both senders' video-off/resume checks passed. However, the direct client reported
`closure invoked recursively or after being dropped` and `unreachable`; the route
correctly remains FAIL, audio BLOCKED and the proxy comparison NOT_RUN. Cleanup
was verified. Next: isolate the direct-client errors before attributing media
failures to Legilimens. No upstream source, core proxy behavior or resource limits
changed; existing built artifacts were reused.

Room isolation/reconnect, remote decoded-audio measurement/mute controls, capture
roundtrip and three complete direct+proxied A/V runs remain pending. Full evidence,
thresholds and reproduction: [media results](VIDEOCALL_MEDIA_RESULTS.md).

## videocall Authenticated UI Connectivity (2026-09-28)

The real pinned Dioxus UI now obtains room tokens from its actual local meeting
API and establishes pinned native WebTransport directly and through Legilimens.
Ten cases passed on three complete runs: two separate authenticated identities,
invalid/missing session cookies with explicit HTTP 401, and wrong pins with native
transport rejection, on both routes. Browser readiness and attributed server joins
must agree; absence of traffic or driver errors cannot pass negative tests.

Fixed the harness's omitted second Start Meeting gesture and misleading success
criteria. The main-page recorder observes native transport, so a worker-specific
adapter was not needed. Unchanged upstream source, exact-origin pins/CORS, local
CSP, disposable identities and owned cleanup retained. Eighteen JS policy/adapter
tests and three Python UI tests passed; one opt-in browser fault test skipped.

Still pending: two-participant media/decoding, room isolation/reconnect, capture
roundtrip against this application, and installer/clean-machine validation. These
identities use sequential separate contexts/rooms, not a verified two-person call.
No OAuth, fresh frontend rebuild or full regression-suite claim. See
[evidence and reproduction](VIDEOCALL_INTEROP.md).

## videocall UI Startup Verification (2026-09-27)

Real Dioxus form and both WASM worker startup checks pass in Chrome. Corrected the
earlier verifier's false network-isolation pass: public analytics is now blocked
before dispatch by CSP, with a separate browser request guard and explicit blocked
attempt evidence. Missing worker assets, runtime failures and unexpected requests
fail verification. Four Python tests (including real missing-WASM fault injection)
and 14 JavaScript policy tests pass; bounded runner cleanup verified. Trunk is pinned
to the installed 0.21.14 builder version. No fresh frontend rebuild or full application
media test in this follow-up. Live UI/backend wiring and two-participant direct/proxy
media remain pending. Details: [videocall evidence](VIDEOCALL_INTEROP.md).

## videocall.rs Admission Follow-Up (2026-09-27)

Pinned unchanged Linux backend sources now build, and local NATS/PostgreSQL/meeting
API/WebTransport services run. Eight native Chrome admission cases pass directly
and through Legilimens: valid room token accepted; invalid signature, expired and
missing tokens rejected. A real upstream-abort bug was reproduced and fixed: QUIC
termination/reset/STOP_SENDING during pending CONNECT now fails promptly rather
than waiting for response headers that will never arrive. All 23 focused proxy
tests pass, including three new regressions. Test resources were cleaned up.

This is not the full application milestone: no Dioxus meeting UI or synthetic
media exchange has been tested, and no public service was contacted. Full suite
reruns, two-participant media, room isolation/reconnect and application-capture
roundtrip remain pending. See [evidence and reproduction](VIDEOCALL_INTEROP.md).

## Browser Certificate Renewal Follow-Up (2026-09-27)

Reproduced a dashboard bug with real Chrome 153.0.8010.53: the backend renewed its
certificate, exited with restart code 75, and restarted with a new instance/pin,
but the open settings view retained the old proxy certificate. Both certificate
and upstream settings previously loaded only on mount.

Fixed both views to refresh after capture-channel recovery. The proxy pin is
cleared while disconnected. Upstream server state is marked unconfirmed until
loaded, unsaved edits survive reconnect, and late responses from an old connection
cannot overwrite the new state. Target Apply is disabled while disconnected.

Added `scripts/tests/rotation-browser.py` and `rotation-browser.mjs`: temporary
certificates, random authenticated loopback ports, and a fresh Chrome session.
The test accelerates the existing renewal scheduler inside the child process;
it does not change system time, normal certificate files, or production hooks.
Its supervisor starts one replacement backend after observing exit 75.

Verified after the fix:

- Changed backend instance and certificate pin, with the old WebTransport closed.
- Capture reconnection and fresh proxy/bundled-upstream pins without a page reload.
- Unsaved host and pin edits preserved in a second open tab.
- Restart defaults to inactive; the dashboard Start button resumes capture.
- Pinned WebTransport echo works after renewal and before/after payloads remain
  visible in the traffic log; the capture-gap warning remains visible.
- Owned processes stop and all test ports are released. Production frontend build
  and all 58 frontend tests pass. No full backend-suite rerun in this scoped change.

Reproduce with the Python development environment, Node 24, Playwright and installed
Chrome available (the browser script accepts `PLAYWRIGHT_MODULE` as a module path):

```powershell
npm --prefix client run build
.venv\Scripts\python.exe scripts/tests/rotation-browser.py --node node --output build/rotation-browser
node --test client/tests/*.test.cjs
```

Local evidence: `build/rotation-before/{report,browser}.json` records the original
stale-pin failure; `build/rotation-after/{report,browser}.json` and `reconnected.png`
record the passing corrected workflow. These generated files are not release assets.

Limits: the test supervisor, not the development or Electron launcher, performs
the restart. It uses the bundled target, not an external application; persistence
of custom upstream settings across restart is not established. No packaged,
clean-machine, long-duration, or universal browser-compatibility claim is made.
The independent Go empty/post-empty datagram browser gate remains failing, and
videocall.rs runtime testing remains on hold.

## Latest Interoperability Follow-Up (2026-09-26, Corrected)

The original four-run all-pass summary is superseded by the corrected harness.
Direct and proxied CLI expectations now gate success; replay must reach the actual
selected session, identified through capture markers, not merely one of two clients.
Exact target-side receipts validate conditional tamper. Manual datagram interception
(forward/edit/drop), bounded readiness, output draining, protocol-aware port checks,
cleanup, and fresh scratch directories are covered. The Go fixture's missing HTTP/3
ALPN advertisement was fixed; the CLI rejects missing ALPN too.

Verified: 18 harness regression tests passed, including deliberately misrouting replay
through the real backend and observing a failed verdict. Backend: 100 passed, one
optional soak skipped. Frontend: 58 passed. These counts are not coverage percentages.

Real Chrome 153.0.8010.53 now exercises the production UI and an independent Go target:
nonempty text/Unicode/whitespace/binary datagrams, bidi/uni streams, two-tab isolation,
reconnect, and actual capture export/offline import passed. Export preserved both
session identities despite filtering; import made no backend requests. Desktop/mobile
screenshots were inspected. No mocked control responses or certificate bypasses.

**Still failing:** empty datagram echoes and subsequent datagram reception time out
on both direct and proxied browser connections. The target receives the payloads;
proxied incoming/outgoing captures retain them. Exact cause is not confirmed.
The strict browser run continues through UI checks but exits 1, never a false all-pass.
A matching direct/proxied failure is not treated as successful functionality.
A separate minimal native-API probe reproduces the failure directly against Go,
without the suite receive pump or retries; target receipts confirm all three sends.
Edge 154.0.4258.37 subsequently reproduced the same failure and passed the other
transport/UI checks. Its experimental reader-type diagnostic did not fix it.
videocall.rs source is pinned and read-only preflight implemented, but its Linux
environment is not prepared and no application compatibility test has run. See
[second-target preparation](VIDEOCALL_INTEROP.md). Follow-up tests: 23 passed,
one opt-in integration skipped; no product-runtime source was changed in this follow-up.

Next: isolate the browser empty-datagram failure, then self-hosted videocall.rs,
full browser certificate rotation, extended resilience, and packaged/clean-machine
validation. Do not drop or rewrite empty traffic to conceal the compatibility gap.
No final bundle or production-readiness claim. Evidence and commands:
[Interoperability](INTEROPERABILITY.md). Older sections retain their historical scope.

## Latest Capture Follow-Up (2026-09-26)

The retained-capture export/import gate is complete in source. Versioned JSON
archives retain full payload strings and encoding, event/session/stream identity,
session-bound target metadata, and explicit eviction/gap indicators. A bounded,
strictly validated offline reader is available without backend authentication;
import does not invoke replay or replace live traffic. Files may contain secrets
in raw payloads, and completeness/provenance are not guaranteed.

Verification: 58 frontend tests, 100 backend tests (one optional soak skipped),
11 launcher tests, production build, and desktop/mobile browser checks passed.
Browser control responses were stubbed; this is not third-party interoperability
evidence. Details and limits: [Capture files](CAPTURE_FILES.md).

Next: independent locally hosted WebTransport interoperability. Extended
resilience, full browser certificate-rotation reconnection, packaged desktop,
and clean-machine validation remain outstanding. No final bundle was created.
Older sections below preserve their dated verification scope.

## Latest Dependency Follow-Up (2026-09-25)

The scoped dependency-security gate is complete for the reviewed source lockfiles.
All four npm scopes and the Python runtime/dev/environment audits report no known
findings. A fresh hash-locked Windows/Python 3.12 install passed 98 backend tests
(one opt-in soak skipped); 21 frontend and 11 launcher tests passed, plus the
production build and local Vite/Electron runtime smoke checks. The fresh install
also exposed a Windows lock initialization race, now fixed and regression-tested.
See [Dependency security](DEPENDENCY_SECURITY.md) for versions, exact scope,
reproduction commands, failed-run explanations, and limitations.

Use Node 24 for the updated desktop tooling; the machine's global Node 20.17 was
not changed. Restart old running processes to load the upgraded dependencies.
Existing frozen builds are unchanged. Independent interoperability, evidence
export/import, full browser rotation, packaging, and clean-machine validation
remain release gates. This is not product-security certification.

Current task status and next implementation step: [Task tracker](../TASKS.md).

Latest resource-hardening completion (2026-09-24): [limits, recovery contract, tests and measured workload](RESOURCE_LIMITS.md).
The earlier first-pass resource section below is historical and is superseded by that report.

Assessment date: 2026-09-23. Scope: current local working tree, including uncommitted changes. No release bundle was created during this assessment.

## Verdict

Usable for controlled local testing of supported WebTransport workflows. Not ready for a public release as a dependable security tool. The follow-ups below resolve the identified control boundary, capture/replay, and settings/cancellation findings. Resource limits, lifecycle reliability, dependency remediation, independent interoperability, and packaging validation remain release gates.

Compatibility with arbitrary real applications, production usage, performance limits, and clean-machine installation are not confirmed. Passing the local fixture tests does not establish those claims.

## Security Follow-Up (2026-09-23)

The findings below preserve the original assessment. The two P0 control-plane findings have now been addressed in source: loopback-only Python listeners, per-launch bearer-token API authentication, exact origin and Host checks, and WebSocket authentication before subscription. The UI, dev launcher, and desktop launcher have been wired for token access. Remote control remains unsupported.

Validation: 27 Python tests passed (15 new security tests plus 12 existing compatibility tests); seven frontend security/state tests passed; the production frontend build passed. Browser checks verified invalid-token rejection, valid-token access, authenticated capture subscription, settings reads, start/pause/disconnect controls, and loss of access after backend restart. After refreshing an expired development certificate, the browser also received live datagrams from the bundled local target. No attack was launched in the browser. A packaged desktop run is not confirmed.

The shared API client also rejects failed HTTP responses, and the capture WebSocket no longer retries an intentional disconnect or an authentication rejection. Regression tests cover these behaviors. This does not close the other lifecycle/state findings below.

Still outstanding: bounded resource use, certificate renewal, dependency remediation, desktop packaging validation, and independent real-application compatibility. No final bundle has been created.

## Capture and Replay Follow-Up (2026-09-24)

The capture-truncation and implicit-session-routing findings below are now resolved for the supported text-datagram workflow. Events retain full payload strings and a separate short display preview, with explicit session identity. Binary captures retain full Base64, but binary and stream replay remain unsupported. Captures represent the logged payload, including modifications; separate pre/post-tamper evidence pairs are not implemented.

Authenticated `GET /sessions` lists established replay destinations. `POST /replay` requires `sessionId`; missing identity returns 422, blank identity 400, and unknown, closed, or unready destinations 409. No fallback session is chosen. The Repeater preserves captured identity, polls live destinations, disables Send for unavailable sessions, and describes successful sends as queued rather than acknowledged delivery.

Verification: 33 Python tests and 10 frontend tests passed, as did the TypeScript/Vite production build. Local fixtures tested exact Unicode/whitespace and empty-datagram replay, both directions with two simultaneous clients, stale/unready identity rejection, and full binary/stream event retention. Browser checks against the bundled local target verified selection gating, queued feedback, closed-session gating, and desktop/mobile layouts. These tests do not establish third-party application interoperability, arbitrary datagram-size support, or sustained-load safety. Full payload retention makes the pending byte-budget/resource work especially important.

## Settings and Cancellation Follow-Up (2026-09-24)

Manual intercept updates now validate all requested fields before committing settings or forwarding held items. Empty or mixed-invalid scopes are rejected; valid duplicate scope entries are normalized. Partial tamper updates preserve omitted matching conditions, preventing a toggle from unintentionally broadening a scoped rule. The frontend sends only changed intercept fields and displays rejected-update errors without clearing held messages.

Cancellation now uses the same stopped terminal state in the runner, API, WebSocket, and UI. Stop waits for task cleanup and returns the actual status record; stopping an already completed or failed run does not relabel it. Pre-start cancellation, cancellation during the first broadcast, and concurrent stop requests have regression tests. UI stop failures are visible, successful HTTP responses reconcile state without depending on WS delivery, and delayed launch/progress events cannot resurrect terminal runs still in the 20-item UI history.

Verification: 42 Python tests, 14 frontend tests, and the TypeScript/Vite production build passed. A browser check stopped a local loris run configured for one connection per cycle during its first inter-cycle wait; the card and history showed stopped, zero running, and the status API agreed with no error. This is lifecycle verification, not load or attack-effectiveness evidence. New runner tests use inert doubles. Cancellation cleanup deadlines, broadcasts under backpressure, events arriving after UI history eviction, and multi-tab synchronization remain outside this verification.

## Resource Limits, First Pass (2026-09-24)

Partial milestone, not a release-readiness or whole-process memory claim:

- UI traffic retains at most 500 events and an 8 MiB serialized UTF-16 size estimate. Stream history retains 64 streams, each at most 128 chunks and 128 KiB by the same estimate. Old entries are evicted with counters; retained payloads remain intact. Harvested tokens retain at most 100 entries of at most 4,096 characters.
- Capture subscribers use one sender task each, at most 16 authenticated subscribers, 256 queued messages and 4 MiB ASCII-serialized bytes per subscriber (including an in-flight send). A five-second send timeout or overflow closes the subscriber with 1013; the UI warns that capture is incomplete. Events over 1 MiB serialized size are replaced by a loss notice, not a replayable preview. Cross-thread ingress has count/byte bounds and coalesced scheduling.
- Manual interception holds at most 256 messages and 4 MiB of UTF-8 payload/decision strings, with a 256 KiB per-payload limit. Capacity exhaustion drops new matching messages and emits a warning, rather than forwarding through an enabled intercept boundary. Oversized edits return 413 and leave the decision unresolved.
- Attack manager allows four active tasks and retains at most 100 records under the default limits. Capacity returns 429; completed records are evicted on new starts. Stop waits up to five seconds, then returns 504 while preserving the unfinished task rather than reporting stopped.

Verification: 49 backend tests and 17 frontend tests passed; resource-specific tests were rerun after accounting for edited held payloads. Production build passed. Deterministic tests cover count/byte saturation, a blocked send timeout, healthy subscriber isolation, pending-item cancellation cleanup, attack history eviction, cleanup timeout, and thousands of UI state updates. These are not real sustained-network-load or browser-heap measurements. New resource warning layouts have not been visually verified in this pass.

Remaining: proxy forwarding task/session/stream budgets, request-body and per-run attack limits, capture-state reconciliation after delivery gaps, terminal events arriving after UI history eviction, broader cross-thread/subscriber admission tests, and sustained-load/memory measurements. Logger serialization can still allocate an oversized temporary string before checking its size. Subscriber admission does not bound unauthenticated network connections. See TASKS.md before treating the entire resource milestone as complete.

## Certificate and Backend Lifecycle Follow-Up (2026-09-24)

Historical implementation report: the 2026-09-25 review found that the atomic-bundle, graceful-shutdown, and launcher-cleanup claims below were not established by the original tests. See the corrective follow-up below for current evidence; this section is not release approval.

The certificate existence-only check and the untruthful `READY` signal in "Code Risks" are resolved in source.

`certs.py` validates certificate parsing, a matching ECDSA P-256 key, localhost/127.0.0.1 SANs, validity dates, and DER-hash consistency (distinct from the SPKI hash). It preserves a valid certificate across restarts and renews a missing, unparsable, key-mismatched, wrong-type, SAN-incomplete, not-yet-valid, expired, or near-expiry (24-hour threshold) certificate. Renewal stages files atomically and moves them into place under a per-directory lock, so an interrupted write never leaves a mismatched cert/key/hash triple; a stale hash is repaired without discarding a good certificate, and the previous usable certificate is preserved if generation fails. Certificates live in the existing writable data directory. The bundled practice target's pin follows renewal; an explicitly chosen external target pin is never overwritten.

`backend.py` is a supervised lifecycle. It owns certificate preparation (launchers no longer generate certificates), registers each service's cleanup as its step succeeds, and prints `READY instanceId=<id>` only after every listener binds and an authenticated `/health` for that instance answers. Startup failure or an unexpected service exit unwinds partial startup and exits nonzero. One idempotent coordinated shutdown — shared by Ctrl+C, an authenticated `POST /shutdown`, read-only parent-liveness for launcher-owned mode, certificate rotation (a controlled restart, exit 75), and startup failure — cancels attack runs, closes proxy sessions, and stops the WebSocket and HTTP services within an 8-second deadline, reporting any incomplete step. `loop.stop()` and the ineffective Windows stdin watcher are gone. Renewal while running is a controlled restart because listeners bind the certificate at start.

Both launchers plumb the control token and parent PID, parse a line-buffered, identity-checked readiness signal with bounded log buffers, stop siblings and exit nonzero on startup failure, and stop gracefully before bounded forced tree cleanup (including Windows child processes). The dev launcher prefers Python source; the desktop launcher offers a controlled restart with no automatic loop.

Verification: 90 backend tests via `python -m unittest discover -s python/tests` (89 passed, 1 opt-in soak skipped; 28 new certificate/lifecycle tests covering valid-certificate preservation, renewal for expiry/near-expiry/corruption/key-mismatch/missing-SANs, stale/missing-hash repair, renewal-failure preservation, no-usable-certificate failure, concurrent generation, atomic-temp cleanup, supervised cleanup order/deadline/idempotency, parent liveness, truthful authenticated readiness with instance identity, port-conflict nonzero exit, unexpected API exit as failure, authenticated graceful shutdown, parent-death clean exit, and repeated start/stop reusing ports without orphans). The TypeScript/Vite production build passed, and both launchers pass `node --check`. The dev launcher (`scripts/start-all.js`) was run end to end on Windows source: authenticated readiness, a matching `READY`/`/health` instance id, and graceful teardown leaving no orphaned ports (including the Vite UI).

Not confirmed: the packaged Electron desktop run, a clean-machine install, and an end-to-end mid-run certificate-rotation restart with UI reconnection. The rotation mechanism and exit code have source-level tests; the full restarted-window path in the desktop shell is not verified. Existing frozen builds do not acquire these protections from source.

## Certificate/Lifecycle Review Fixes (2026-09-25)

All six review findings have source fixes and focused regression coverage:

- Certificate generation stages and validates all files before replacement. A restricted-permission recovery journal preserves the previous files, with rollback after a failed replacement or process death. Directory locks are OS-owned and release on process exit; the lock file remains intentionally. Listener loading, validation, and hash reads acquire the same lock and recover pending transactions first. These are individually atomic renames with journaled recovery, NOT a four-file atomic filesystem operation. External tools that read the flat files without the lock do not get this guarantee; power-loss/storage-failure durability is not confirmed.
- Validation rejects lifetimes over 14 days, CA certificates, missing server-auth constraints, and incompatible key usage, alongside the existing key/SAN/date checks. The inclusive 14-day boundary is tested.
- Cancelling the API guard no longer cancels the supervised Uvicorn task. Normal shutdown requests server exit and awaits graceful HTTP cleanup. QUIC shutdown closes its UDP listener and awaits the saved connection protocols, without calling the nonexistent QuicServer.wait_closed(). Incomplete cleanup returns failure instead of exit zero. Uvicorn bind-failure SystemExit is contained inside supervision so partial startup can unwind.
- The development launcher handles exit 75 by restarting only the backend, preserving Vite and the control token, and checking new-instance readiness. Automatic restarts are capped at three per launcher run. Backend state remains in-memory: a restart drops active sessions and resets runtime settings; this does not implement state persistence.
- Both launchers use a shared child-cleanup helper. Windows taskkill runs hidden with a five-second command timeout; its completion and the owned child's exit are awaited (a separate five-second exit-confirmation bound). The graceful wait is eight seconds after the shutdown request, which has its own two-second HTTP timeout. These are per-phase bounds, not a single eight-second launcher deadline. Electron prevents quit until the shared cleanup promise settles and surfaces cleanup errors. The helper is included in desktop packaging configuration, but no bundle was produced.

Tests now inject failure at every certificate commit step, abruptly exit a generator after key replacement, observe the real Uvicorn shutdown method, assert absence of cleanup errors, test UDP port binding rather than TCP probes for QUIC, exercise API-only port conflict and active QUIC draining, and rotate a real backend certificate followed by a separate backend restart with a changed advertised pin. Launcher behavior is covered with deterministic process/HTTP doubles; the cleanup helper also terminates a real test-owned Node process on Windows.

Verification commands: `.venv/Scripts/python.exe -m unittest discover -s python/tests -v`; `npm run test:launchers`; `npm --prefix client test`; `npm --prefix client run build`; `node --check desktop/main.cjs`; `node --check scripts/start-all.js`.

Results on 2026-09-25: 98 backend tests discovered (97 passed, one opt-in soak skipped); 11 launcher tests passed; 21 frontend tests passed; production frontend build and both launcher syntax checks passed. Active-connection QUIC draining and journal recovery after abrupt process death passed locally on Windows. The full backend suite completed in 76 seconds; this is test duration, not a performance benchmark.

Not confirmed: packaged Electron startup/quit/rotation, clean-machine installation, and an end-to-end automatic rotation with an open browser refreshing displayed certificate/target state and reconnecting a real application. The opt-in resource soak was not rerun for this correction. No final bundle was created.

## Verified Passes (Original Assessment, Historical)

- `python -m unittest discover -s python/tests -v`, using the project virtual environment: 12 tests passed. Coverage includes binary datagrams, handshake header forwarding, certificate pin/CA checks, rejected upstream handshakes, bidirectional/unidirectional streams, server-initiated stream replies, manual interception, and stream ordering/FIN behavior.
- Frontend TypeScript/Vite production build passed with `npm --prefix client run build`.
- Additional isolated probes verified text datagram replay in both directions, conditional tampering, manual intercept timeout forwarding, forwarding pending items when interception is disabled, and closing live sessions with `disconnect_all()`.
- Pause dropped datagrams; resume restored forwarding. This is destructive traffic suppression, not buffered delivery or merely pausing the display.
- Bounded loopback attack smoke tests completed: flooding with two connections and loris with one connection over two cycles. These establish execution only, not load capability, effectiveness against real applications, or resilience.
- Earlier browser checks exercised workspace navigation, retained drafts/filters, JSON formatting, keyboard navigation, local attack target validation, and desktop/mobile layouts. This assessment did not repeat every browser workflow.

The additional probes were temporary, isolated checks, not new committed regression tests. No coverage percentage has been established.

## Reproduced Release Blockers

Historical findings from 2026-09-23; resolution evidence appears in the dated follow-ups above and current remaining work is tracked in TASKS.md.

| Priority | Finding | Evidence | Required acceptance gate |
| --- | --- | --- | --- |
| P0 | Control API lacks an authentication/origin boundary | An unauthenticated POST to `/tamper` with an untrusted Origin returned 200 and wildcard CORS. API is configured to bind to all interfaces. | Default to loopback; reject unauthorized state changes and untrusted origins; explicitly secure any remote mode. Test allowed and rejected clients. |
| P0 | Capture WebSocket accepts an untrusted origin | A temporary logger listener accepted a WebSocket connection carrying an untrusted Origin without authentication. | Authenticate subscriptions and validate origins. Verify that unauthorized clients receive no captured traffic. |
| P1 | Captures lose the original payload used by Repeater | A 600-character event payload became 301 characters: a 300-character preview plus ellipsis. Repeater consumes this preview. | Keep display previews separate from original bytes; test exact replay of large supported payloads. |
| P1 | Replay can select the wrong session | With two real fixture clients connected, replay returned success without requiring a session selection. Code selects the first live session. | Carry explicit session identity through capture, UI, and replay API. Reject stale or ambiguous destinations. |
| P1 | UI reports success after backend failure | With fetch returning HTTP 500, the actual store still marked the proxy active. | Check response status; propagate actionable errors; keep displayed state consistent with acknowledged backend state. |
| P1 | Explicit WebSocket disconnect reconnects | Mocked WebSocket/timer probe against the actual store created a second connection after explicit disconnect. | Distinguish intentional disconnect from connection loss; cancel timers; test repeated connect/disconnect and recovery. |
| P1 | Rejected settings can partially mutate state | Manual intercept update with a new direction and invalid timeout returned 400 but changed the direction. | Validate the full request before applying an atomic update; assert unchanged state for rejected requests. |
| P1 | Stream retention is unbounded | After 1,001 chunks, the store retained 1,001 stream chunks while the traffic log was capped at 500 events. | Bound byte/event/session retention and expose truncation or eviction; run sustained-stream memory checks. |
| P2 | Stopped attacks appear failed | Cancellation left API state `stopped` while the terminal broadcast reported `failed`. | Use consistent terminal states across runner, API, WebSocket, and UI; regression-test cancellation. |

The origin probes demonstrate acceptance by the application. They do not establish remote exploitability through every browser policy, firewall, or network configuration.

## Code Risks Requiring Validation or Fixes

- ~~`python/certs.py` checks certificate/key existence, not expiration.~~ Resolved (2026-09-24): full validation, atomic staged renewal under a lock, and regression tests. See the Certificate and Backend Lifecycle Follow-Up above.
- ~~`python/backend.py` emits READY after scheduling the API task; `desktop/main.cjs` accepts any HTTP health response.~~ Resolved in source (2026-09-24): truthful readiness gated on bound listeners plus an authenticated, instance-identified `/health`; nonzero exits on failure; supervised idempotent shutdown; both launchers verify instance identity. The desktop shell's packaged run remains not confirmed. See the follow-up above.
- `python/logger.py` schedules a send task per subscriber/event without a bounded queue. Pending intercept work and attack history also need explicit resource budgets. Sustained-load behavior is not confirmed.
- Listeners, including the practice target, use all-interface bindings. Prefer loopback defaults and deliberate remote exposure.
- `fuzz` and `out_of_joint` remain accepted through the runner API despite incomplete behavior and being hidden from the UI. Remove unsupported operations from the supported contract or implement and test them.
- Frontend START creates demo WebTransport traffic. Keep demo traffic clearly separate from captures of an application under test.

## Dependency Check

Read-only `npm --prefix client audit --json` reported seven affected dependency packages: four high, two moderate, and one low. These include frontend build tooling/transitive dependencies; the count is not evidence of seven exploitable runtime vulnerabilities. No audit fix was applied. Review supported upgrades and re-run build/browser tests. Python and desktop dependency audits remain outstanding.

## Not Confirmed or Not Supported

- Compatibility with an independently implemented external WebTransport application.
- Full browser-to-proxy-to-real-application workflow on a clean machine.
- Windows installer, packaged backend startup, process cleanup, crash recovery, and certificate renewal after installation.
- Privileged Scapy encapsulation workflows and Npcap setup. No elevated/raw-packet test was run.
- Performance, sustained-load stability, concurrency limits, or attack effectiveness. No benchmark claims are justified.
- Binary or stream replay: currently outside the supported Repeater workflow. Transport forwarding tests do not prove replay support.
- Application-message framing/reassembly, durable capture storage, or capture export/import.
- Complete automated UI/end-to-end coverage or clean-machine release CI.

## Recommended Work Order

1. **Secure the control plane.** Loopback defaults, authentication, origin policy, deliberate remote mode, and negative security tests. Do this before encouraging use against sensitive application traffic.
2. **Make captures and replay trustworthy.** Preserve original bytes, select sessions explicitly, handle closed sessions, and test multiple concurrent clients and large payloads.
3. **Make state and failures accurate.** HTTP errors, atomic settings, intentional disconnects, reconnect behavior, and consistent attack cancellation. Convert the reproduced failures into regression tests.
4. **Harden lifecycle and resource use.** Certificate renewal, backend readiness/crashes, bounded capture buffers/queues, slow subscribers, dependency upgrades, and sustained-load tests with documented limits.
5. **Validate supported real-world workflows.** Independently implemented local test applications, browser compatibility, clear protocol limitations, and consented targets. Keep unsupported operations unavailable.
6. **Package only after release gates pass.** Test installation, first run, startup failure, restart, expiration/renewal, uninstall, and absence of orphan processes on a clean Windows environment.

Prioritize correctness and security over adding more simulator modes or visual polish. The next milestone is a trustworthy local research tool with explicit limits, not an unsupported claim of universal compatibility or production readiness.
