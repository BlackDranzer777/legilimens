# Vuln app #3 — Sensitive Data Exposure (secret leakage)

A leaderboard server. When a client asks for the leaderboard, the server serializes its
**whole internal user record** for every player — including `email`, `session_token`, and
`role` — instead of just the public `{name, score}`. Anyone who connects reads everyone's secrets.

> **Analogy:** you ask the front desk for the guest list, and they hand you a printout with
> everyone's name **plus room number, credit card, and passport**. You only needed names.

This is the **passive-detection** showcase: unlike app #1 (Legilimens *watches*) and app #2
(Legilimens *attacks*), here Legilimens **auto-detects** the leak — you configure nothing.

- `server.py` — the leaderboard (:4453). The bug is marked `THE VULNERABILITY`; the fix
  (send only a public projection) is commented right next to it.
- `client.py` — asks for the leaderboard and prints what came back.

## See the vulnerability (direct)

```
.venv\Scripts\python.exe vuln_apps\03_secret_leak\server.py          # terminal 1
.venv\Scripts\python.exe vuln_apps\03_secret_leak\client.py          # terminal 2
```

The client asked for a leaderboard and received every player's `email`, `session_token`, and
`role` — data that should never have left the server.

## Watch Legilimens catch it (through the proxy)

Here you don't set a tamper rule or intercept anything — you just **watch**:

```
# point Legilimens at the leaderboard and start capture
curl -X POST http://localhost:4436/target    -H "content-type: application/json" -d "{\"host\":\"127.0.0.1\",\"port\":4453}"
curl -X POST http://localhost:4436/intercept -H "content-type: application/json" -d "{\"action\":\"start\"}"

# run the client through the proxy (:4433)
.venv\Scripts\python.exe vuln_apps\03_secret_leak\client.py --port 4433
```

The moment the leaderboard reply flows through, Legilimens **auto-flags it `SUSPICIOUS`**
(it spots keywords like `session_token`, `token`, `secret`) and the `SUSPICIOUS` counter climbs
on its own. No config — the tool surfaces the leak just by watching.

> app #1 it *watched*, app #2 it *attacked*, app #3 it *detects* — three different sides of
> the same wiretap.

## The lesson

Never serialize your internal model to the client. Send a **public projection** — only the
fields the client actually needs (`name`, `score`) — and keep tokens, PII, and roles
server-side. TLS encrypts the payload in transit; it does **not** stop you from putting
secrets in that payload in the first place.
