# Legilimens — Project Context (handoff)

> Handoff doc for an LLM/dev continuing this work (e.g. on Windows). Captures the
> current state, what's verified, what's broken, and how to run/test. Last updated
> after wiring the 5 attacks to the API + UI.

## What this is

Legilimens is a **WebTransport MITM proxy + security testing tool**. A client connects
to the proxy instead of the real server; the proxy forwards everything to the upstream
target (MITM position), logging and optionally tampering with traffic, and broadcasting
events to a React UI. The backend was **migrated from Node.js to Python**; the React UI
in `client/` is preserved (same API contract).

## Architecture / ports

| Port | Service | File |
|------|---------|------|
| 4433 | WebTransport MITM proxy (QUIC/UDP) | `python/proxy.py` |
| 4434 | Deliberately vulnerable target server | `python/vulnerable_server.py` |
| 4435 | WebSocket broadcaster → React UI | `python/logger.py` |
| 4436 | FastAPI control API | `python/api.py` |
| 5173 | React UI (Vite dev server) | `client/` |

Entry point: `python/backend.py` runs all 4 Python services in one asyncio process,
prints `READY` once up, accepts `--port-proxy/-target/-ws/-api`, exits when stdin closes
(for future Electron embedding).

Old Node backend is still in `server/` (kept as reference/fallback; not used).

## How to run

```bash
# 1. Python backend (needs a venv — do NOT install globally)
python -m venv .venv
.venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r python/requirements.txt
npm run gen-cert              # = python python/certs.py (uses `cryptography` lib, no OpenSSL)
python python/backend.py      # wait for "READY"

# 2. React UI (separate terminal)
cd client && npm install && npm run dev   # http://localhost:5173

# 3. For the browser WebTransport attack channel, launch Chromium with the printed flags:
#    chromium --origin-to-force-quic-on=127.0.0.1:4433,127.0.0.1:4434 \
#             --ignore-certificate-errors-spki-list=<SPKI hash from gen-cert> http://localhost:5173
```

`python/test_client.py` is a CLI WebTransport client for testing without a browser:
`python python/test_client.py --port 4433 --count 5`

## HTTP API contract (`:4436`)

- `POST /intercept` `{action: "start"|"pause"|"disconnect"}` — capture control
- `GET/POST /tamper` `{enabled, field, value, matchField, matchValue}` — rewrite a JSON field in passing traffic
- `GET/POST /target` `{host, port, certHash}` — upstream target
- `GET /cert-hash` → `{hash}` (base64 SHA-256 of the DER cert — for `serverCertificateHashes`)
- `GET /health`
- `POST /attack` `{type, target, params}` → `{attackId, status, type}`
- `GET /attack/{id}/status`, `POST /attack/{id}/stop`

## WebSocket events (`:4435`)

Traffic: `{id, timestamp, direction, type:"datagram"|"stream"|"connection", payload, rawSize, size, latency, flag:"normal"|"suspicious"|"tampered", streamId?}`

Attack: `{id, timestamp, type:"attack", attackId, attackType, status:"running"|"complete"|"failed", progress:{current,total,message}, result?, error?}`

The UI store (`client/src/store/useStore.ts`) routes `type:"attack"` to `handleAttackEvent`, everything else to `addEvent`.

## Attacks (`python/attacks/`, wired via `python/attack_runner.py`)

Each exposes `async run(target_url, params, progress_callback) -> dict`. The runner runs
them as background tasks and broadcasts progress. Triggered from the UI's Attack Simulator.

| Attack | Params | Result | Layer |
|--------|--------|--------|-------|
| flooding | `{connections:100}` | `{handshakesCompleted, failed, duration}` | **Real** — parallel QUIC handshakes |
| loris | `{connections,cycleDelay,cycles}` | `{cyclesCompleted, connectionsPerCycle, totalConnections}` | **Real** — handshake/drop cycles |
| fuzz | `{packets, mutationStrategy}` | `{packetsSent, responsesObserved:0, mutationsUsed}` | ⚠️ see Known Issues |
| out_of_joint | `{probes:4}` | `{probesAttempted, probesResponded:0}` | ⚠️ see Known Issues |
| encapsulation | `{packets:100}` | `{packetsSent, requiresRoot:true}` | Scapy raw packets; **needs root/Admin** |

## Status: what's verified working

- Node→Python migration of all 4 services (exact API contract preserved).
- **Datagram MITM** + tamper (force `score=99999`) + suspicious-keyword flagging.
- **Bidirectional stream proxying** (after fixing two aioquic quirks — see below).
- **Session teardown** — `activeSessions` returns to 0 on disconnect; survives concurrent load.
- **Cross-implementation proof** — the Python proxy MITM'd the old *Node* server and
  captured its hardcoded `SECRET_TOKEN_abc123` (real wire data, not fabricated).
