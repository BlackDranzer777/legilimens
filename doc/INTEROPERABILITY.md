# Independent WebTransport Interoperability

Updated 2026-09-26. Target #1 is a loopback echo fixture built on
quic-go/webtransport-go, independent of Legilimens' aioquic transport stack.
The CLI suite passes. **The strict Chrome suite does not fully pass:** receiving
an empty datagram and receiving subsequent datagrams on that connection time out
both directly and through Legilimens. Do not describe this milestone as an
unqualified browser interoperability pass.

## Verified Scope

| Case | CLI direct / proxied | Chrome direct / proxied |
| --- | --- | --- |
| CONNECT, path/query/Origin reflection | pass / pass | pass / pass |
| Text, Unicode, whitespace, binary datagrams | pass / pass | pass / pass, before empty payload |
| Empty datagram echo | pass / pass | timeout / timeout |
| Nonempty datagram after empty echo | not a separate CLI assertion | timeout / timeout |
| Bidi single-write and 20 KB application-chunked stream, exact bytes + FIN | pass / pass | pass / pass |
| Client uni stream, server-initiated echo stream | pass / pass | pass / pass |
| Two simultaneous clients / browser tabs | pass / pass | pass / pass |
| Reconnect | pass / pass | pass / pass |

Chrome was **153.0.8010.53**, launched headless with a fresh profile. Browser traffic
uses the actual WebTransport API, not a WebSocket transport fallback. Capture uses
the application's authenticated WebSocket, a separate control-plane channel.

These are deterministic local fixture tests, not evidence of compatibility with
arbitrary applications, production deployment, throughput, or attack effectiveness.

## Control-Plane and UI Evidence

The corrected CLI harness uses the real backend and verifies:

- Authenticated capture startup waits for an acknowledgement. Exact payload,
  direction, encoding, size, target, and live session identity must match.
- Two client identities are mapped from unique captured markers to backend UUIDs.
  Replay is tested for each selected session in both directions. The other client
  must receive nothing during the observation window. Server-directed replay also
  requires a receipt from the correct target-side session.
- Conditional tampering preserves the entire expected JSON object except the
  requested field. Target-side byte receipts establish the changed payload reached
  the independent server; return-path tampering alone cannot satisfy the test.
  A nonmatching message must remain byte-identical, including whitespace.
- Manual datagram interception holds traffic before target receipt, then tests
  forward, edit, and drop. A post-drop sentinel verifies the connection remains live.
  Absence checks are bounded observations, not guarantees against arbitrary delays.

The real production UI, served by the owned backend, was also exercised:

- Two actual browser tabs produced distinct captured session IDs.
- Export retained both tabs' traffic despite an active payload filter.
- The downloaded capture preserved payload, encoding, raw size, session, and target.
- The backend access token was absent from the exported file.
- Offline import displayed the exported payloads without backend requests or replay.
- Desktop (1440 px) and mobile (390 px) screenshots were inspected; no document-width
  overflow was detected.

There are no mocked API responses, replaced WebSocket implementations, injected
store events, or disabled certificate checks in this browser test.

## Remaining Empty-Datagram Failure

The browser test sends nonempty cases first, then an empty datagram, then the
nonempty sentinel `after-empty`.

Observed on both direct and proxied Chrome connections:

1. Nonempty text and binary echo succeeds before the empty case.
2. The Go fixture records receiving the empty datagrams.
3. Chrome's datagram reader does not deliver the empty echo or subsequent sentinel.
4. Stream echo still succeeds on the same connection.
5. Closing and reconnecting restores normal datagram echo.

The proxied capture additionally records **incoming and outgoing** empty datagrams
and sentinel payloads. This narrows the failure to the browser-facing return path;
it does not prove the exact browser/network implementation cause. A browser-side
empty-datagram handling defect is a hypothesis, **not confirmed**. The original
binary failure was order-dependent: binary succeeds when sent before the empty case.

The test continues through two-tab and capture-file checks but retains these four
failing assertions in the report and exits **1**. No empty payload is silently
dropped, padded, rewritten, or excluded from the acceptance gate.

The separate native-API probe in `interop/harness/empty_probe.mjs` reproduces the
failure without the suite's receive pump or retry logic: `[1,2,3]` echoes, the empty
payload times out, and `[4,5,6]` also times out on the pending read. The target records
all three. This probe runs directly against Go, without the Legilimens proxy.

### Browser Comparison Follow-Up

Edge **154.0.4258.37** reproduced all four empty/post-empty failures, directly and
proxied. Its nonempty/stream/two-tab/reconnect and actual capture-file UI cases passed;
owned service cleanup passed. Both tested products use Chromium, so this is not a
comparison against an independent browser engine.

The minimal probe now tests whether the native datagram readable accepts a BYOB
reader, then releases it before the actual default-reader test. Edge reports a byte
readable. A separate diagnostic profile enabled the specifically named
`WebTransportDatagramsReadableType` feature and tried both default and explicit byte
readers. Both still reported byte readables and failed; the requested feature did
not produce a usable non-byte path in this installed browser. It is **not a fix**.

