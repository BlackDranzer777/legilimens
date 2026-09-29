# videocall.rs Two-Participant Media Verification — Results

Updated 2026-09-28 after review and lifecycle/proxy follow-up. The latest section
and linked investigation are authoritative; the initial experiment is historical and its
direct-PASS/proxy-cause conclusions are superseded. No production, audible-quality
or full application compatibility claim. See `CLAUDE_VIDEOCALL_MEDIA_HANDOFF.md`.

## Latest Verdict: BLOCKED (Patched Fixture)

The separately labelled lifecycle-patched build passes both direct and proxied
simultaneous decoded-video windows (30 samples in each direction) and both senders'
video-off/resume controls in `lifecycle-stream-cleanup-2`. No unexpected browser
errors; native transport and server attribution verified; cleanup passed. Fixed
proxy completed-stream mappings and pinned-library send-only retirement without
raising limits. One already-closed waiting-room connection hit a resource cap before
the windows; teardown and long-duration metadata retention remain follow-ups.
Audio decoding/remote mute, capture roundtrip and media tampering remain unverified.
One paired video run is not complete A/V validation or unchanged-app compatibility.
Use [the lifecycle investigation](VIDEOCALL_LIFECYCLE_DIAGNOSIS.md) for exact
patch/build provenance, regressions and the full ledger including failed runs.

## Historical Unpatched Verdict: FAIL

The earlier unpatched direct route shows sustained simultaneous decoded video in both
directions and working video-off/resume controls, but the client raises WASM
runtime errors. A clean direct baseline is therefore **not established**. Remote
audio decoding remains **BLOCKED**. The corrected proxy comparison is **NOT_RUN**,
not a proxy failure or success. No proxy settings, resource limits or upstream
source were changed in this follow-up.

### Initial Direct-Client Investigation (Historical)

The next diagnostic run now records timestamped admission/start/control phases
and bounded, JWT-redacted browser console messages (up to 800 records, with a
dropped-record counter). `run_media.py --direct-only` explicitly excludes the
proxy comparison and cannot pass the complete gate. These additions do not suppress
runtime errors or modify the app's transport/media behavior.

Source inspection found a concrete candidate in the pinned upstream
`videocall-transport/src/webtransport.rs`: `WebTransportTask::drop` closes the
native transport, then task-owned Rust closures are dropped while promise handlers
registered by `connect_common` still reference them. In saved run
`harness-review-fixed-2`, B's first transport closed at 1790557309942 and the
dropped-closure error followed at 1790557309965 (23 ms later). This correlation
does not yet establish the cause of both errors or rule out admission races.

Next reproduction: collect the new direct timeline/console evidence, isolate
drop-before-promise-settlement in a minimal WASM test, then validate a narrowly
scoped lifecycle fix if confirmed. Any upstream test patch must be separately
identified, leaving the pinned checkout and original build evidence intact.

The Docker engine initially failed to answer its status request. The owned stalled
CLI processes were stopped, then the user restarted Docker Desktop. Engine access
is now verified and live runs resumed. See the lifecycle investigation linked above
for their results. The initial diagnostics-only checks passed 50 JS tests (one
opt-in Chrome probe skipped) and nine Python tests; newer counts are in that ledger.

### What Was Corrected

- Runtime/worker errors, missing evidence, unsuccessful cleanup, closed transport,
  successful media WebSocket fallback and inconsistent child exits reject success.
  Video observations are distinct from complete A/V success; absent audio or remote
  mute/resume verification cannot yield PASS.
- Both video directions are sampled concurrently in one shared 15-second window.
  Each 3-second bucket requires at least three valid samples, changing native
  VideoFrame timestamps, advancing draw counts and motion-bar range of at least
  0.025. At least 90% sample coverage and a maximum 1.6-second gap are required.
- The remote tile must belong to the other authenticated user. Its stable canvas
  must show that participant's color and a run/participant-specific 16-bit fixture
  marker. The canvas ID is corroborated against actual transport-server room/user
  session logs. Multiple canvases, self-preview, stale run markers and early bursts
  followed by freezes cannot pass.
