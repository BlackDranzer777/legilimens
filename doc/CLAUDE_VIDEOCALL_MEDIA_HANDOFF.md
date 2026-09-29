# Claude Code Handoff: Real videocall.rs Media Verification

Prepared: 2026-09-28. This is an implementation-and-test assignment, not a request
for another plan. Complete the scoped work below, or report a precise blocker with
retained evidence. Do not call an unobserved result a pass.

## Objective

Verify that two simultaneous participants using the real, pinned videocall.rs
Dioxus UI receive and decode each other's identifiable synthetic audio AND video:

1. Directly against the owned local videocall backend.
2. Through the real Legilimens proxy, with certificate verification enabled.

Use real UI actions, real meeting APIs, real codecs and native WebTransport. Do
not substitute an echo server, a clone UI, mock responses, prerecorded receiver
output or a standalone transport client for this application workflow.

The expected deliverable is a repeatable automated test plus honest evidence and
a review handoff for Codex. No installer or production-readiness claim is in scope.

## Workspace and Boundaries

Repository root:

```text
C:\Users\divya\OneDrive\Divyansh\Development_Security\Legilimens
```

Keep work separated as follows. Relative paths below are relative to that root:

| Purpose | Location |
| --- | --- |
| Tracked test harness | `interop/videocall/` |
| Harness regression tests | `interop/tests/` |
| Untouched, ignored upstream source | `.tooling/videocall-rs/` |
| Existing real frontend build | `build/videocall-frontend/dist/` |
| New generated media/evidence | `build/videocall-media/<unique-run-id>/` |
| Reviewed final narrative | `doc/VIDEOCALL_MEDIA_RESULTS.md` |
| Existing status records | `TASKS.md`, `doc/VIDEOCALL_INTEROP.md`, `doc/RELEASE_READINESS.md` |

The worktree already contains substantial unrelated changes. Read applicable
repository instructions and inspect status first. Do not reset, stash, revert,
commit, push, clean the repository or modify unrelated work. Do not add fixtures,
browsers or downloaded tooling to product runtime/installer dependencies.

Do not edit the upstream checkout or its built application JS/WASM to manufacture
success. Test-only input/observation adapters are allowed under the restrictions
below and must be disclosed. Prefer no Legilimens runtime changes; if a runtime
defect is reproducible, preserve the failing case, make a narrow fix and add a
regression test. Do not weaken application authorization or codec validation.

## Read First

Read these before implementation; current files take precedence over this guide
if versions or paths have changed:

- `TASKS.md` and `doc/VIDEOCALL_INTEROP.md`.
- `interop/videocall/target.json`, `preflight.py` and `run_smoke.py`.
- `interop/videocall/connect_ui.py`, `connect-ui.mjs`, `connect-policy.mjs`.
- `interop/videocall/serve_ui.py`, `verify_frontend.py`, `verify-ui.mjs`.
- `interop/harness/support.py` and existing tests in `interop/tests/`.
- Upstream `dioxus-ui/src/pages/{home,meeting}.rs`.
- Upstream `dioxus-ui/src/components/{attendants,host_controls}.rs`.
- Upstream `videocall-client/src/decode/{peer_decoder,neteq_audio_decoder}.rs`.
- Upstream `videocall-codecs/src/decoder/wasm.rs` and `src/bin/worker_decoder.rs`.
- Upstream `videocall-client/src/audio/shared_audio_context.rs` and
  `videocall-client/src/scripts/pcmPlayerWorker.js` as needed for audio observation.

### Existing verified baseline

- Pinned upstream revision: `31a8b2076e7228f1b846fe4ed75dc70ffc27eb44`.
- Real local NATS, PostgreSQL, meeting API and WebTransport server are available
  through the existing Docker harness; image definitions are in `run_smoke.py`.
- Authenticated UI connectivity passed ten cases on three runs. Evidence:
  `build/videocall-connect-fixed-{1,2,3}/`.
- Those runs exercised identities sequentially in separate rooms. They did NOT
  verify two participants exchanging media.
