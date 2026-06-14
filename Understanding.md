# Legilimens — Understanding the Project

> A self-contained explainer. Paste this into a fresh Claude chat to brainstorm, ask
> questions, or get help — it contains everything needed to understand the project without
> seeing the code. Written plainly; security concepts explained from the ground up.

---

## 1. The one-sentence version

**Legilimens is a wiretap for WebTransport traffic** — it sits invisibly between an app and
its server, showing you every message, letting you change or block them, and letting you
attack the server. Think "Burp Suite, but for WebTransport instead of normal HTTPS."

The name is the Harry Potter spell for reading minds — *"Reading what others cannot see."*
The bundled test target is **Vault713 / QuaffleArena**, a deliberately-insecure WebTransport
game built to practice against.

---

## 2. Why this needs to exist (the problem)

Normal web traffic (HTTPS) runs over **TCP**, and tools like Burp Suite or Wireshark can
inspect it. But a newer technology, **WebTransport**, runs over a different stack:

```
WebTransport  →  HTTP/3  →  QUIC  →  UDP
```

QUIC runs over **UDP** and encrypts almost everything, including parts of its own handshake.
The result: **existing tools can't inspect WebTransport.** Burp doesn't understand it.
Wireshark just sees encrypted noise. Chrome's DevTools barely show anything.

So if a developer builds a real-time app on WebTransport (multiplayer games, live trading,
video calls, collaborative editors, IoT control) and wants to find security bugs in it —
**there is no tool.** Legilimens fills that empty space.

---

## 3. Key concepts (plain English)

- **WebTransport** — a browser API for fast, two-way communication, like a faster, more
  flexible WebSocket. Used for low-latency real-time apps.
- **QUIC** — the transport underneath; runs on UDP, always encrypted, built by Google, now an
  internet standard. It's what makes WebTransport hard to inspect.
- **HTTP/3** — HTTP running over QUIC. WebTransport rides inside it.
- **Datagram** — a single small message that's *unreliable* (might get lost, no ordering).
  Used for things like player positions where losing one doesn't matter.
- **Stream** — a *reliable, ordered* channel of bytes (like a phone line). Used for chat,
  events, anything that must arrive intact and in order.
- **MITM (man-in-the-middle)** — sitting between two parties, reading/altering messages that
  one side thinks go straight to the other. This is what Legilimens *is*.
- **Self-signed certificate** — an ID card a server makes for itself (not vouched for by a
  trusted authority). Browsers normally distrust these.
- **`serverCertificateHashes`** — a WebTransport feature that lets a browser trust a specific
  self-signed cert *if you give it the cert's exact fingerprint (hash)*. This is how
  Legilimens gets the browser to trust its proxy without installing anything.

---

## 4. How it works (the core idea)

Normally an app talks **straight** to its server. Legilimens inserts itself in the middle by
having the app connect to **it** instead:

```
   [ App / game client ]
            │  (thinks it's talking to the real server)
            ▼
   [ LEGILIMENS PROXY ]  ── reads, can change or block, then forwards ──►  [ Real server ]
            │
            ▼
   [ Inspector UI ]  ◄── every message is copied here over a WebSocket
```

For this to work, two things must point at each other:
1. **The app's client** is configured to connect to Legilimens (not its real server).
2. **Legilimens is told where the real server is** (the "upstream target").

Then every message detours through Legilimens, which **copies it to your screen** and
**forwards it on** — optionally changing it first.

> Important honesty: Legilimens can only inspect an app **that is pointed at it.** It can't
> reach out and grab a random app's traffic. This is the same limitation Burp has, and it's
> what keeps it a legitimate testing tool (you test apps you own or are authorized to test).

---

## 5. The architecture (what runs where)

The backend is **Python** (recently migrated from an earlier Node.js version, which is kept
as dead reference in `server/`). It runs four services in one process:

| Port | Service | What it does |
|------|---------|--------------|
| 4433 | **MITM proxy** | Where the app connects; intercepts & forwards (QUIC/UDP) |
| 4434 | **Vulnerable server** | The bundled practice target (QUIC/UDP) |
| 4435 | **WebSocket log** | Pushes every captured message to the UI (TCP) |
| 4436 | **HTTP control API** | The UI's remote control: start/pause, tamper, set target, attacks (TCP) |
| 5173 | **React UI** | The inspector you look at (browser) |

**Data flow:** app → proxy(4433) → real server(4434). For every message it relays, the proxy
sends a copy over the WebSocket(4435) to the UI. The UI sends commands back over the HTTP
API(4436).

**Tech stack:** Python (aioquic for QUIC/WebTransport, FastAPI for the API, websockets,
cryptography for certs) + React 18 / TypeScript / Vite / Zustand / Recharts (neo-brutalist
dark UI).

---

## 6. The certificate trust trick (and why it was the hardest part)

To make a browser trust Legilimens' self-signed cert without installing anything, WebTransport
requires the cert to be very specific:
- **ECDSA P-256** key (Chromium *rejects* the more common RSA).
- A **leaf** cert (not a certificate authority).
- Valid **≤ 14 days** (Legilimens uses 13).

