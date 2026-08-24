# Vuln app #2 — Trust-the-client (no server-side validation)

A coin-wallet game server. The client reports how many coins it "collected" and the server
just **adds whatever number the client sends**. It treats client-controlled data as the truth
about money. Any client can lie, and a MITM can rewrite the value in flight.

> **Analogy:** a shop where you fill in your own receipt. You write "paid: $1,000,000" and the
> cashier files it without checking the till. Whatever you claim, they believe.

- `server.py` — the wallet (:4452). The bug is marked `THE VULNERABILITY`; the fix (let the
  **server** decide the reward) is commented right next to it.
- `client.py` — collects coins; honest by default, but `--amount` lets it lie.

## See the vulnerability (direct)

```
.venv\Scripts\python.exe vuln_apps\02_trust_client\server.py                 # terminal 1
.venv\Scripts\python.exe vuln_apps\02_trust_client\client.py                 # terminal 2, honest (+1 each)
.venv\Scripts\python.exe vuln_apps\02_trust_client\client.py --amount 1000000 --count 1
```

The honest client walks the balance `1 → 2 → 3`. The cheating client jumps it to `1000000`
in a single call — the server never questions it.

## Watch Legilimens attack it (through the proxy)

Here Legilimens does more than watch — it **rewrites the value in flight** (its Tamper feature):

```
# point Legilimens at the wallet, set a tamper rule (amount -> 999999), start capture
curl -X POST http://localhost:4436/target    -H "content-type: application/json" -d "{\"host\":\"127.0.0.1\",\"port\":4452}"
curl -X POST http://localhost:4436/tamper    -H "content-type: application/json" -d "{\"enabled\":true,\"field\":\"amount\",\"value\":\"999999\"}"
curl -X POST http://localhost:4436/intercept -H "content-type: application/json" -d "{\"action\":\"start\"}"

# run the HONEST client through the proxy (:4433) — it still only claims 1
.venv\Scripts\python.exe vuln_apps\02_trust_client\client.py --port 4433
```

The client claims `1`, but the server replies `"credited": 999999`. Legilimens rewrote the coin
count between the honest client and the server, and the server trusted it.

> This is the difference from app #1: there Legilimens was the **camera** (it watched). Here it
> is the **burglar** (it changes the message). The vulnerability is what lets the change stick.

## The lesson

Never trust client-supplied values for state the **server** owns — money, score, inventory,
permissions. The server must decide: compute the reward from events it verified, or at minimum
reject/clamp impossible values. A value from the client is a *request*, not a *fact*.