- The home form is not the authentication gate. After the home submit and real
  join API response, a new host must click the second **Start Meeting** button.
- Cookies bootstrap disposable signed local identities. Real meeting API calls
  issue room tokens. This is NOT OAuth coverage.
- Main-page instrumentation successfully observes this pinned UI's native
  WebTransport. Do not repeat the unsupported claim that its transport is hidden
  inside workers. Video decoding DOES use a worker; audio has a NetEq pipeline.
- The client attempts WebSockets as well. The harness points that media endpoint
  at an unused loopback port, NOT Legilimens' capture WebSocket. Successful media
  WebSocket use must fail the WebTransport test; a rejected attempt is recorded.
- The independent Chrome/Edge empty-datagram issue remains open. Do not waive or
  change that gate as part of this assignment.

## Environment and Preflight

Known local tools; verify availability instead of assuming:

```powershell
Set-Location 'C:\Users\divya\OneDrive\Divyansh\Development_Security\Legilimens'
$Python = Join-Path $PWD '.venv\Scripts\python.exe'
$Node = 'C:\Users\divya\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$env:PLAYWRIGHT_MODULE = 'file:///C:/Users/divya/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'

& $Python --version
& $Node --version
docker info --format '{{.ServerVersion}}'
& $Python interop/videocall/preflight.py --check-docker --report build/videocall-preflight/media.json
```

Use installed Chrome initially; record the actual browser version. The preflight
is a source/environment check, not an overall project runtime verdict. Read its
scope before interpreting its static `runtimeVerified`/pending fields.

Re-run `connect_ui.py` once into a fresh evidence directory before extending it:

```powershell
& $Python interop/videocall/connect_ui.py --node $Node --output build/videocall-media-baseline
```

If this fails, diagnose that baseline first. Do not interpret a media failure
against a broken setup as a Legilimens regression. Reuse existing built artifacts;
do not start a long Rust/WASM rebuild unless a demonstrated issue requires it.
Report why any rebuild/install is necessary and record its exact versions.

## Safety and Test Integrity

- Test only owned local services. Never target the public videocall.rs service,
  public meeting rooms, public OAuth, analytics or unrelated applications.
- Publish service ports only on `127.0.0.1`. Keep PostgreSQL/NATS unpublished.
  Use uniquely labelled owned containers and a dedicated bridge. On this Docker
  setup `--internal` previously broke Windows port publication; the regular bridge
  is not an outbound-deny firewall. Retain browser local-origin request guards.
- Preserve room authorization, exact credentialed CORS, local CSP and COOP/COEP.
  The existing CSP intentionally blocks hardcoded upstream analytics. Log that as
  a blocked attempt, not evidence of no attempted analytics.
- Use fresh short-lived certificates. The browser trusts only the exact owned
  endpoint pin; Legilimens verifies the exact upstream pin. No global TLS ignore
  flag, `ignoreHTTPSErrors`, `INSECURE=true` or disabled web security.
- Use only synthetic camera/microphone inputs. Never access physical devices or
  the user's normal browser profile. Silence physical playback if necessary while
  preserving the software decoding/playback path under observation.
- Use disposable identities/secrets. Redact JWTs, cookies, passwords and control
  tokens from reports, console output and saved logs. Raw browser traces/HARs can
  contain tokens: do not retain unsanitized traces merely for convenience.
- Preserve native codec, worker, transport and playback behavior. Observers must
  not create decoded results, replace decoder outputs, draw reference images onto
  receiving canvases or inject generated audio into the receive pipeline.
- Do not retry until green and discard failures. Record all attempts and explain
  fixes before starting a new validation series.

## Implementation Shape

Suggested new entry points (create them; these commands do not exist yet):

- `interop/videocall/run_media.py`: owned-service orchestration, deadlines,
  evidence aggregation and cleanup; accept `--node` and `--output`.
- `interop/videocall/media-browser.mjs`: two-browser real UI workflow.
- Small media fixture/observer/verdict helpers only where useful for testing.
- Focused regression tests under `interop/tests/`.

