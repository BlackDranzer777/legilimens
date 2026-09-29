<div align="center">

![Legilimens](assets/banner.png)

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![aioquic](https://img.shields.io/badge/aioquic-QUIC%2FWebTransport-1a1a1a)](https://github.com/aiortc/aioquic)
[![FastAPI](https://img.shields.io/badge/FastAPI-control%20API-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-C8F400?labelColor=1a1a1a)](#license)

**A man-in-the-middle inspector for WebTransport — the traffic Burp Suite and Wireshark can't see.**

Part of the **Hallows** security toolkit.

</div>

---

## What is Legilimens?

Legilimens is a MITM proxy and live inspector purpose-built for [WebTransport](https://developer.chrome.com/docs/capabilities/web-apis/webtransport) — the modern browser API for bidirectional, low-latency communication over HTTP/3 (QUIC / UDP).

A client connects to Legilimens **thinking it is the real server**. Legilimens forwards everything upstream to the actual target (the man-in-the-middle position), logs every datagram and stream chunk, optionally rewrites traffic in flight, and streams a live copy of it all into a React dashboard.

> **Think of it as Burp Suite + Wireshark, but for a protocol existing tools can't reach.**

## Why WebTransport needs its own inspector

Burp and Wireshark were built for TCP. WebTransport runs over **QUIC**, which:

- Is **UDP-based** with its own self-contained **TLS 1.3** handshake at the QUIC layer — outside the OS/browser certificate trust chain a normal proxy intercepts.
- Supports **`serverCertificateHashes`** pinning: a client can trust one exact certificate hash with no CA involved, which makes the classic "swap in our own CA cert" MITM trick impossible.
- Multiplexes **datagrams and bidirectional streams** at once — a model TCP proxies don't natively understand.

Legilimens handles all of this by running a real WebTransport server as the proxy entry point, dialing the upstream target as a real WebTransport client, and relaying traffic bidirectionally at the application layer — all while broadcasting a live feed to the UI.

---

## Architecture

![Architecture](assets/architecture.png)

The whole backend runs as **one Python asyncio process** (`python/backend.py`) hosting four services:

| Port | Service | Protocol | File |
|------|---------|----------|------|
| `4433` | MITM proxy — browser/client connects here | WebTransport (QUIC/UDP) | `python/proxy.py` |
| `4434` | Deliberately vulnerable target server | WebTransport (QUIC/UDP) | `python/vulnerable_server.py` |
| `4435` | Live event broadcaster → React UI | WebSocket | `python/logger.py` |
| `4436` | Control API (capture / tamper / target / attack) | HTTP (FastAPI) | `python/api.py` |
| `5180` | React inspector UI | HTTP (Vite dev server) | `client/` |

> The backend was **migrated from Node.js to Python** (`aioquic`). The original Node implementation is kept under `server/` for reference but is no longer used. The React UI is unchanged — same API contract.

---

## Features

- **Capture files** - export all retained traffic to a versioned JSON file and reopen it in a separate read-only view without backend access. Includes session/target metadata and recorded loss indicators; raw payloads may contain secrets. See [capture format, limits, and verification](doc/CAPTURE_FILES.md).
- **Live capture** — every datagram and bidirectional stream chunk, in real time, in a scrolling log you can filter (`SUS` / `TAMPERED` / `NORMAL` / `CONN`) and full-text search.
- **Capture controls** — `START` (capture), `PAUSE` (hold the wire, connection stays alive), `DISCONNECT` (hard cut).
- **Conditional tamper** — rewrite a JSON field in passing traffic, optionally only when another field matches (e.g. *change `score` to `99999`, but only where `playerName = Seeker`*).
- **Runtime target switching** — point the proxy at a different upstream WebTransport server without restarting.
- **Manual intercept** — hold a datagram/stream mid-flight, then **edit, forward, or drop** it (Burp-style Intercept), scoped by direction/type with a timeout.
- **Repeater** — resend or **inject** an (edited) message into the live session — to the server or back to the client.
- **Suspicious-data flagging** — payloads containing `token` / `auth` / `bearer` / passwords are auto-flagged.
- **Attack simulator** — connection floods, slow-loris cycles, and more, launched from the UI with live progress (see [Attacks](#the-vulnerable-target--attacks)).
- **Cert helper** — the cert hash for `serverCertificateHashes` is served at `/cert-hash` and shown in the UI with a copy button.

---

## Quick start

### Prerequisites
- **Python 3.12** for the validated Windows dependency locks; other Python/platform combinations are not confirmed.
- **Node.js 24 LTS** recommended (`.nvmrc`); Electron development requires Node 22.12 or newer. The system Node installation is not upgraded automatically.
- **Chrome** or **Edge** — WebTransport is not supported in Firefox/Safari

### 1. Install dependencies

```bash
# Backend — an isolated Python environment (do NOT install globally)
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
python -m pip install --require-hashes -r python/requirements.txt

# Frontend — React/Vite dependencies (installs into client/)
npm run install-ui              # = npm --prefix client ci

# Optional: pinned audit and packaging tools, including the runtime dependencies
python -m pip install --require-hashes -r python/requirements-dev.txt

# Optional: desktop development dependencies (use Node 24)
npm --prefix desktop ci
```

### 2. Run it (one command)

```bash
npm run dev
```

This validates or renews the TLS certificate, starts the **backend** (proxy `:4433`, target `:4434`, WS log `:4435`, API `:4436`), and starts the **UI** on **http://localhost:5180** — all in one terminal. `Ctrl+C` stops everything.

> The cert is a self-signed **ECDSA P-256** cert generated with `cryptography`, with a roughly 13-day validity window. Valid certificates are preserved; missing, invalid, or near-expiry certificates are renewed. Runtime renewal requests a controlled backend restart.

Dependency audit results, regeneration commands, and verification limits are recorded in [Dependency security](doc/DEPENDENCY_SECURITY.md). With the project virtual environment active, run `npm run audit:js` and `npm run audit:python` to recheck the lockfiles. A clean advisory scan is not a product-security certification.

### 3. Open the UI

Open the **private dashboard link printed by `npm run dev`** in Chrome or Edge. The link supplies a per-launch access token; the UI removes it from the address bar and keeps it in tab-scoped session storage. A plain visit to `http://127.0.0.1:5180` instead displays the token entry screen. Once connected, click **START** to enable capture.

Keep that link and your terminal output private. Restarting the launcher generates a new token; use the new link when access expires. The desktop shell supplies its token automatically.

> **No launch flags needed.** Legilimens serves its cert hash at `/cert-hash`, and the UI connects with the WebTransport `serverCertificateHashes` API — so a normal browser trusts the self-signed cert without any `--origin-to-force-quic-on` / `--ignore-certificate-errors-spki-list` flags. (`npm run gen-cert` still prints those flags if you want the legacy path.)

> **No browser?** `python/test_client.py` is a CLI WebTransport client for testing:
> ```bash
> .venv\Scripts\python.exe python/test_client.py --count 5
> ```

<details>
<summary><b>Prefer to run the pieces separately?</b></summary>

```bash
npm run gen-cert     # regenerate the cert only
npm start            # backend only  (= python python/backend.py)
npm run start-ui     # UI only, on http://localhost:5180
```
</details>

---

## Frontend architecture

The inspector UI is a **React 18 + TypeScript** app built on one rule: **data flows one way.** There are two paths, and together they form a loop.

![Frontend architecture](assets/frontend.png)

- **Data in (read path).** An auto-reconnecting WebSocket pushes live events into a single **Zustand store** — the one source of truth. It routes each event by `type` (traffic / attack / intercept), updates derived counters, and flags risky payloads. View components subscribe and re-render; **no component keeps its own copy of the data**.
- **Controls out (write path).** Clicking a button — start, pause, set a tamper rule, change the target, run an attack — sends a **REST** request to the control API. The backend acts, and the results come back as new events over the WebSocket. That closes the loop.
- **Why it's built this way.** Components split cleanly into **controls** (which write) and **views** (which read), so the app is easy to reason about. Non-serializable objects (the WebSocket, the browser `WebTransport` session) live outside the store as module variables, keeping the store pure render-state. Because the log can update tens of times a second, selector subscriptions keep re-renders scoped to only the components that use each slice.

---

## Control API (`:4436`)

Everything the UI does goes through this FastAPI surface. **All API routes, including `/health` and `/cert-hash`, require `Authorization: Bearer <control-token>`.** Static UI assets are public but contain no token or captured traffic.

| Method & path | Purpose |
|---|---|
| `POST /intercept` `{action: "start"\|"pause"\|"disconnect"}` | Capture control |
| `GET/POST /tamper` `{enabled, field, value, matchField, matchValue}` | Rewrite a JSON field in passing traffic (conditional) |
| `GET/POST /target` `{host, port, certHash}` | Switch the upstream target |
| `GET /cert-hash` | Base64 `SHA-256(DER)` cert hash for `serverCertificateHashes` |
| `GET /health` | Liveness check |
| `POST /attack` `{type, params}` → `{attackId}` | Launch an attack |
| `GET /attack/{id}/status` · `POST /attack/{id}/stop` | Track / stop a running attack |

Example — enable a conditional tamper rule:

```bash
curl -X POST http://127.0.0.1:4436/tamper -H "Authorization: Bearer $LEGILIMENS_CONTROL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled":true,"field":"score","value":"99999","matchField":"playerName","matchValue":"Seeker"}'
```

Tampered messages appear with an orange **`TAMPERED`** badge in the traffic log.

For the shell example, set `LEGILIMENS_CONTROL_TOKEN` in your shell to the current backend token first. A standalone `python python/backend.py` prints its generated token. For automation, you may supply a cryptographically random 32-128-character URL-safe token through that environment variable before starting the backend; externally supplied tokens do not rotate automatically. Never commit a token or put it in a query string.

### Local security boundary

- The Python API, capture WebSocket, proxy, and bundled practice target bind to `127.0.0.1` only. Remote control is unsupported; do not expose or forward these ports to other machines.
- Browser control requests allow only exact `http://127.0.0.1` or `http://localhost` origins on the API port and development UI port `5180`. Other origins, including `null`, are rejected even with a valid token. Non-browser clients may omit Origin but must authenticate. Host validation rejects foreign hostnames used in DNS rebinding.
- The capture WebSocket requires a first message `{"type":"authenticate","token":"<control-token>"}` within five seconds. It sends `{"type":"authenticated"}` before subscribing the client. Failed authentication closes with code `1008`; no captures are sent before authentication.
- Vite is restricted to loopback and port `5180`; it fails rather than silently switching to an untrusted origin. The built UI is served from the API port. Custom backend port flags work with the built UI; the development UI expects API port `4436`.
- This boundary protects against unauthenticated control and untrusted browser origins. It is not protection against malware running as your OS user, compromised trusted UI code, or a leaked token. Local HTTP/WS is not encrypted. Standalone practice apps and the legacy `server/` implementation are outside this change.

Security regression checks:

```bash
.venv/Scripts/python.exe -m unittest discover -s python/tests -v
npm --prefix client test
npm --prefix client run build
```

---

## Vulnerable practice apps

Beyond the bundled target, **`vuln_apps/`** is a suite of tiny, **deliberately-insecure** WebTransport apps — one isolated flaw each — for practising against Legilimens. Every app is a headless `server.py` + a CLI `client.py`, with a `README.md` and a Markdown + Word report explaining the bug, the exploit, and the fix.

| App | Vulnerability | Port | Legilimens shows |
|---|---|---|---|
| `01_no_auth` | Missing authentication (CWE-306) | `4451` | **watches** — session granted with no credential |
| `02_trust_client` | Trusting client input (CWE-602) | `4452` | **tampers** — forge a value in flight |
| `03_secret_leak` | Sensitive data exposure (CWE-200) | `4453` | **auto-flags** — secrets in the payload |
| `04_stored_xss` | No input validation / stored XSS (CWE-79) | `4454` | **injects** — plant a payload via the Repeater |
| `05_no_rate_limit` | No rate limiting / brute-force (CWE-307) | `4455` | **floods** — watch a brute-force live |

```bash
npm run vulns        # start all of them together
npm run vuln-3       # start just one (here: 03_secret_leak on :4453)
```

Then point Legilimens at that app's port (**Upstream Target → `127.0.0.1:445X` → Apply**) and run its client through the proxy:

```bash
.venv\Scripts\python.exe vuln_apps\03_secret_leak\client.py --port 4433
```

> These are **local practice targets only** — they use fake demo data and intentionally omit defences. Each app's `README.md` walks through exploiting and fixing it.

---

## The vulnerable target & attacks

`python/vulnerable_server.py` is a deliberately insecure WebTransport server used as a safe practice target. It has **no authentication**, **leaks a session token** in periodic heartbeat datagrams, **echoes any payload** without validation, and **applies no rate limiting**.

The attack simulator (`python/attacks/`, orchestrated by `python/attack_runner.py`) supports three attacks, launched from the UI with live progress:

| Attack | What it does | Status |
|---|---|---|
| `flooding` | Many parallel QUIC handshakes | ✅ Real |
| `loris` | Slow handshake/drop cycles (slow-loris style) | ✅ Real |
| `encapsulation` | Raw QUIC packets via Scapy | ⚠️ Needs Administrator + Npcap; not available in the desktop app |

`fuzz.py` and `out_of_joint.py` remain in the source tree but are **not registered**: the API rejects them. They sent hand-built QUIC Initial packets that a compliant server silently drops, so they never produced meaningful results.

---

## Project structure

```
python/
  backend.py            entry point — runs all four services on one asyncio loop
  proxy.py              MITM proxy :4433  (capture, conditional tamper, intercept, repeater)
  vulnerable_server.py  deliberately insecure target :4434
  logger.py             WebSocket event broadcaster :4435
  api.py                FastAPI control API :4436
  certs.py              ECDSA P-256 cert generation -> python/certs/ (gitignored)
  udp_fix.py            Windows SIO_UDP_CONNRESET fix (keeps the UDP listener alive on reload)
  attack_runner.py      attack lifecycle + live progress over the WS
  attacks/              flooding · loris · encapsulation  (fuzz · out_of_joint: unregistered)
  test_client.py        CLI WebTransport client for browser-free testing

client/src/
  store/useStore.ts     Zustand store (state, actions, WS event routing)
  components/            Header · TrafficLog · StreamInspector · Repeater · InterceptPanel
                        StatusBar · TargetConfig · TamperConfig · AttackSimulator · ServerInfoBar

scripts/
  start-all.js          `npm run dev`   — cert + backend + UI, one command
  start-vulns.js        `npm run vulns` / `vuln-N` — the vuln_apps launchers

vuln_apps/              deliberately-insecure practice apps (server + client + report each)
assets/                 README images (committed) + generate_assets.py to rebuild them
server/                 original Node.js backend — kept for reference, not used
```

---

## Status & honest caveats

Capture/history buffers now have count and byte budgets; old entries can be evicted,
and oversized capture events can be omitted with a warning. Slow capture subscribers
are disconnected rather than queued indefinitely. Manual intercept capacity overflow
drops new matching messages, with a warning; it does not bypass interception. Attack
concurrency/history is capped, and a stop cleanup timeout reports 504 without claiming
completion. See `doc/RELEASE_READINESS.md` for exact limits and remaining resource gaps.
The completed resource-hardening scope, WebSocket envelope/recovery contract, and
measured local workload are documented in [Resource limits](doc/RESOURCE_LIMITS.md).
These limits do not establish sustained-load safety or bound total process memory.

Manual intercept settings are validated as one update: rejected requests do not
partially change scope or release held traffic. Partial tamper updates preserve
omitted conditions; send empty matching fields explicitly to clear them.
`POST /attack/{attackId}/stop` waits for cancellation cleanup and returns the current
status record. A cancelled run reports `stopped`; an already completed or failed run
retains its original outcome. Stop failures are displayed rather than assumed successful.

### Application compatibility checks

The proxy preserves unmodified wire bytes, including binary datagrams and stream
chunks containing incomplete UTF-8 sequences. Non-UTF-8 payloads appear as Base64
previews in the traffic log and as editable Base64 in manual interception. Invalid
Base64 edits are rejected. The Repeater remains text/datagram-only; binary log
previews cannot be sent to it.

Capture events retain the full logged payload separately from the short row preview.
The Repeater requires an explicit live session and preserves captured text, including
whitespace. It never falls back to another client when a session closes. Successful
sends indicate queueing, not confirmed delivery. Authenticated `GET /sessions`
returns `{ "items": [{ "id": "session-uuid", "target": "host:port" }] }`;
`POST /replay` requires `sessionId`, `direction` (`incoming` or `outgoing`),
`messageType` (`datagram`), and `payload`. Missing identity returns 422, blank identity
400, and unavailable sessions 409. Binary/stream replay, separate pre/post-tamper
evidence pairs remain unimplemented. Capture retention now has explicit byte budgets.

Upstream connections now require either CA/hostname validation (blank target hash)
or an exact Base64 SHA-256 certificate fingerprint with a currently valid certificate.
Pinned connections verify the peer before sending the WebTransport CONNECT request.
The pin identifies the certificate directly; it does not additionally require a CA
chain or hostname match. The pin check uses aioquic's internal TLS peer-certificate
accessor and fails closed if it is unavailable; dependency upgrades need regression testing.

The original CONNECT path/query, Origin, and application headers are forwarded to
the configured target, with the authority rewritten for that target. The proxy waits
for upstream acceptance and relays rejection status codes instead of reporting an
early success. It does not relay rejection response bodies. Forwarded credentials
are only those the client actually supplies: browser cookie scope, login flows,
client certificates, and application-specific authentication are not emulated.

Run the local regression suite with:

```bash
.venv/Scripts/python.exe -m unittest discover -s python/tests -v
```

The suite uses temporary certificates and local aioquic client/server fixtures. It
checks binary traffic, uni/bidirectional streams, stream ordering and FIN, manual
interception, original headers, rejection statuses, and certificate validation.
Compatibility with an independent third-party application is **not confirmed**.
Clients still need to connect to the proxy and trust its certificate explicitly.

JSON tampering requires a complete JSON value in the current datagram or stream
chunk. Fragments pass through unchanged; application-specific stream framing and
message reassembly are not implemented.

Legilimens is a **strong working prototype**, not a shipped product. What's solid and what isn't:

**Works end to end:** datagram + bidirectional-stream MITM, conditional tamper, **manual intercept (edit / forward / drop)**, **repeater (resend / inject)**, suspicious-keyword flagging, capture start/pause/disconnect, **capture export and offline import**, session teardown under load, runtime target switching, and the `flooding` / `loris` attacks (real QUIC handshakes). Verified end-to-end against **real Chrome** (via `serverCertificateHashes`, no launch flags) and across the whole `vuln_apps/` suite. A **Windows desktop app** (Electron + frozen backend) is available as an unsigned preview; see [`desktop/README.md`](desktop/README.md).

**Known limitations:**
- **No automated scanner or fuzzer (Intruder-style)** — Legilimens is Burp's *manual core* (proxy, repeater, intercept). Automated brute-force / fuzzing is done today by external scripts (see `vuln_apps/05_no_rate_limit`), not yet in-tool.
- **`serverCertificateHashes` pinning caps real-world MITM** — you can inspect apps whose client you control (your own / test builds); a shipped client that pins its own cert can't be intercepted. This blocks *every* MITM tool equally (Burp, mitmproxy), not just Legilimens.
- **Tamper is per-chunk** — JSON split across multiple stream chunks won't match.
- **No automatic persistence** — live traffic is kept in memory (the UI retains up to 500 events) until you export it to a [capture file](doc/CAPTURE_FILES.md).
- **Empty datagrams time out in Chromium browsers** — Chrome and Edge fail to receive empty (and subsequent) datagrams, both directly and through the proxy; the cause is still under investigation.

---

## Roadmap

- **Intruder-style attack tool** — fire a payload list / range at an injection point from the UI (automated brute-force / fuzzing — the one big Burp feature still missing).
- **Working `fuzz` / `out_of_joint`** — re-enable them with proper QUIC Initial-packet construction (HKDF secrets, header protection, AEAD, 1200-byte padding).
- **List virtualization** — render only visible log rows for high-volume captures.
- **Signed Windows releases** — code signing to remove SmartScreen warnings.

**Done:** session export (versioned JSON capture files with an offline viewer) and Electron packaging (Windows desktop app bundling the backend and UI).

---

## Part of Hallows

Legilimens is the first tool in the **Hallows** security toolkit — purpose-built instruments for protocols and attack surfaces that mainstream tools cannot reach.

> *"For when the Deathly Hallows are not enough."*

---

## License

MIT — see [LICENSE](LICENSE). The desktop build bundles third-party software listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

<!-- <div align="center">
<sub>Diagrams are generated by <code>assets/generate_assets.py</code> — edit that script and re-run it to update them.</sub>
</div> -->
