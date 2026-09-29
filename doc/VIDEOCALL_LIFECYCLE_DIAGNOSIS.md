# videocall Client Lifecycle Investigation

2026-09-28. Isolated upstream test-fixture changes and a Legilimens stream-lifecycle
fix. This is not an unchanged-upstream compatibility claim. The pinned checkout
and original frontend are intact.

## Reproduced Failure Paths

The unpatched `direct-lifecycle-trace-1` run reproduced two guest-side errors:

1. `WebTransportTask::drop` in upstream
   `videocall-transport/src/webtransport.rs` closes the browser transport while
   dropping the Rust closures still referenced by its ready/closed promises.
   The browser later invokes a dropped closure. The error coincides with waiting
   room transport teardown, not with traffic passing through Legilimens.
2. The console panic names `dioxus-ui/src/components/waiting_room.rs:99:49`:
   a post-connect status request calls an already-dropped admission handler after
   the waiting-room component has unmounted. Push admission and post-connect/poll
   status completion can race. The exact panic and timestamps are retained.

Both errors disappeared in the first two runs of the experimental lifecycle build.
This is before/after integration evidence, not an exhaustive lifecycle proof or a
standalone Rust/WASM unit-test result. Drop-during-handshake and other timing cases
still merit a focused upstream regression suite.

## Isolated Patch

`interop/videocall/patches/lifecycle.patch` changes exactly those two upstream files:

- Await native promises with `JsFuture`, which retains callbacks through settlement.
  An alive flag cancels notifications after task destruction; a terminal flag
  prevents duplicate close notifications and late opened notifications.
- Guard waiting-room async completions with a shared alive/one-shot flag. Clear it
  on unmount and before accepting admission/rejection, so late callbacks neither
  invoke dropped handlers nor update a completed waiting room.

`build_lifecycle_fixture.py` builds from the cached frontend image, verifies both
normalized source hashes, checks/applies the patch and runs locked offline Trunk.
It exports a separate directory and records base image, resulting image, source
hashes and patch hash in `fixture-build.json`. It refuses to replace an existing
output. Original checkout/build files are not modified. Line endings of only the
two patched files are normalized inside the experimental image.

The first build attempts failed on a local-image reference and then CRLF handling;
no successful build is claimed for those attempts. The successful image is
`sha256:d623e3aac26e95f0a7b8b97dab5b584bd8d493c3c9b39c8183a788b649a73d89`.

## Test Server and Harness Fixes

The test UI's CSP blocked the upstream PCM player's locally generated blob module.
A minimal Chrome test reproduced the same AudioWorklet AbortError without the
meeting/proxy stack. Adding `blob:` to the test server's `script-src` fixes module
registration. The probe also verifies external analytics remains CSP-blocked before
network dispatch. No global TLS bypass, external script allowlist, receive-media
injection or production-security claim is involved.

The media harness now records bounded/redacted console diagnostics and rejects
unexpected console errors, rather than relying only on uncaught exceptions.
Only the exact CSP-blocked analytics load and refused unused local WS endpoint
are expected. Repeated messages are counted without filling the log with duplicates.
Samples align with the common 500 ms clock, eliminating cumulative evaluation
overhead; coverage/motion/freeze thresholds are unchanged. `--direct-only` cannot
pass the complete A/V gate. Custom frontend metadata is included in `report.json`.

## Evidence

All directories below are under `build/videocall-media/` and are retained locally:

| Run | Result |
| --- | --- |
| `direct-lifecycle-trace-1` | Original build: lifecycle errors reproduced, insufficient video sample count; FAIL, cleanup verified. |
| `direct-lifecycle-patched-1` | No page-level WASM errors, initial video passes; A's resume fails, worklet errors persist under old CSP; FAIL, cleanup verified. |
| `direct-lifecycle-patched-2` | New CSP and strict console gate: both simultaneous video windows (30 samples each) and both senders' video controls PASS, no unexpected runtime/console errors. Overall BLOCKED because remote audio is unverified; direct-only, cleanup verified. |
| `lifecycle-comparison-1` | Direct video/control baseline passes again without unexpected errors; proxied route FAILS decoded-frame readiness with repeated transport reconnects and a later browser WASM allocation error. Audio BLOCKED; cleanup verified. |
| `lifecycle-stream-cleanup-1` | Proxy application-table cleanup only: direct video/controls PASS, proxied readiness still FAILS. New logs show transport-capacity closures, not active application-stream exhaustion. Cleanup verified. |
| `lifecycle-stream-cleanup-2` | Both direct and proxied simultaneous video windows PASS (30 samples per direction), both senders' video-off/resume and mic UI checks PASS, native transport and server attribution verified, no unexpected browser errors. Overall BLOCKED because remote audio is unverified. Cleanup verified. One already-closed waiting-room connection hits a resource cap before observation; see caveats below. |