Reuse the current harness helpers and exact-pin adapter. A small extraction of
shared service setup is acceptable if it avoids another large copied runner;
preserve the existing connectivity entry point and rerun its tests if touched.
Do not build a general test framework or redesign the frontend for this task.

### Phase 1: Distinct, deterministic source media

Generate two reproducible media fixtures, one per participant:

- Video: visibly different participant markers, changing frame counters or
  patterns and a run-specific marker. Use a modest resolution/frame rate; begin
  with approximately 640x360 at 10 fps if supported by the real application.
- Audio: different known, repeating voice-band tone sequences with deliberate
  sound/silence intervals. A single nonzero RMS value is NOT an identity check.
  Choose sequences long enough to distinguish them from join notification sounds.
- Store generation parameters, seed, duration, format and SHA-256 hashes. Do not
  require byte-identical decoded output from lossy codecs.

Prefer two separate Chrome processes with distinct fake-device fixtures so each
participant gets its own inputs. Verify the installed browser actually consumes
the chosen files; silently falling back to the same default fake device invalidates
the identity test. If a scoped synthetic `getUserMedia` adapter is needed instead,
document it and restrict it to the source-device boundary, preserving real media
encoding, networking and decoding. Never patch the receive path to produce media.

Calibrate signal-detection tolerances using fixture/codec behavior, not a failing
proxy run. Declare the final thresholds, warm-up and observation windows BEFORE
the final repeatability series and keep them identical direct/proxied. Document
any earlier adjustments and retain failed evidence.

### Phase 2: Two simultaneous participants, direct route

1. Create A and B with separate browser storage and signed test identities.
2. Use a fresh room. Drive A through the actual home form, API admission and
   second Start Meeting gesture; keep A open.
3. Drive B into the same room. If B waits for admission, admit B using A's actual
   host UI. Do not disable waiting-room authorization or insert database rows to
   skip this workflow. Follow the actual guest UI if its next gesture differs.
4. Enable camera and microphone through real controls; source defaults are off.
   Verify the resulting control state, not just that a click was attempted.
5. Require distinct identities/session IDs and simultaneous server membership.
   Require native WebTransport readiness to the expected direct origin in BOTH
   browsers and no successful media WebSocket connection.
6. Keep both browsers alive during all receiver observations. Sequential one-user
   joins, local previews or sender-side encoding do not satisfy this phase.

### Phase 3: Prove receiver-side decoding

Implement this incrementally: direct video A-to-B, direct video B-to-A, audio in
each direction, then all four media paths in one simultaneous meeting.

Video evidence must include:

- Received decoded VideoFrame evidence from the actual decoder-worker boundary
  or another justified decoded-output boundary, AND actual remote-canvas rendering.
- Peer/canvas attribution showing this is the other participant, not self-preview.
- Detection of the other participant's marker and advancing sequence in multiple
  samples spread across the observation interval. A changing UI screenshot or a
  nonblack pixel is insufficient.
- Observed values/timestamps and bounded representative crops/screenshots so an
  independent reviewer can inspect the result. Record expected values separately.

Source inspection shows decoded video frames are returned from the worker and
drawn onto a 2D canvas in `peer_decoder.rs`. Use passive observation where possible.
Do not close/detach the application's VideoFrame. If an observer makes a clone,
close its own clone and cap sampling/storage overhead.

Audio evidence must include:

- Decoded PCM samples from the active remote audio pipeline, with peer/direction
  attribution and the expected OTHER participant's tone sequence over time.
- At minimum, identity/correlation or frequency-sequence measurements, sample
  counts and timestamps. A speaking indicator, incoming packet count, workerReady,
  AudioContext creation, nonzero RMS or notification beep alone is insufficient.
- Evidence that the real playback pipeline is initialized/running and fed decoded
  samples. Do not claim that physical speakers were tested or audibly verified.

Inspect the actual NetEq/worker/playback path used by this browser first. Do not
assume wrapping main-thread AudioDecoder observes all audio. The appropriate
boundary may be worker PCM messages, a shared buffer or playback-node input.
Preserve existing listeners, transfers, timing and sample delivery. Cap collected
samples and record exactly where observation happens.