- **5 attacks wired** UI→API→runner→WS; verified: launch, live progress, stop, concurrent
  runs, encapsulation root-fail message. flooding/loris produce real handshakes.
- `ServerInfoBar.tsx` widget — live capture mode, session count, cert hash + copy.

## Known issues / honest caveats (NOT yet fixed)

1. **fuzz & out_of_joint are inert.** They operate at the correct layer (raw UDP to the
   QUIC port) but send **unprotected, hand-built QUIC Initial packets** (no header
   protection, no AEAD, not padded to 1200B). A compliant server silently discards them —
   verified: zero server reaction. They report `responsesObserved/probesResponded: 0`,
   which is honest but means they exercise almost nothing. To make them real: derive
   Initial secrets via HKDF, apply header protection + AEAD, pad to 1200B. (Left as-is
   intentionally — separate task.)
2. **Latency metric is meaningless** — `proxy.py` measures its own ~0ms processing time,
   not real RTT. Graph/StatusBar show ~0.
3. **Tamper is per-chunk** — JSON split across multiple stream chunks won't match.
4. **Unidirectional WebTransport streams not proxied** — bidi only.
5. **No persistence/export/replay; no interactive per-message intercept-and-edit.**
6. **Dead code:** the old browser-side attacks in `useStore.ts`
   (`floodAttack`/`payloadInjection`/`unauthorizedStream`) are no longer called by
   `AttackSimulator.tsx` (replaced by the server-side `/attack` flow). Harmless; can be removed.

## aioquic gotchas already handled in `proxy.py` (don't regress these)

- **Server mode shares one UDP transport**, so `connection_lost` does NOT fire per QUIC
  connection. Per-connection teardown is driven by the `ConnectionTerminated` QUIC event.
- **Incoming data on a locally-created WebTransport bidi stream is NOT surfaced as a
  `WebTransportStreamDataReceived` event** — only for peer-opened streams. The proxy
  captures replies on streams it created via the raw `StreamDataReceived` event instead.
- A startup race (client opens a stream before the proxy finishes dialing upstream) is
  gated by an `_upstream_ready` asyncio.Event.

## Windows notes (this is where it's being tested)

- **Not yet run on Windows.** Cross-platform by construction (pathlib; `cryptography`
  instead of shelling to OpenSSL; `os.geteuid` guarded in encapsulation).
- **Most likely failure point: backend boot** — `asyncio` on Windows uses the
  ProactorEventLoop; aioquic + uvicorn + websockets must coexist on it. If
  `python python/backend.py` errors, capture the full traceback first.
  - uvicorn handles Windows signal limitations internally (catches `NotImplementedError`).
  - `backend.py:watch_stdin` uses `loop.connect_read_pipe`, unsupported on Windows
    Proactor — guarded by an `S_ISFIFO` check so it returns early for console stdin. If a
    parent process pipes stdin (Electron, Phase 2), this needs Windows-specific handling.
- `npm run gen-cert` works on Windows (pure-Python `cryptography`, no OpenSSL).
- `encapsulation` attack needs **Administrator + Npcap**; the other 4 don't.
- Use `python` (not `python3`) — matches `package.json` scripts.

## File map

```
python/
  backend.py            entry point (all 4 services)
  certs.py              ECDSA P-256 cert gen → python/certs/ (gitignored)
  logger.py             WS broadcaster :4435
  api.py                FastAPI :4436 (incl. /attack endpoints)
  proxy.py              MITM proxy :4433
  vulnerable_server.py  target :4434
  attack_runner.py      attack lifecycle + WS progress
  test_client.py        CLI WebTransport test client
  attacks/{flooding,loris,fuzz,out_of_joint,encapsulation}.py
client/src/
  store/useStore.ts                 Zustand store (+ attack state/actions/WS routing)
  components/AttackSimulator.tsx     5 attack cards + progress + stop + recent log
  components/ServerInfoBar.tsx       live status + cert hash widget
  components/{Header,TrafficLog,LatencyGraph,StreamInspector,StatusBar,TargetConfig,TamperConfig}.tsx
```

## Quick verification commands

```bash
# backend up?
curl -s localhost:4436/intercept
# launch an attack, watch it complete:
curl -s -X POST localhost:4436/attack -H "Content-Type: application/json" \
  -d '{"type":"flooding","params":{"connections":20}}'
curl -s localhost:4436/attack/<attackId>/status
```
