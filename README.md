# LEGILIMENS
### *"Reading what others cannot see."*

A WebTransport traffic inspector and security testing tool. Part of the **Hallows** security toolkit.

---

## What is Legilimens?

Legilimens is a MITM proxy and inspector purpose-built for [WebTransport](https://developer.chrome.com/docs/capabilities/web-apis/webtransport) — the modern browser API for bidirectional communication over HTTP/3 (QUIC/UDP).

**Burp Suite cannot inspect WebTransport traffic.** Burp is a TCP-layer proxy; WebTransport runs over QUIC, which is UDP-based and uses its own TLS 1.3 stack outside the browser's normal certificate trust chain. Legilimens fills this gap: it acts as a transparent proxy between a browser client and a WebTransport server, intercepting every datagram and stream chunk in real time and displaying them in a terminal-aesthetic React dashboard.

Think of it as Wireshark + Burp Suite, but specifically for WebTransport.

---

## Why WebTransport Needs Its Own Inspector

Traditional HTTP/1.1 and HTTP/2 tools intercept TCP streams and swap out TLS certificates using a trusted root CA. WebTransport over HTTP/3 runs over **QUIC** (UDP), which:

- Uses a self-contained TLS 1.3 handshake at the QUIC layer — not the OS/browser TLS stack
- Allows `serverCertificateHashes` pinning: clients can accept a specific cert hash without a trusted CA, making standard CA-swap MITM impossible
- Carries multiplexed datagrams and streams simultaneously — a model that TCP proxies cannot natively understand

Legilimens handles all of this by running a real WebTransport server (port 4433) as the proxy entry point, then creating a corresponding WebTransport client connection to the target server (port 4434) and forwarding all traffic bidirectionally at the application layer.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         LEGILIMENS                                  │
│                                                                     │
│  Chrome Browser                                                     │
│  ┌──────────────┐    WebTransport     ┌─────────────────────────┐  │
│  │   React UI   │ ──────────────────► │  Proxy (port 4433)      │  │
│  │  (port 5173) │                     │  @fails-components/wt   │  │
│  │              │ ◄── WS events ────  │  + Express (port 4436)  │  │
│  │              │   ws://4435         │  + WS broadcast (4435)  │  │
│  └──────────────┘                     └──────────┬──────────────┘  │
│                                                  │                  │
│                                         WebTransport               │
│                                         (forwards all traffic)     │
│                                                  │                  │
│                                       ┌──────────▼──────────────┐  │
│                                       │  Vulnerable Server      │  │
│                                       │  (port 4434)            │  │
│                                       │  4 intentional vulns    │  │
│                                       └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**Ports:**
| Port | Protocol | Purpose |
|------|----------|---------|
| 4433 | WebTransport (QUIC) | Proxy entry point — browser connects here |
| 4434 | WebTransport (QUIC) | Vulnerable target server |
| 4435 | WebSocket | Live event stream → React UI |
| 4436 | HTTP (Express) | Cert hash endpoint + health check |
| 5173 | HTTP (Vite) | React dev server |

---

## Setup

### Prerequisites
- **Node.js 18+** (QUIC support)
- **Chrome** or **Edge** (WebTransport is not supported in Firefox/Safari)
- `npm`

### 1. Install dependencies

```bash
# Install root dependencies (selfsigned for cert generation)
npm install

# Install server and client dependencies
npm run install-all
```

### 2. Generate TLS certificate

WebTransport requires HTTPS/TLS. Generate a self-signed certificate:

```bash
npm run gen-cert
```

This creates `server/certs/cert.pem`, `server/certs/key.pem`, and `server/certs/fingerprint.json`.

> **Important:** The cert is valid for **14 days** (WebTransport's `serverCertificateHashes` limit). Re-run `npm run gen-cert` before it expires.

The script prints the exact Chrome launch command with your cert's SPKI hash.

### 3. Launch Chrome with WebTransport flags

Copy the command printed by `gen-cert`. It looks like:

```bash
# macOS/Linux
google-chrome \
  --origin-to-force-quic-on=localhost:4433,localhost:4434 \
  --ignore-certificate-errors-spki-list=<YOUR_HASH_HERE> \
  http://localhost:5173

# Windows (PowerShell)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --origin-to-force-quic-on=localhost:4433,localhost:4434 `
  --ignore-certificate-errors-spki-list=<YOUR_HASH_HERE> `
  http://localhost:5173
```

### 4. Start the servers

**Terminal 1 — Proxy + Vulnerable server:**
```bash
npm start
# or individually:
npm run start-proxy   # proxy on 4433 + API on 4436 + WS on 4435
npm run start-server  # vulnerable server on 4434
```

**Terminal 2 — React UI:**
```bash
npm run start-ui
```

### 5. Open the UI

Navigate to `http://localhost:5173` in your Chrome window (the one launched with flags in step 3).

Click **▶ START** to establish a WebTransport connection through the proxy.

---

## Using Legilimens Against Your Own App

To inspect your own WebTransport server instead of the built-in vulnerable one:

1. Change the proxy target in `server/proxy.js`:
   ```js
   // Change this line:
   serverSession = new NodeWebTransport('https://localhost:4434/', ...)
   // To point at your server:
   serverSession = new NodeWebTransport('https://your-server:port/', ...)
   ```

2. Update `serverCertificateHashes` or use a properly trusted cert for your server.

3. Update your client code to connect to `https://localhost:4433/` (the proxy) instead of your server directly.

All traffic will now flow through Legilimens and appear in the dashboard.

---

## The 4 Vulnerabilities in the Test Server

The vulnerable server (`server/vulnerable-server.js`) contains four **intentional** security flaws used to demonstrate the attack simulator:

### 1. No Authentication
Server accepts every WebTransport connection with zero credential checking. No token, no API key, no session validation. Anyone who can reach port 4434 is immediately accepted.

**Demonstrated by:** Unauthorized Stream attack

### 2. Sensitive Data in Datagrams
Every 2 seconds, the server broadcasts a heartbeat datagram containing a plaintext session token and admin user ID:
```json
{"type":"heartbeat","session_token":"SECRET_TOKEN_abc123","user_id":"admin","timestamp":...}
```
These appear as `SUSPICIOUS` events in the traffic log immediately.

**Demonstrated by:** Token Harvester attack (collects them passively over 5 seconds)

### 3. No Payload Validation
The server echoes back any datagram without sanitization, size checking, or JSON validation. It accepts malformed JSON, XSS strings, SQL injection, and prototype pollution payloads without complaint.

**Demonstrated by:** Payload Injection attack

### 4. No Rate Limiting
The server processes every incoming datagram unconditionally. There is no throttle, no circuit breaker, and no connection limit. A flood of 1000 messages is accepted in full.

**Demonstrated by:** Flood Attack

---

## Dashboard Components

| Panel | Description |
|-------|-------------|
| Traffic Log | Live scrolling list of all intercepted events. Click any row to expand the full payload. Rows auto-scroll; scroll up to pause. |
| Latency Monitor | Recharts line graph showing datagram and stream latency over the last 120 seconds. |
| Attack Simulator | Four attack buttons. Disabled when proxy is inactive. Shows real-time status and result summaries. |
| Stream Inspector | All bidirectional streams with chunk counts. Click to expand and view each chunk. |
| Status Bar | Fixed bottom bar with live counters: events, datagrams, streams, avg latency, tampered, suspicious. |

---

## Tamper Mode

The proxy supports payload tampering via the HTTP API:

```bash
# Enable tamper mode (replaces any "token" value in payloads)
curl -X POST http://localhost:4436/tamper -H 'Content-Type: application/json' -d '{"enabled":true}'

# Disable
curl -X POST http://localhost:4436/tamper -d '{"enabled":false}'
```

Tampered events appear with an orange `TAMPERED` badge in the traffic log.

---

## Roadmap

- **v2 — Electron app:** Package Legilimens as a standalone desktop app. No Chrome flags needed — Electron manages its own Chromium instance with the cert pre-trusted.
- **True MITM mode:** Intercept connections to arbitrary remote WebTransport servers with automated certificate management (similar to Burp's CA approach for QUIC).
- **Request editor:** Pause, edit, and replay intercepted datagrams/stream chunks before forwarding.
- **Export:** Save capture sessions as NDJSON or HAR-like format for offline analysis.
- **Scripting engine:** JavaScript hooks that fire on each intercepted event (like Burp extensions).
- **Connection graph:** Visualize session topology across multiple concurrent connections.

---

## Part of Hallows

Legilimens is the first tool in the **Hallows** security toolkit — a suite of purpose-built tools for protocols and attack surfaces that mainstream security tools cannot reach.

*"For when the Deathly Hallows are not enough."*

---

## License

MIT