If reliable decoded audio cannot be observed without modifying upstream behavior,
record audio as BLOCKED or NOT_RUN and overall success as false. A verified video
result is useful partial evidence; do not rename it a successful audio/video call.

Use a declared sustained observation window (initial proposal: 15 seconds after
bounded warm-up), with repeated identity matches in each direction across the
window. Define numeric frame/audio thresholds in the test specification before
the final series. This is a functional gate, not a latency/FPS quality benchmark.

### Phase 4: Controls that catch false positives

For each sender, use the actual UI to mute audio and disable video, while keeping
the connection and the other participant active. After a bounded drain interval:

- The receiver must stop observing NEW matching frames from that sender. A frozen
  last frame may remain visible; do not require the canvas to become black.
- The receiver must stop detecting that sender's live audio sequence; allow an
  explicitly bounded buffer/decoder-concealment tail, not indefinite activity.
- Re-enable the media and require fresh sequence progression and audio identity
  matches to return. Do not pass based on cached samples.

Also unit-test verdict/observer logic with no observations, frozen/wrong/self
markers, unrelated tones, missing directions, missing reports, worker errors,
timeouts, nonzero child exit and missing cleanup. Expected failures must be tied
to an exercised precondition, not inferred from silence after an unrelated error.

### Phase 5: Repeat unchanged through Legilimens

Only after a working direct media baseline, repeat the same meeting workflow,
fixtures, thresholds, controls and observation windows with BOTH browser media
connections directed to Legilimens. REST meeting traffic still uses the real API.

- Pin browsers to the proxy certificate and the proxy to the upstream certificate.
- Verify the browser transport origin is the proxy, not the direct target. Record
  matching proxy/upstream session evidence without persisting room tokens.
- Require all four media paths: A-to-B video/audio and B-to-A video/audio.
- Keep tamper and manual holds disabled for this forwarding baseline. A proxy
  capture containing bytes is supplementary evidence, not decoding proof.
- Do not test media modification, binary replay, capture export/import, room
  isolation or reconnect here; those are later milestones.

After fixes, require three consecutive complete direct-plus-proxied runs on the
same final code/config, using fresh identities/rooms/resources each time. Keep
earlier failed diagnostic runs and report any remaining flakiness. If a complete
repeatability series cannot finish, report exactly how many runs completed.

## Deadlines, Cleanup and Failure Classification

Give every readiness/action/observation phase a timeout. Choose a documented outer
deadline that covers the phases (for example 180 seconds per route), rather than
sleeping indefinitely. Observe conditions, not a fixed sleep followed by success.
Longer build deadlines must be separate from functional test deadlines.

Always attempt all owned cleanup actions, even if one fails: browsers and child
process trees, UI server/thread, containers, network, temporary profiles and secret
files. Verify TCP/UDP port release with the correct protocol and record cleanup
errors. Never kill unrelated Chrome, Docker containers or user services. On Windows,
validate absolute paths before recursive removal and keep removal in one shell/API.
Retain reusable images/caches; no Docker prune, factory reset or broad deletion.

Write evidence even on exceptions and timeouts. Use unique run directories so a
stale report cannot satisfy a new run. Do not swallow child failures or ignore
their exit codes. Preserve sanitized, bounded diagnostics before teardown.

Use these case statuses consistently:

- PASS: specified observations and thresholds actually satisfied.
- FAIL: exercised case violated a criterion, timed out after prerequisites were
  ready, or its verification logic failed.
- BLOCKED: a prerequisite/observation capability prevented a meaningful test.
- NOT_RUN: never attempted, including proxy cases deferred after direct failure.

Suggested runner exits: 0 only for every required case passing with verified
cleanup; 1 for a test/verifier failure; 2 for blocked/incomplete execution.
Overall `passed` must be false for missing, skipped, blocked or unknown results.
A direct baseline failure is not evidence of a proxy defect. A direct pass with a
proxy failure warrants diagnosis, not an automatic claim of the root cause.

