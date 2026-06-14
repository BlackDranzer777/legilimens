# Legilimens — Future Roadmap (from a pentester's point of view)

> What's missing to take Legilimens from a **strong prototype** to a **tool a security
> tester actually reaches for.** The proxy engine (the hard 70%) is done and works. What's
> missing is mostly the **workflow layer** — the "cockpit" on top of the engine — plus
> finishing a few features that are currently scaffolding.

Priorities: **P0** = without these it isn't really a testing tool; **P1** = real capability
gaps; **P2** = polish, scale, and reach.

---

## P0 — The core testing workflow (must-have)

### 1. Interactive intercept (hold → edit → forward / drop)
**The single most important gap.** Right now tamper is a fire-and-forget *rule*. Burp's
defining feature is the **manual intercept loop**: each message (or selected ones) is
*paused*, shown to the tester, who edits it freely and then **Forwards** or **Drops** it.
- **Why a pentester needs it:** real testing is exploratory — "what if I change *this one*
  field on *this one* message and see what breaks?" Rules can't express that.
- **Approach:** add an `intercept_queue` in `proxy.py`; when intercept-mode is on, hold the
  message and `await` a decision pushed from the UI over the API/WS. UI gets an "Intercept"
  panel with the editable payload + Forward/Drop/Edit buttons. Needs a per-message id and a
  timeout fallback (auto-forward) so a stuck queue can't freeze the target.
- **Touches:** `proxy.py`, `api.py` (new `/intercept/decision`), WS protocol, a new UI panel.

### 2. Repeater (craft & resend a message manually)
Take any captured message, edit it, and **fire it again** as many times as you want —
without replaying the whole session.
- **Why:** iterate on a single payload (fuzz a field by hand, test boundaries, confirm a bug).
- **Approach:** a UI "send to repeater" action on any log row → an editor that POSTs the
  crafted message to a new `/send` endpoint, which injects it on an existing or new session.
- **Touches:** `proxy.py` (inject path), `api.py` (`/send`), new UI tab.

### 3. Persistence, save/load, and export
Today: close the tab, **lose everything**. No pentester accepts that.
- **Why:** evidence. You must save a session, reopen it, and export captured traffic +
  findings into a report.
- **Approach:** write events to a SQLite file (or JSONL) as they stream; add `/sessions`
  endpoints to list/load; export to HAR-like JSON + CSV. UI: a session picker + "Export".
- **Touches:** new `python/store.py`, `api.py`, UI.

### 4. A real match-&-replace rule library
One tamper rule isn't enough. Pentesters keep a *set* of rules (scoped by direction, type,
host, regex) toggled independently.
- **Why:** e.g. "strip auth on outgoing", "force `isAdmin:true` only on `/admin`",
  "log-only on everything else" — simultaneously.
- **Approach:** generalize `tamper_rule` → `tamper_rules: list`, each with match
  (field/regex/direction/type) + action (replace/drop/flag). UI: a rule table (add/remove/
  reorder/enable).
- **Touches:** `proxy.py` (`tamper_payload` → rule engine), `api.py`, `TamperConfig.tsx`.

---

## P1 — Real capability gaps

### 5. Finish or cut the inert attacks
`fuzz` and `out_of_joint` send **unprotected QUIC Initial packets** that any compliant
server silently discards (their own docstrings admit it). They look like features but
exercise almost nothing.
- **Why:** a security tool that ships fake attacks loses trust.
- **Approach (to make them real):** derive Initial secrets via HKDF (RFC 9001), apply header
  protection + AEAD, pad to 1200B, then mutate the *encrypted* packet or the *plaintext
  frames before encryption*. Observe responses (version negotiation, retry, CONNECTION_CLOSE).
  **Alternative:** if that's out of scope, *remove* them or relabel as "experimental / no-op"
  so the tool stays honest.
- **Touches:** `attacks/fuzz.py`, `attacks/out_of_joint.py`.

### 6. Stream reassembly before tamper
Tamper is **per-chunk**. JSON split across stream chunks won't match the rule.
- **Why:** real apps send large/streamed messages; you'd silently miss them.
- **Approach:** buffer per-stream until a message boundary (length-prefix or delimiter), then
  tamper the whole message, then re-chunk. Needs a configurable framing or a "best-effort
  reassemble on FIN" mode.
- **Touches:** `proxy.py` stream handlers.

### 7. Real metrics (throughput now, RTT for remote)
The latency graph measures the proxy's own ~0ms processing time — meaningless on localhost.
- **Why:** floods/pauses/activity should be *visible*; latency only matters against remote
  targets.
- **Approach:** plot **messages/sec & bytes/sec** (always meaningful). Separately, measure
  true **RTT to the upstream** via a periodic probe when the target is remote.
- **Touches:** `useStore.ts` bucketing, `LatencyGraph.tsx`.

### 8. Target scoping, auth, and remote/CA targets
To test something beyond the bundled server you need: per-target auth (inject headers/tokens),
**CA-trusted** upstreams (not just pinned self-signed), and an explicit **scope** so you only
touch authorized hosts.
- **Why:** real engagements have credentials, real certs, and strict authorization boundaries.
- **Approach:** extend `/target` with auth + verify mode; add a scope allowlist enforced in
  `proxy.py` before forwarding.
- **Touches:** `proxy.py`, `api.py`, `TargetConfig.tsx`.

### 9. WebTransport-specific security checks
Move from "shows traffic" to "finds bugs" with passive heuristics for WebTransport's own
weak spots:
- **Origin validation** — does the server accept connections from any `Origin`? (CSRF-for-WT)
- **Auth-on-stream** — are privileged streams reachable without credentials?
- **Resource limits** — unbounded streams/datagrams (DoS).
- **Secret leakage** — already partly done (keyword flagging); expand to entropy/format checks.
- **Approach:** a passive analyzer that watches the event stream and raises **findings**.
- **Touches:** new `python/analyzer.py`, a "Findings" UI panel.

---

## P2 — Polish, scale, reach

### 10. Reporting
Turn captured evidence + findings into an exportable report (Markdown/PDF/HTML) with
request/response excerpts. This is what actually gets delivered to a client.

### 11. Inspector tools (encoders/decoders)
Inline base64 / URL / JWT / hex / JSON pretty-print + an entropy view on selected payloads —
the small utilities testers use constantly.

### 12. Search, history, and marking
Persistent full-text search across the whole session, "mark/star" interesting messages, and
a filter-by-host/type/flag history (beyond the current in-memory 500-event cap).

### 13. Packaging & one-click run
The Electron embedding that `backend.py` is already designed for (the stdin-watch hook):
ship a single app that boots the backend + UI, no venv/npm dance. Add a Windows-specific
stdin/shutdown mechanism (the current watcher is skipped on Windows).

### 14. Tests + CI
There are no automated tests. A real tool needs: unit tests for the tamper/rule engine, an
integration test that boots proxy+target and asserts a round-trip + a tamper, and CI on
Linux/Windows/macOS (the upstream lib already does this).

### 15. Unidirectional streams + full WT coverage
Only bidirectional streams are proxied today. Add unidirectional streams and any WT features
the target uses, so nothing slips past uninspected.

---

## The one-line summary
**You built the engine; this list is the cockpit.** The highest-leverage next step is **#1
(interactive intercept)** — it's the feature that turns a passive viewer into an actual
testing tool, and everything else (repeater, rules, findings) builds around it.