- A passive wrapper records the application's native `drawImage(VideoFrame)`
  calls, preserving arguments/results and frame ownership. It neither creates
  received media nor alters codec output. A weak map avoids retaining canvases.
- Each sender is muted and video-disabled using real controls. After a 3-second
  drain, a 6-second window must show stopped remote video while the other video,
  peer membership and native transport remain live. Video/audio UI controls are
  restored, then both video directions must pass another 15-second window.
  Mic UI state is not proof of remote silence or decoded audio.
- Startup/resume waits at most 30 seconds for identified native-frame progression;
  this is separate from, and does not weaken, the sustained observation window.
- Evidence directories must be fresh. All bounded observations, errors, screenshots,
  child logs and sanitized proxy logs are retained. Owned cleanup and released ports
  are checked. A clean explicit video-only baseline can permit a diagnostic proxy
  comparison, but incomplete audio still prevents complete A/V success.

Files: `media-policy.mjs`, `media-probes.mjs`, `media-browser.mjs`,
`media_results.py`, `media_fixtures.py`, `run_media.py` under `interop/videocall/`;
focused tests under `interop/tests/`. Existing fixture verification is reused.

### Live Results

| Evidence directory under `build/videocall-media/` | Direct observations | Overall / comparison |
| --- | --- | --- |
| `harness-review-fixed-1/` | A-to-B video FAIL (startup frames missing); B-to-A PASS; controls NOT_RUN; client runtime errors | FAIL; proxy NOT_RUN; cleanup verified |
| `harness-review-fixed-2/` | Both directions PASS, 28/28 matching samples each in the shared window; both senders' video-off/resume and mic UI checks PASS; session attribution verified | FAIL due to direct-client runtime errors; audio BLOCKED; proxy NOT_RUN; cleanup verified |

Run 1 used the old fixed six-second startup delay. Run 2 used bounded decoded-frame
readiness (about 12.7 seconds in this run); the sustained thresholds stayed unchanged.
Run 1 is retained, not discarded. Both used real Dioxus UI, two separate Chrome
processes, real authenticated local admission, exact endpoint pins and synthetic
source-device files. Existing compiled artifacts were reused. Upstream revision
`31a8b2076e7228f1b846fe4ed75dc70ffc27eb44` remained unchanged (preflight verified).

Run 2's participant B reported:

```text
Error: closure invoked recursively or after being dropped
RuntimeError: unreachable
```

These occurred on the **direct** route. Their root cause is not isolated: do not
label them a Legilimens defect or an established upstream bug yet. The absence of a
clean baseline prevents a defensible proxy-specific diagnosis. Video observations
remain useful evidence, but `videoPassed` is false because overall video health
includes runtime safety. Both the browser driver and orchestrator exited 1.

### Verification and Evidence

- New media JS tests: **33 passed**, including an opt-in real Chrome native-frame
  probe. That isolated probe uses a synthetic test document, not the real app.
- New Python fixture/verdict tests: **9 passed**.
- Existing connection/startup JS policies: **18 passed**.
- Existing Python UI tests: **3 passed, 1 opt-in browser fault test skipped**.
- No full backend/frontend regression-suite or fresh frontend rebuild claim.

The initial Chrome probe fixture failed on `about:blank` because it lacked the
required secure-context API; the test was corrected to a routed loopback document
and rerun successfully. The final audio-control completeness checks and retention
of partial control diagnostics were regression-tested after the latest live run;
they do not change its failing verdict. No third live run was performed.

Current artifacts (gitignored, retained locally):

- `build/videocall-media/harness-review-fixed-2/report.json` and `direct.json`:
  final verdict, full windows, controls, transport state, stacks and session mapping.
- `direct.json.A.png` and `direct.json.B.png`: actual application screenshots.
- `direct-driver.log`, `proxy.log`, `child-*.log`, container logs: diagnostics.
- `fixtures/fixtures.json`: input markers, parameters and SHA-256 hashes.
- `build/videocall-preflight/media-harness-review.json`: source/build preflight.

Use the reproduction command below with a **new empty output directory**. The
driver deadline is 300 seconds; the complete runner also performs setup and cleanup.

### Next Steps