## Evidence Contract

Each unique run should contain:

- `report.json`: overall status, route/case verdicts, exact versions, fixture
  hashes, thresholds, durations, cleanup and evidence paths.
- `direct.json` and `proxied.json` (or explicit NOT_RUN entries): per-participant
  auth/admission, transport origins, server membership and four media-path results.
- Bounded receiver observations, video samples/screenshots and audio measurements.
  If sample snippets are retained, they must contain synthetic media only.
- Sanitized browser/worker/server/proxy diagnostics and exact repro commands.
- A concise explanation of every test-only adapter, where it runs, what it
  observes/modifies, and how false-positive controls validate it.

Required per media-path fields: sender, receiver, media type, status, expected
source identity, observed identity/match metrics, observation timestamps/counts,
thresholds, evidence references and failure reason when not PASS. Never replace
observed data with the expected fixture values in the report.

Record route/browser/worker/codec configuration actually observed, not inferred
from installed package names alone. Include the upstream revision/clean state,
Docker image IDs, browser/Python/Node versions and whether artifacts were rebuilt.
Do not retain JWTs or credentials as part of the configuration snapshot.

## Regression and Documentation Requirements

- Add focused tests for new verdict/fixture/observer helpers and failure paths.
- Rerun the existing JS policies and Python UI tests with exact pass/skip counts:

```powershell
& $Node --test interop/tests/connect-policy.test.mjs interop/tests/verify-policy.test.mjs
& $Python -m unittest discover -s interop/tests -p test_videocall_ui.py -v
```

- Run any new tests you add. Rerun connectivity after modifying shared setup,
  serving, auth or pinning helpers. Rerun focused backend/frontend tests if their
  product code is changed. Do not claim a full regression run unless it occurred.
- Confirm upstream source is still unchanged; run preflight and whitespace checks.
- Update `doc/VIDEOCALL_INTEROP.md`, `doc/RELEASE_READINESS.md` and `TASKS.md` with
  exact verified scope. Partial results stay partial; do not mark the whole
  videocall milestone done while isolation/reconnect/capture tasks remain open.
- Create `doc/VIDEOCALL_MEDIA_RESULTS.md` using the handoff format below, including
  absolute evidence paths. It must be useful for review without repeating the run.

## Final Handoff to Codex and the User

End your response with this structure and also save it in the results document:

```text
Overall verdict: PASS / FAIL / BLOCKED / PARTIAL
Scope: real two-participant local synthetic media; direct and proxied
Upstream revision and clean status:
Browser / runtime / image versions:
Rebuilt artifacts: yes/no, with reason

Changes made:
- File paths, purpose and any product-runtime changes
- Every test-only adapter and its boundary

Results (report each validation run, not just the best one):
Route | Run | A->B video | B->A video | A->B audio | B->A audio
Direct  | ...
Proxied | ...
Include simultaneous membership, native WT/no successful WS fallback,
mute/video-off/resume controls and cleanup results for each route/run.

Receiver evidence:
- Video: measured identity/progression, remote-canvas attribution, evidence paths
- Audio: actual observation boundary, measured identity/sequence, evidence paths
- Thresholds and observation duration

Regressions: exact commands, passed/failed/skipped counts
Failed attempts: what failed, reproduced cause or hypothesis, fix and rerun
Cleanup: owned resources removed, ports released, anything left running
Reproduction: exact PowerShell commands and prerequisites
Evidence: absolute paths to reports, logs and screenshots/samples
Not established: explicit list; use "not confirmed" when appropriate
Remaining blocker/next action: precise and bounded
```

Do not say "all done" from process exit zero alone. Explain why the evidence proves
the other participant's decoded media, and disclose any unverified direction or
signal. Do not claim public-service compatibility, real-device quality, audible
speaker output, performance guarantees, successful media tampering, universal app
support or production readiness from this experiment.

After saving the report, tell the user to bring its summary/path back to Codex.
Do not attempt to message another agent/chat without user authorization.
