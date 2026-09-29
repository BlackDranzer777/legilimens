# Resource Budgets and Verification

Verified 2026-09-24 against the local working tree. These are application limits,
not an OS-level process memory cap, throughput promise, or public-release certification.

## Budgets

| Surface | Default | At capacity |
| --- | --- | --- |
| Admitted WebTransport sessions, including draining work | 32 globally | Reject CONNECT with 503 |
| Session forwarding work | 128 tasks, 2 MiB raw queued data | Close session and emit a resource notice |
| Active stream mappings | 128 logical streams; 256 directional locks | Close session on active-budget exhaustion; release completed uni streams after forwarding FIN and bidi streams after forwarding both FINs |
| QUIC buffered stream receive/send and datagram data | 4 MiB per connection; 272 transport streams; datagram queue checked against 128 | Reject further sends or close connection |
| CONNECT headers | 16 KiB name/value bytes | Reject with 431 |
| Control request body | 1 MiB, actual bytes even without/with misleading Content-Length | 413, no route mutation |
| Control request body read | 5 seconds | 408 |
| Concurrent control requests | 32 middleware requests; Uvicorn concurrency 64, backlog 128, keep-alive 5 seconds | 429/503 |
| Replay text datagram | 1,024 UTF-8 bytes | 413 before proxy callback |
| WebSocket handlers/subscribers | 32 handlers, 16 authenticated subscribers | Close with 1013 |
| Subscriber send queue | 256 queued messages, 4 MiB ASCII-serialized bytes including in-flight send | Close with 1013; incomplete-capture warning |
| Subscriber send deadline | 5 seconds | Close with 1013 |
| Serialized capture event | 1 MiB before sequence-envelope overhead | Replace with explicit omission notice, never a replayable truncated payload |
| Cross-thread ingress | 256 items, 4 MiB | Omit with notice; coalesced and batched dispatch |
| Held interception | 256 messages, 4 MiB UTF-8 originals plus edited decisions; 256 KiB per payload | New matching traffic dropped, not bypassed; oversized edits return 413 |
| Browser traffic history | 500 events, 8 MiB serialized UTF-16 estimate | Old entries evicted; counter shown |
| Browser stream history | 64 streams; 128 chunks and 128 KiB estimate per stream | Old entries evicted; omission counters shown |
| Harvested token history | 100 tokens, 4,096 characters each | Older/oversized tokens not retained |
| Active simulator runs/history | 4 tasks, 100 records | 429 for new active work; evict finished history on admission |
| Run deadline / stop cleanup wait | 300 seconds / 5 seconds | Deadline failure / 504 while still tracking cleanup |
| Run result / error / progress text | 64 KiB serialized result / 1,024 characters / 512 characters | Explicit result omission / bounded diagnostic text |
| Snapshot recovery buffer | 128 events, 4 MiB UTF-16 estimate | Close and retry recovery; loss warning retained |

Simulator parameters are validated before task creation: flooding connections 1-128;
loris connections 1-128, cycles 1-10, delay 0-60 seconds, at most 1,024 total
connections; encapsulation packets 1-1,000. Counts must be integers, booleans and
nonfinite values are rejected, and unknown parameters are rejected. Targets must
be HTTPS URLs without embedded credentials/fragments. Inert fuzz/out_of_joint
modes are not supported through the runner/API.

## Capture Recovery

WebSocket authentication returns an epoch and sequence cursor. Capture messages
are envelopes with `epoch`, `sequence`, and `event`. Authenticated `GET /state`
returns an atomic event-loop snapshot of current capture mode, intercept settings
and queue, tamper-enabled state, active runs, and the latest 20 terminal runs.

On reconnect or a sequence gap the UI loads that snapshot and applies buffered
messages newer than its cursor. Old/duplicate sequence numbers are discarded,
even when their run has fallen out of visible history. Stale HTTP recovery
responses cannot overwrite a newer socket. Recovery failure or overflow retries
without claiming synchronization. Missed traffic itself is not reconstructed;
the capture-loss warning remains until the user clears the log.

## Executed Checks

- Regular Python discovery: 62 tests discovered, 61 passed, one opt-in soak skipped.
- Explicit soak invocation: the skipped soak ran separately and passed.
- Frontend: 21 tests passed, including gap recovery, snapshot/event races,
  delayed events after history eviction, stale responses and recovery overflow.
- TypeScript/Vite production build passed.
- Browser: restarting the local backend reconciled interception from enabled to
  disabled without a page reload; connected state returned and loss warning remained.
  The warning fit at a 390 x 844 viewport.

The final soak used four aioquic clients, a separate local echo server, the actual
proxy and a real authenticated capture WebSocket. It ran datagram round trips and
periodic stream echoes, checked byte equality, and checked registry cleanup.
It used Python tracemalloc and three garbage-collected heap samples:

| Measurement | Observed |
| --- | --- |
| Workload duration | 30.33 seconds |
| Datagram round trips | 220 |
| Capture events received | 496 |
| Traced Python heap samples | 4,182,044; 4,084,622; 5,205,804 bytes |
| Traced Python peak | 5,292,009 bytes |
| Maximum observed forwarding tasks per session | 1 |
| Maximum observed queued raw bytes per session | 261 |

These are measurements of this short, instrumented Windows run, not throughput
benchmarks or maximum-capacity findings. Debug/tracing overhead and concurrent
regression tests affected the environment. Deterministic saturation tests separately
checked slow senders, queue limits, ingress coalescing, admission, timeout cleanup,
edited-payload accounting, and transport-buffer checks. The soak did not saturate
the configured task or byte caps.

Run the normal suite:

```powershell
.venv/Scripts/python.exe -m unittest discover -s python/tests -q
npm.cmd --prefix client test
npm.cmd --prefix client run build
```

Run the opt-in local soak:

```powershell
$env:LEGILIMENS_SOAK = '1'
.venv/Scripts/python.exe -m unittest discover -s python/tests -p test_resource_soak.py -v
Remove-Item Env:LEGILIMENS_SOAK
```

## Limits of Evidence

Native TLS/QUIC allocations, OS socket buffers, browser heap and whole-process RSS
were not measured. Pre-handshake hostile traffic and multi-hour workloads remain
unverified. Deadlines are cooperative asyncio deadlines: they cannot preempt a
blocking native call such as Scapy send. Scapy/raw-packet workflows were not run.
QUIC byte checks depend on aioquic internal buffer attributes and fail closed if
those attributes are unavailable; dependency upgrades require these regressions.
Serialization may allocate a bounded-input temporary string before omission.
Loopback-only exposure remains the default; these checks are not permission to
expose the control or proxy service publicly or test third-party systems.