1. Reproduce and isolate the direct client's WASM errors. Correlate first error,
   admission/control events and transport closure; distinguish fixture/automation
   effects from application defects without suppressing errors or patching evidence.
2. Once the direct video baseline is clean, run the same criteria through Legilimens
   and diagnose any difference using the retained proxy/server/browser logs.
3. Add passive remote decoded-PCM measurement and independent audio mute/resume
   controls. Source microphone levels or UI indicators alone cannot establish it.
4. Complete room isolation/reconnect, application capture roundtrip and three
   consecutive complete direct+proxied A/V runs. Installer validation remains later.

## Historical Initial Experiment (Verdicts Superseded)

The remainder preserves the initial run's reported observations, environment and
commands for traceability. Its PASS labels used a weak gate that ignored direct
runtime errors and allowed sparse/sequential video observations. Do not use these
historical labels or next actions as current release evidence; use the review above.

- **Direct route: two-participant real-UI synthetic VIDEO decode PASS in both
  directions** (receiver renders the *other* participant's identity colour and an
  advancing frame bar, distinct identities, native WebTransport ready, no media
  WebSocket).
- **Proxied route (through Legilimens): video FAIL.** Both participants connect over
  WebTransport through the proxy, but media stalls after a few frames with heavy
  WebTransport reconnection churn — a preserved failing case, not a direct-baseline
  problem.
- **Audio: BLOCKED** (both routes). No non-invasive decoded-PCM observation boundary
  was instrumented; not faked.
- Repeatability (3 consecutive complete direct+proxied runs) **not achieved**: direct
  video reproduced, proxied failed both runs.

## Scope, upstream and versions

- Scope: real two-participant local synthetic media; direct and proxied. Real Dioxus
  UI, real meeting API (cookie session bootstrap = local test identity, not OAuth),
  native WebTransport, real codecs/worker/canvas. No echo server, clone UI, mock
  responses or standalone transport client substituted.
- Upstream revision `31a8b2076e7228f1b846fe4ed75dc70ffc27eb44`; preflight confirms the
  checkout is unchanged. Rebuilt artifacts: **no** (reused existing `build/videocall-frontend/dist`).
- Browser: Chrome `153.0.8010.53`. Node `24.19.0`. Python `3.12.0`. Docker `26.0.0`.
- Image IDs: backend `sha256:a47e6afe2107…`, nats `sha256:e556ce07f949…`, postgres `sha256:570db3b62768…`.

## Changes made (all under `interop/videocall/`, nothing committed)

- `media_fixtures.py` — distinct synthetic fixtures: 640×360 Y4M (I420) at 10 fps with a
  per-participant identity colour + centred letter + advancing bar + per-frame counter;
  48 kHz 16-bit WAV with participant-specific tone/silence sequences. Records params,
  seed, durations, formats and SHA-256.
- `verify-fixtures.mjs` — confirms Chrome consumes the chosen files (identity colour +
  dominant tone), so it is not silently using a default fake device.
