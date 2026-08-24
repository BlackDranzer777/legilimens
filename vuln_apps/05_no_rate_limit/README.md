# Vuln app #5 — No rate limiting (brute-force / DoS)

A vault **pin-pad**. A client sends a 4-digit PIN guess; the server says right or wrong. It
processes **every guess instantly** — no per-client limit, no lockout after repeated failures —
so a client can try all 10,000 combinations in seconds and **brute-force the secret**.

(App #1's vault had no lock at all. This one *has* a PIN lock — but nothing stops you guessing.)

> **Analogy:** a padlock you can try keys on as fast as you like, forever. Your phone locks
> after a few wrong PINs; this door lets you try 10,000 in a row without blinking.

Here Legilimens is the **watcher/attacker of a flood**: it captures the brute-force live, and
its **Attack Simulator** can pile on connection-level DoS.

- `server.py` — the pin-pad (:4455) with a random secret PIN. The bug is marked
  `THE VULNERABILITY`; the fix (throttle / lock out) is commented next to it.
- `client.py` — an attacker that walks 0000..9999 until the door opens.

## See the vulnerability (direct)

```
.venv\Scripts\python.exe vuln_apps\05_no_rate_limit\server.py          # terminal 1
.venv\Scripts\python.exe vuln_apps\05_no_rate_limit\client.py          # terminal 2
```

The attacker prints something like `CRACKED: PIN = 5861 in 5862 guesses, 8.6s (683 guesses/sec)`
— nothing ever slowed it down.

## Watch it with Legilimens (through the proxy)

```
# point Legilimens at the pin-pad and start capture
curl -X POST http://localhost:4436/target    -H "content-type: application/json" -d "{\"host\":\"127.0.0.1\",\"port\":4455}"
curl -X POST http://localhost:4436/intercept -H "content-type: application/json" -d "{\"action\":\"start\"}"

# run the brute-forcer through the proxy — the traffic log floods with guesses
.venv\Scripts\python.exe vuln_apps\05_no_rate_limit\client.py --port 4433
```

You'll watch **thousands of `{"cmd":"guess",...}` datagrams** stream through the log — a
brute-force in progress, made visible. Legilimens also has an **Attack Simulator**
(QUIC-flooding / QUIC-loris) for the *connection-level* flavour of the same "no limits" problem.

> app #1 watched · #2 tampered · #3 auto-flagged · #4 injected · **#5 floods** — the tool shows
> (and can pile onto) an attack that only works because nothing says "slow down".

## The lesson

Rate-limit anything an attacker can repeat: logins, PIN/OTP checks, password resets, API calls.
**Throttle per client and lock out after repeated failures.** A 4-digit PIN is fine *with* a
"5 tries then wait" rule — the flaw is never the short secret, it's the missing limit.
