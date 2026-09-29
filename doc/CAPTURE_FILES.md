# Capture Files

Implemented and verified locally on 2026-09-26. This is a retained-event archive,
not a packet capture, server-side recorder, or complete investigation database.

## Workflow

1. In Traffic, use the download icon ("Export all retained events"). Confirm the
   sensitive-data warning. Export ignores the visible search/filter and includes
   every event still in this browser tab's bounded traffic store.
2. Use the folder icon ("Open offline capture"), or the same action on the backend
   access screen. No backend token is required to read a saved file.
3. Use the offline folder icon to select a `.capture.json` file. Search, filter,
   and expand rows; Enter/Space also expands a focused row. Expanded archived
   payloads display the stored string without JSON reformatting.
4. "Back to live workspace" explicitly returns to the normal access gate/live
   UI. Import never copies archived events into the live store, and archived
   session IDs are never used as replay destinations.

Opening offline mode unsubscribes the UI's capture WebSocket and records a gap
warning. It does not stop the backend, connected clients, an existing simulator
run, or an already-established browser test channel. Those may continue running.
The offline reader makes no control requests and offers no replay/attack controls.
The UI itself still needs to be served; "offline" does not imply a packaged
standalone file viewer or that all other browser/backend activity stops.

## Format v1

UTF-8 JSON, `format: "legilimens-capture"`, `version: 1`, and
`source: "retained-browser-events"`. Unknown versions and unknown/missing fields
are rejected instead of partially imported.

| Field | Meaning |
| --- | --- |
| `exportedAt` | Browser export time in Unix milliseconds |
| `observationStartedAt` | Browser-store initialization or last clear time, not connection establishment |
| `events` | Ordered retained traffic events, at most 500 |
| `loss.completeness` | Always `"not-confirmed"`; zero recorded losses does not prove completeness |
| `loss.evictedEvents` | Browser traffic events evicted since last clear |
| `loss.evictedStreams` | Browser stream summaries evicted since last clear |
| `loss.omittedStreamChunks` | Sum of omitted chunks in the stream summaries still retained; not a lifetime total |
| `loss.warning` | Latest retained capture/resource warning, or `null`; not a complete gap history |

Each event explicitly contains `id`, `type`, `direction`, `payload`,
`payloadEncoding`, `rawSize`, `timestamp`, `latency`, `sessionId`, `streamId`,
`target`, and `flag`. Session/stream identifiers refer to the recorded session,
not a currently connected destination. `incoming` means client-to-upstream and
`outgoing` means upstream-to-client, matching the existing live log.

`payloadEncoding` is `utf8`, canonical `base64`, or `null` when not confirmed.
Whitespace, empty payloads, Unicode, and binary bytes are preserved. UTF-8 text
with unpaired UTF-16 surrogates and malformed/noncanonical Base64 are rejected.
`rawSize` is the original event's reported input size; it can differ from the
stored payload size after tampering or for synthetic status/drop events. It is
not a hash, transfer acknowledgment, or integrity check.

New proxy data/replay/drop events carry the session's upstream host/port snapshot.
Changing target settings does not relabel events from an existing connection.
Initial connection notifications before upstream selection, older captures, and
other events without target metadata use `null` (displayed as "not confirmed").
The format does not store private certificates or control credentials as metadata.

## Security and Limits

- File limit: 16 MiB, checked before reading and again before JSON parsing.
- Event limit: 500; individual payload strings: 1,048,576 UTF-16 code units.
  IDs are limited to 128 code units; targets/warnings to 1,024.
- Finite, nonnegative, safe integers are required for counts, sizes and latency;
  timestamps must also fit the JavaScript Date range. Duplicate/empty event IDs,
  extra object properties, wrong shapes, and unsupported enums are rejected.
- Export uses an explicit field allowlist, not a dump of the store or backend
  configuration. It excludes auth state, certificate keys, the harvested-token
  index, and replay permissions. It does **not** redact payloads: credentials or
  personal data captured in traffic remain in the file. Exports are unencrypted.
- Imported strings are rendered as text, never HTML; targets are not fetched.
  A failed import preserves the previously loaded archive. Imported events are
  additionally marked non-replayable in the view model.
- Files and their metadata are untrusted assertions. There is no signature,
  provenance verification, chain of custody, encryption, or completeness proof.
- Streams remain logged chunks, not reassembled application messages. Separate
  pre/post-tamper pairs, FIN history, full stream summaries, attack history, and
  handshake/header archives are not included. Missing/evicted events cannot be
  recovered from an export. Archived binary/stream replay is not implemented.

## Verification

Windows, Python 3.12, Node 24, installed Chrome (headless Playwright):

- 58 frontend tests passed (36 new format/file tests and one subscription-gap
  regression, plus the 21 existing tests).
- 101 backend tests ran: 100 passed; one opt-in soak skipped. Two new transport
  regressions cover session-bound target identity and unknown target metadata.
- 11 launcher tests passed; TypeScript/Vite production build passed.
- Browser checks passed at 1440x900 and 390x844: unauthenticated offline import,
  zero backend requests during import, read-only controls, exact expanded text,
  binary labels, search, keyboard expansion, inert HTML payloads, invalid-import
  recovery, no page-level horizontal overflow, and screenshots reviewed.
- With **stubbed** control responses: live export/download included all retained
  rows despite filtering; export excluded the fixture auth token; importing the
  download and returning to live preserved live events and did not invoke replay.
  This browser check is not an independent real-application interoperability test.
- Initial browser harness attempts needed the installed Chrome channel (the
  matching Playwright browser was absent) and exact-label file-input selectors.
  Sandbox esbuild resolution and owned-process termination needed unsandboxed
  reruns; final build and launcher runs passed.

Repeat the tests from the repository root:

```powershell
npm --prefix client test
npm --prefix client run build
.venv\Scripts\python.exe -m unittest discover -s python/tests -v
npm run test:launchers
```

For the optional UI test, start Vite on a free loopback port, use an existing
Playwright installation, and run:

```powershell
$env:CAPTURE_TEST_URL = 'http://127.0.0.1:5181'
$env:CAPTURE_TEST_CHANNEL = 'chrome'
# Optional: file URL pointing to an existing Playwright index.mjs
# $env:PLAYWRIGHT_MODULE = 'file:///path/to/playwright/index.mjs'
node scripts/tests/capture-browser.mjs
```

Screenshots are generated in ignored `build/capture-browser/`. No Playwright
dependency was added to the project. No final desktop bundle was created.

Next release gate: independent, locally hosted WebTransport interoperability.
Extended resilience, full browser certificate-rotation reconnection, and packaged
desktop/clean-machine verification remain outstanding.