Current [upstream Chromium source](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/third_party/blink/renderer/modules/webtransport/web_transport.cc)
contains distinct byte/non-byte datagram sources, with the latter controlled by an
[experimental feature](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/third_party/blink/renderer/platform/runtime_enabled_features.json5).
This supports investigating reader behavior, but does not identify the exact
installed-binary defect. No browser flags are added to Legilimens or normal tests.

To reproduce the Edge comparison, set `INTEROP_CHROME_CHANNEL=msedge`. To include
the separate experimental reader probe, also set `INTEROP_READABLE_DIAGNOSTIC=1`.
Use separate scratch/report paths so previous evidence remains available. The normal
suite's failures still determine exit status; diagnostic results cannot override it.

Next: isolate the installed browser's receive path with browser/wire diagnostics or
a genuinely independent browser engine before selecting a fix. Meanwhile the pinned
second-target setup is recorded in [videocall preparation](VIDEOCALL_INTEROP.md).
Do not alter faithful proxy forwarding to hide this failure.

## Harness Corrections

Earlier four-run success claims came from a weaker harness and are superseded here:

- Both direct and proxied expected results now gate success; comparisons are recomputed.
- Selected-session replay is no longer inferred from an unlabelled XOR.
- Substring checks were replaced by exact bytes/full-object checks and target receipts.
- A continuously draining, bounded process-output reader enforces readiness deadlines.
- UDP and TCP ports are allocated and checked using their actual socket protocols.
  Readiness requires bound listeners; cleanup failure makes the run fail.
- Missing scratch/report directories are created; owned certificate directories are
  removed after each run.
- The Go fixture now advertises HTTP/3 ALPN. Chrome exposed its missing TLS setting;
  the CLI driver now rejects missing HTTP/3 ALPN as well.
- Multi-chunk tests now issue multiple application writes and a separate FIN.
- Builds use readonly Go modules instead of allowing dependency-lock mutation.

The negative integration test deliberately changes replay to the **other** session.
The harness correctly fails that check while still cleaning up its processes,
ports, and temporary certificates.

## Versions and Trust

Pinned target dependencies: Go 1.27.1, webtransport-go v0.13.0, quic-go v0.62.0.
See `interop/target/go.mod` and `interop/target/go.sum`.

The target generates an ECDSA P-256 leaf certificate with localhost/127.0.0.1 SANs
and a lifetime under 14 days. CLI clients trust the exact endpoint PEM. Chrome uses
per-transport SHA-256 DER pins. Legilimens pins the upstream certificate. Control API
and capture authentication remain enabled. No global trust-store changes or
certificate-verification bypass flags are used.

A test cookie was set for the browser origin. The target reported
`cookiePresent: false` for both browser paths; cookie-based application authentication
is **not established**. The fixture intentionally accepts all Origins; reflecting
Origin is not an origin-rejection/security test.

## Reproduce (PowerShell)

Use the project Python environment, a Go toolchain, Node 24, Chrome, and an existing
Playwright installation. Build the production frontend before the browser run.

```powershell
.venv\Scripts\python.exe interop/build_target.py
npm --prefix client run build

# CLI/control suite: expected exit 0.
.venv\Scripts\python.exe interop/harness/run_interop.py --scratch build/interop-hardening --report build/interop-hardening/reports/cli.json

# Optional: point PLAYWRIGHT_MODULE to an installed Playwright module's file URL.
# Otherwise normal Node module resolution must find "playwright".
# Node defaults to PATH; --node accepts an explicit Node 24 executable.
# Strict Chrome suite: currently expected exit 1 for the documented empty case.
.venv\Scripts\python.exe interop/harness/run_interop.py --scratch build/interop-hardening --report build/interop-hardening/reports/browser.json --browser

# Harness regression tests, including deliberate wrong-session fault injection.
$env:INTEROP_INTEGRATION = '1'
.venv\Scripts\python.exe -m unittest discover -s interop/tests -v
Remove-Item Env:\INTEROP_INTEGRATION

.venv\Scripts\python.exe -m unittest discover -s python/tests
npm --prefix client test
```

Ports are dynamically allocated on 127.0.0.1; startup races fail rather than
attaching to another process. The harness owns only the services it starts.
Browser output is under `<scratch>/browser/`: result.json, the actual exported
capture, live.png, and offline screenshots. Reports/artifacts are gitignored.
The runtime token is passed through the child environment, not a URL or command argument.

## Verification and Limits

2026-09-26: 18 harness tests passed including opt-in real wrong-session fault injection;
100 backend tests passed, one optional soak skipped; 58 frontend tests
passed. The production build passed during the preceding fix run.
The browser workflow reached completion with capture roundtrip true and the four
explicit empty/post-empty failures above. Owned service ports were released.

The subsequent Edge/preflight follow-up ran 24 harness/preflight tests: 23 passed,
one opt-in real fault-injection test skipped (that test passed in the preceding run).

A 4096-byte CLI datagram remains informational: no echo was observed directly or
proxied. A timeout alone does not establish a datagram-size-limit cause.

Still pending: the empty-datagram compatibility investigation, self-hosted
videocall.rs, additional stacks/browsers, browser certificate rotation, extended
resilience, packaged desktop, and clean-machine validation. Binary/stream replay
and application-message reassembly are not implemented by these tests.