And there are **two different fingerprints** that are easy to confuse:
- **SPKI hash** — hash of just the public key. Only used for a Chrome launch flag.
- **Cert hash** — hash of the *entire* certificate. This is the one `serverCertificateHashes`
  actually needs. Using the wrong one makes every connection silently fail.

The proxy and the target **share the same cert**, so one fingerprint validates both hops.
Getting this exactly right (ECDSA + correct hash + leaf + short validity) was the single
biggest source of early bugs.

---

## 7. The controls (what you can do)

- **START / PAUSE / DISCONNECT** — START begins capturing+forwarding; PAUSE *holds the wire*
  (connection stays alive, traffic frozen, resume instantly); DISCONNECT hard-cuts the
  session (the app must reconnect itself — a proxy can't force a client to redial).
- **UPSTREAM TARGET** — where the proxy forwards to. Change this (plus point a different app
  at the proxy) to inspect a different app.
- **TAMPER RULE** — rewrite a named JSON field in passing traffic (e.g. `score → 99999`),
  optionally scoped to one object ("only where `playerName` = `Seeker`").
- **Traffic Log** — the live feed, with filters (suspicious / tampered / etc.) and search.
- **Attack Simulator** — 5 server-side attacks against the target (see §9).
- **Latency / Stream Inspector / Status bar** — graphs and counters.

---

## 8. The bundled vulnerable target (what you practice on)

Vault713 / QuaffleArena is a WebTransport multiplayer game built to be insecure on purpose,
demonstrating real classes of bug:
1. **No authentication** — anyone can connect; the server trusts client-supplied identity.
2. **Secret leakage** — it broadcasts `session_token` and `email` in plaintext in every
   message (Legilimens flags these as SUSPICIOUS).
3. **Trust-the-client** — it believes client-reported values (score/speed/position) with no
   validation — the classic real-time-app bug.
4. **No input validation** — chat is echoed/stored unsanitized (stored XSS).
5. **No rate limiting** — unlimited messages/streams (DoS).
6. **Replayable actions** — resending a captured "score" message counts again.

These mirror the bug classes a real WebTransport app would have — Legilimens is how you'd
*find* them (read the leaks, tamper the trusted values, flood, replay).

---

## 9. The attacks (and an honest note)

Five server-side attacks run from the UI:
- **flooding** — opens many parallel QUIC connections, each handshakes then drops. **Real.**
- **loris** — slow-loris-style handshake/drop cycles. **Real.**
- **fuzz** — sends malformed QUIC packets. **Currently inert** — they're unprotected packets a
  compliant server silently discards (the code admits this).
- **out_of_joint** — injects forbidden/out-of-order frames. **Also currently inert**, same
  reason.
- **encapsulation** — raw packet crafting; **needs Administrator/root**.

So the real count today is "2 working + 2 scaffolding + 1 privileged." The codebase is
honest about this in its comments.

---

## 10. What it can and can't do (scope)

**Can:** inspect, filter, tamper, replay-by-hand, and attack any WebTransport app you point at
it and are authorized to test; surface leaked secrets; prove trust-the-client and no-auth bugs.

**Can't:** secretly intercept an app that isn't routed through it; force a disconnected client
to reconnect; meaningfully fuzz yet (see §9); measure real latency on localhost; persist or
export a session (closing the tab loses everything).

---

## 11. Honest status

A **strong, working prototype in a genuinely empty niche.** The hard part — a correct
WebTransport MITM over QUIC, including bidirectional streams and tricky aioquic edge cases —
is done and verified (it even MITM'd a different-language server and pulled out its real
secret). What's missing is the **pentester workflow layer**: interactive hold-and-edit
interception, save/replay/export, a real rule library, and finishing the fake attacks. See
`Future.md` for that roadmap.

It's also slightly **ahead of its market** — real WebTransport apps are still rare, so there
aren't many real-world targets to point it at *yet*.

---

## 12. Good things to brainstorm (prompts for Claude)

- How would I design the **interactive intercept** loop (hold a message, edit it in the UI,
  forward/drop) without freezing the target? What about timeouts and ordering?
- What's the right way to **persist and export** a capture session for a pentest report?
- How do I make the **fuzz attack real** — what does proper QUIC Initial-packet crypto
  (HKDF secrets, header protection, AEAD, 1200B padding) involve?
- What **WebTransport-specific vulnerabilities** should a passive analyzer look for
  (origin validation, stream limits, auth-on-stream)?
- Is the **"point your app at the proxy"** model the right one, or is there a more
  transparent interception approach for QUIC?
- How would I package this as a **one-click app** (Electron) so non-developers can run it?
- Who is the **real user** today, given WebTransport adoption is early — and how would that
  change what I build next?
- How does this compare, feature-for-feature, to **Burp Suite**, and which of Burp's features
  matter most to replicate first?

---

*Companion docs in this repo: `context.md` (technical handoff / file map), `Future.md`
(prioritized roadmap).*
