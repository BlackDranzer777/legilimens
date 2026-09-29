# Audio Stage-1 Review and Verification

Verified: 2026-09-29. Scope: test harness only.

Status: offline evaluator hardened and standalone Chrome observer mechanics verified.
NOT integrated into the live-call verdict. Real-call receiver audio decode, remote
mute/resume, and speaker playback remain unverified. No Docker/live-call run or
upstream rebuild was performed for this review.

This record supersedes conflicting Stage-1 implementation/test claims in
[the audio plan](VIDEOCALL_AUDIO_PLAN.md). Its integration sections remain plans.

## Findings Fixed

- A delivery stall could be forgotten after samples resumed. Arrival gaps and stale
  delivery now set sticky failure flags, including a gap between polling calls.
- Reusing the same snapshot could append the same samples again. Absolute sample
  cursors now deduplicate exact repeats and overlapping ranges without refreshing
  liveness.
- Mute evaluation could accept loud DC or unrelated audio because it checked only
  expected tones. Silent mode now rejects any classified non-silent content and
  requires measured PCM coverage. Missing PCM is BLOCKED, not proof of mute.
- Correct symbol order with incorrect tone/gap durations could pass. The evaluator
  now checks tone lengths, gaps, cyclic file-tail timing, and proportional coverage.
- A previously passing worker could mask a replacement. Replacement, ambiguity,
  counter regression, identity changes, missing ranges, and invalid timestamps now
  fail the direction.
- Consumer accumulation was unbounded. Worker count, phase duration, retained
  samples, observer rings, and arrival metadata now have explicit bounds.

Changes are confined to `media-audio.mjs`, the audio portion of `media-probes.mjs`,
their two JavaScript test files, and documentation. The existing fixture generator
and live media orchestrator were not changed during this fix.

## Integration Contract

1. Start each observation phase with `newAudioState({ startMs, cursors })`.
   Obtain current sample cursors at the phase boundary to exclude warmup samples.
   Browser arrival times, phase start, and snapshot times must share the same clock.
2. Poll `sampleAudioObservers(cursors)` and advance the caller's per-worker cursors.
   Feed snapshots into `consumeSnapshot(state, snapshot, nowMs)` regularly, before
   the three-second observer ring can overwrite unconsumed PCM.
3. Each returned sample interval carries contiguous absolute sample ranges and
   per-message `arrivedAt` timestamps. Do not replace these with polling timestamps
   or silently concatenate across missing samples.
4. Use `evaluateDirection` for an observed phase, with its actual duration and the
   OTHER participant's run-specific signature. `evaluateAudioWindow` alone checks
   signal content, not wall-clock freshness or worker lifecycle.
5. Preserve failures. A later good buffer must not erase gaps, stalls, overflow,
   replacement, ambiguity, or capacity failures in the same phase.

Current provisional bounds: 48 kHz PCM; at most eight workers; three seconds of PCM
per observer ring; at most 1,024 arrival records per worker; a maximum 30-second
phase and 1,440,000 retained samples across the consumer's workers. Metadata eviction
also exposes lost sample coverage. Active evaluation requires 90-110% duration
coverage and fresh samples in each wall-clock bucket, in addition to the signature.
These are test-observation budgets, not whole-process memory guarantees.

Thresholds have NOT been calibrated against actual decoded Opus audio. Fix them
before the direct/proxied comparison and keep them identical across routes; do not
weaken a failing assertion to manufacture a pass.

## Verification

- JavaScript: **90 passed, zero failed, zero skipped**, including the existing
  native-video-frame Chrome probe and the strengthened transferred-PCM Chrome probe.
- Python fixture and media-result suites: **11 passed**.
- No full backend/frontend suite was rerun; neither application layer changed.

Reproduction on this machine (PowerShell):

```powershell
$env:MEDIA_BROWSER_TEST='1'
$env:PLAYWRIGHT_MODULE='file:///C:/Users/divya/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
& 'C:\Users\divya\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test interop/tests/connect-policy.test.mjs interop/tests/verify-policy.test.mjs interop/tests/media-policy.test.mjs interop/tests/media-audio.test.mjs interop/tests/media-audio-observer.test.mjs
.venv/Scripts/python.exe -m unittest interop.tests.test_media_fixtures interop.tests.test_media_results
```

The standalone Chrome test transfers three distinct 480-sample Float32 buffers from
a synthetic worker. It verifies sender-side detachment, unchanged app-side samples
and delivery counts, equal observer copies, and arrival metadata. This establishes
passive-read integrity for that mechanism, NOT execution of the real NetEq decoder.
An offline production-observer-to-evaluator test also passes an eight-second stream
of synthetic messages. Neither is evidence of a live call.

## Next Work

- Wire bounded audio polling into `media-browser.mjs` for simultaneous receivers.
- Implement a separate mic-only mute/resume phase. NetEq may stop emitting PCM on
  mute: missing PCM alone cannot distinguish intentional mute from a dead worker or
  broken observation. Require independent receiver/worker liveness and resumed
  expected-source decoding, or keep that result BLOCKED. The current silent-mode
  evaluator proves measured silence only, not this no-emission case.
- Preserve source/session attribution, transport checks, runtime-error rejection,
  and strict result propagation. Require direct A/V success before counting a
  proxied comparison as successful.
- Run the labelled lifecycle-patched fixture locally, retain per-direction evidence,
  then compare direct and proxied routes under identical assertions. Keep original
  unpatched-app results separate. Repeated complete A/V runs remain pending.

The last real-app evidence remains the paired video-only success recorded in
[the lifecycle diagnosis](VIDEOCALL_LIFECYCLE_DIAGNOSIS.md). Overall A/V remains BLOCKED.
No media tampering, arbitrary-app compatibility, production readiness, or audible
speaker-output claim follows from this Stage-1 verification.
