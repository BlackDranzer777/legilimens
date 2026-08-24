# Vuln app #1 — No authentication

A tiny "vault" that serves a private account balance over WebTransport. Its **only** flaw:
it reads the credential off the `CONNECT` request and then never checks it, so **any peer
gets in**. Everything else is done correctly on purpose, so the only thing you see when you
inspect it is unauthenticated access.

- `server.py` — the vault (:4451). The bug is marked `THE VULNERABILITY`; the fix is the
  commented-out `401` block right next to it.
- `client.py` — asks for the balance, with or without a token.

## See the vulnerability (direct)

```
.venv\Scripts\python.exe vuln_apps\01_no_auth\server.py          # terminal 1
.venv\Scripts\python.exe vuln_apps\01_no_auth\client.py          # terminal 2, NO token
```

The client presents no credential and still gets:
`{"account": "ACC-4417", "owner": "H. Potter", "balance": 10450, "currency": "GAL"}`

With the token it *should* demand, it also works (as it should):
`... client.py --token tok-hermione-granger`. A **fixed** vault would answer `401` to the
first case and `200` only to the second.

## Watch it in Legilimens (through the MITM proxy)

With the main backend running (`python\backend.py`) and the vault running:

```
# point Legilimens' upstream at the vault, then press START in the UI (or):
curl -X POST http://localhost:4436/target    -H "content-type: application/json" -d "{\"host\":\"127.0.0.1\",\"port\":4451}"
curl -X POST http://localhost:4436/intercept -H "content-type: application/json" -d "{\"action\":\"start\"}"

# run the client THROUGH the proxy (:4433) instead of hitting the vault directly
.venv\Scripts\python.exe vuln_apps\01_no_auth\client.py --port 4433
```

In the traffic log you'll see the session get **accepted with no credential** and the
private balance flow back — Legilimens is the wiretap that makes the missing auth check
visible. (The balance logs as a `normal` datagram, not `suspicious`: the bug isn't that the
data is sensitive, it's *who the server handed it to*.)

## The lesson

WebTransport gives you TLS, not authorization. Authenticate the `CONNECT` request and reject
the session (`401`) when the token is missing or invalid — don't open a session first and
hope the client behaves.