Current scoped regressions: 53 JavaScript tests passed, including native Chrome
frame observation; nine Python fixture/verdict tests passed; four Python UI tests
passed including the worklet/CSP browser test, one unrelated opt-in browser fault
test skipped. JS/Python syntax checks passed. Frontend production build not rerun
in this investigation. Backend results are recorded with the stream fixes below.

## Proxy Stream Lifecycle

The first clean comparison exposed a separate proxy limit: completed streams
remained in its pairing/UI-ID/lock tables. A real loopback QUIC regression failed
on the 65th sequential uni-stream echo (two completed logical streams per echo),
exhausting the 128-entry lifetime budget. This is independently reproduced, not
merely inferred from the video's reconnect pattern.

The proxy now releases completed uni-stream mappings after forwarding FIN. Bidi
streams retain their pairing until both halves have forwarded FIN and completed
capture/interception work. Raw-reply routing registrations are released too.
The active limits remain 128 logical streams / 256 locks; no buffer or task limit
was increased. Resource closures now record a reason and counts in backend logs.
Streams without both required FINs remain budgeted; reset-specific reclamation
and long-duration memory behavior are not established by this fix.

Increasing the regression past the transport cap exposed a second retention issue:
it failed at uni echo index 264 even with application mappings released. The pinned
aioquic 1.3.0 initializes a send-only stream's receive half as unfinished, preventing
normal transport retirement. This is visible in the installed source and
[the versioned upstream implementation](https://github.com/aiortc/aioquic/blob/1.3.0/src/aioquic/quic/stream.py).
The proxy's compatibility workaround marks only locally initiated uni streams'
impossible receive half finished. It does not remove transport streams, discard
send buffers, forge ACKs, or change either bidirectional half; aioquic still owns
retirement after sender acknowledgement. The integration regression now passes
280 sequential uni echoes followed by 280 bidi echoes in one session. It checks
empty application tables and fewer than 16 retained transport streams per proxy
leg. Separate tests preserve half-close routing, held replies, ordered dropped FIN,
and over-budget unacknowledged data. Twenty-seven focused proxy tests pass.
Final full backend run: `python -m unittest discover -s python/tests -q`, 108
tests discovered, 107 passed, one opt-in soak skipped (exit 0). Earlier focused
runs emitted a Windows closing-datagram-transport ResourceWarning; live-harness
owned-resource and released-port cleanup checks passed. No multi-hour soak claim.

In `lifecycle-stream-cleanup-2`, the remaining capacity notice is 273 transport
streams with 72,234 queued send bytes, not a 4 MiB byte-limit breach. The generic
"client transport buffer" label covers both counts and bytes; new numeric diagnostics
disambiguate it. Its session corresponds by connection time to B's waiting-room
transport, which the browser had closed at 1790596207417, before the notice at
1790596210575 and baseline start at 1790596221421. No further capacity closures
appear during the measured media windows. Early teardown of closed/draining peers
remains a follow-up, not proof of failure-free startup or long-term stability.

Only one complete paired video/control run has passed with these fixes. Native
HTTP/3 context retention and QUIC finished-ID history need a longer-workload audit;
the active-stream regression is not a whole-process memory bound. Audio decoding,
remote mute, capture roundtrip and call tampering are still unverified.

## Reproduction

Docker Linux engine, the existing cached baseline images and Playwright/Chrome are
required. Keep original and patched runs separate. The default runner still selects
the original frontend unless explicitly overridden:

```powershell
$Node = 'C:\Users\divya\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
# Build only when this output directory does not exist:
.venv\Scripts\python.exe interop/videocall/build_lifecycle_fixture.py
# Use a fresh output directory for each run; omit --direct-only for comparison:
.venv\Scripts\python.exe interop/videocall/run_media.py --node $Node --direct-only --frontend-dist build/videocall-frontend-lifecycle/dist --output build/videocall-media/lifecycle-new-run
```

Audio receiver attribution/decoded-PCM checks, remote mute controls, complete A/V
repeatability, capture roundtrip and media tampering are not established by this work.