- `media-browser.mjs` — two real Chrome participants in one room via the real UI; drives
  home submit → real join API → second gesture (host "Start Meeting" / guest "Join
  Meeting") → host admits guest via `.btn-admit` → real camera/mic controls; observes
  receiver-side decoded video by sampling the *remote peer* canvas for the other
  participant's identity colour and advancing bar in a sustained window.
- `run_media.py` — owned-service orchestration (reuses the connectivity harness's stack:
  NATS, PostgreSQL, meeting API, WebTransport server + Legilimens proxy), fixture
  generation, deadlines, evidence aggregation, owned-resource cleanup; `--node`, `--output`.

Test-only adapters (disclosed): (1) a scoped `serverCertificateHashes` observer for the
exact configured transport origin (the upstream client sets no pin); the browser trusts
only the exact owned endpoint pin, Legilimens verifies the exact upstream pin; no global
TLS ignore/`ignoreHTTPSErrors`. (2) synthetic fake-device fixtures at the source-device
boundary only. No receive-path modification: observers read the application's own canvas
passively and create no decoded results, reference images or injected audio.

## Results (per validation run, not just the best)

Run 1 (`build/videocall-media/run-1790551422/`):

| Route | Run | A→B video | B→A video | A→B audio | B→A audio | membership | transport |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Direct  | 1 | PASS (15 samples, advancing, 8 bar states) | PASS (15 samples, advancing, 6 bar states) | BLOCKED | BLOCKED | both admitted, distinct user_ids | native WT ready, no media WS |
| Direct  | 2 | PASS (13 samples, advancing) | PASS (8 samples, advancing) | BLOCKED | BLOCKED | both admitted, distinct user_ids | native WT ready, no media WS |
| Proxied | 1 | FAIL (7 samples, froze, 2 bar states) | FAIL (0 samples) | BLOCKED | BLOCKED | both admitted, distinct user_ids | WT ready but 11–18 reconnects |
| Proxied | 2 | FAIL (0 samples) | FAIL (0 samples) | BLOCKED | BLOCKED | both admitted, distinct user_ids | WT ready but reconnection churn |

Repeatability: **direct video PASS reproduced across both runs (2/2, both directions);
proxied FAIL across both runs (2/2).** Run 2: `build/videocall-media/run-1790551632-r2/`.
A third consecutive run was not performed (proxied not yet passing, so the required
direct+proxied series cannot complete).

Direct receiver evidence: on B, the remote canvas matched participant **A's red
identity** with the advancing bar sweeping 0.11→0.22→0.35→0.46→0.58→wrap across the
15 s window (8 distinct states) — proof of *live decoded* frames from A, not a frozen
image or self-preview (B's own tile is blue). Symmetrically, A's page showed a **blue,
advancing** remote canvas (participant B). Thresholds: colour match with ≥3 distinct
advancing-bar states across a 15 s window after a 6 s warm-up, identical direct/proxied.

Proxied failure evidence: both browsers reach `wt.ready`, but with 11 (A) / 18 (B)
WebTransport sessions versus 1–3 direct, repeated client `unreachable` wasm panics, and
the received A-frame bar frozen at 0.636. Media forwards briefly, then stalls.
Original interpretation attributed this to proxy forwarding/resource handling.
**Review correction:** that attribution is not established; both historical direct
runs also contain runtime errors. Retain the observations, not the causal claim.

Audio: BLOCKED both routes — the decoded-PCM boundary (NetEq worker / AudioWorklet) was
not instrumented in this pass; per the guide, recorded as BLOCKED rather than inferred
from RMS/indicators.

## Regressions

- `node --test interop/tests/connect-policy.test.mjs interop/tests/verify-policy.test.mjs`
  → **18 passed, 0 failed**.
- `VIDEOCALL_UI_BROWSER_TEST=1 python -m unittest discover -s interop/tests -p test_videocall_ui.py -v`
  → **4 passed**.
- Connectivity baseline re-run (`connect_ui.py`) before extending → **direct + proxied PASS, cleanup verified**.

## Cleanup

Owned containers, network, Legilimens proxy process, UI server thread and temp
directories removed; loopback TCP/UDP ports verified released (`cleanup: true`, no
cleanup errors) in every run. No unrelated containers/processes touched; reusable images
and build caches retained.

## Reproduction (PowerShell)

```powershell
Set-Location 'C:\Users\divya\OneDrive\Divyansh\Development_Security\Legilimens'
$Python = Join-Path $PWD '.venv\Scripts\python.exe'
$Node = 'C:\Users\divya\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$env:PLAYWRIGHT_MODULE = 'file:///C:/Users/divya/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
& $Python interop/videocall/run_media.py --node $Node --output build/videocall-media/run-manual
```

Prerequisites: Docker running with the pinned backend/NATS/PostgreSQL images and the
existing `build/videocall-frontend/dist`; installed Chrome; the Playwright module above.

## Not established / not confirmed

- Audio decode either direction (BLOCKED — observation boundary not built).
- Proxied media (video or audio) — fails; forwarding under media load is unreliable.
- False-positive controls (mute / video-off / resume) — NOT_RUN (deferred until proxied
  and audio are addressed).
- Three consecutive complete direct+proxied runs — not achieved.
- Audible speaker output, real-device quality, performance, media tampering, capture
  export/import for this traffic, room isolation, reconnect — out of scope / not tested.

## Historical Next Actions (Superseded by Review Above)

1. **Proxied media forwarding** is the primary blocker. Bounded next step: capture a
   minimal reproducer of the WebTransport reconnection churn / media stall through
   Legilimens, determine whether proxy session/stream/datagram budgets or lifecycle drop
   the media session under load, make a narrow fix with a regression test, then re-run.
2. **Audio observation boundary**: instrument the NetEq worker PCM messages or the
   AudioWorklet input (test-only, passive) to measure the other participant's tone
   sequence, or keep audio explicitly BLOCKED.
3. Then add false-positive controls (Phase 4) and the 3-run repeatability series.

## Evidence (absolute paths)

- Run 1 report: `C:\Users\divya\OneDrive\Divyansh\Development_Security\Legilimens\build\videocall-media\run-1790551422\report.json`
- Per-route: `…\run-1790551422\direct.json`, `…\proxied.json`; driver logs `…\direct-driver.log`, `…\proxied-driver.log`; container logs `…\*.log`; screenshots for failing routes `…\proxied.json.A.png`/`.B.png`.
- Fixtures + hashes: `…\run-1790551422\fixtures\fixtures.json` (video/audio SHA-256 recorded).
- Baseline connectivity: `…\build\videocall-media-baseline\report.json`.

## Historical Handoff Summary (Not Current Evidence)

```text
Overall verdict: PARTIAL
Scope: real two-participant local synthetic media; direct and proxied
Upstream revision and clean status: 31a8b2076e7228f1b846fe4ed75dc70ffc27eb44, unchanged (preflight passed)
Browser / runtime / image versions: Chrome 153.0.8010.53, Node 24.19.0, Python 3.12.0, Docker 26.0.0; backend a47e6afe2107, nats e556ce07f949, postgres 570db3b62768
Rebuilt artifacts: no (reused existing build/videocall-frontend/dist)

Changes made:
- interop/videocall/{media_fixtures.py, verify-fixtures.mjs, media-browser.mjs, run_media.py} (test harness; no product-runtime change)
- Test-only adapters: scoped serverCertificateHashes observer (exact owned origin); synthetic fake-device fixtures (source boundary). No receive-path modification; no Legilimens runtime change.

Results (per run):
Route   | Run | A->B video | B->A video | A->B audio | B->A audio
Direct  |  1  | PASS       | PASS       | BLOCKED    | BLOCKED
Direct  |  2  | PASS       | PASS       | BLOCKED    | BLOCKED
Proxied |  1  | FAIL       | FAIL       | BLOCKED    | BLOCKED
Proxied |  2  | FAIL       | FAIL       | BLOCKED    | BLOCKED
Simultaneous membership: yes (both admitted, distinct user_ids). Native WT ready, no successful media WS. Mute/video-off/resume controls: NOT_RUN. Cleanup: verified all runs.

Receiver evidence:
- Video: identity colour + advancing bar on the remote peer canvas (A=red, B=blue), 15 samples/8 & 6 distinct bar states over a 15s window; direct only. Evidence: build/videocall-media/run-1790551422/direct.json
- Audio: BLOCKED — decoded-PCM boundary (NetEq worker/AudioWorklet) not instrumented.
- Thresholds: colour match + >=3 advancing bar states across 15s after 6s warm-up.

Regressions: node --test connect-policy+verify-policy = 18 passed/0 failed; unittest test_videocall_ui = 4 passed.
Failed attempts: proxied media stalls with WebTransport reconnection churn (11-18 sessions vs 1-3 direct) and client wasm panics; preserved, root cause not isolated.
Cleanup: owned containers/network/proxy/UI-thread/temp removed; ports released; nothing left running.
Reproduction: see Reproduction section above.
Not established: audio decode; proxied media; false-positive controls; 3-run repeatability; audible/quality claims.
Remaining blocker/next action: diagnose+fix proxied media forwarding (reconnection churn/stall), then instrument audio, then Phase 4 controls and the repeatability series.
```

Use the current verdict and next steps at the top for any new handoff.
