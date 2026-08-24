# Vuln app #4 — No input validation (Stored XSS)

A guestbook server. Clients **post** a message; the server stores it **verbatim** and serves it
back to anyone who **lists** the guestbook. It never validates, limits, or escapes the text — so
a message like `<script>...</script>` is stored as-is and handed to every viewer. Any client UI
that renders the guestbook as HTML will **run that script**: stored cross-site scripting (XSS).

> **Analogy:** a public noticeboard where the staff pin whatever note you hand them, word for
> word — including a note rigged to shout in the face of everyone who later reads the board.
> The staff never check the notes.

Here Legilimens plays the **injector**: unlike app #2 (which *changed* a value), it **injects a
whole malicious message** into the traffic using the **Repeater**.

- `server.py` — the guestbook (:4454). The bug is marked `THE VULNERABILITY`; the fix
  (validate + `html.escape`) is commented right next to it.
- `client.py` — posts a message (a `<script>` payload by default) and lists the guestbook back.

## See the vulnerability (direct)

```
.venv\Scripts\python.exe vuln_apps\04_stored_xss\server.py          # terminal 1
.venv\Scripts\python.exe vuln_apps\04_stored_xss\client.py          # terminal 2
```

The client posts `<script>alert('xss')</script>` and then lists the guestbook — the script
comes back **un-escaped**, exactly as stored.

## Attack it with Legilimens (through the proxy)

Legilimens **injects** a payload with its **Repeater** — no real client ever typed it:

```
# point Legilimens at the guestbook and start capture
curl -X POST http://localhost:4436/target    -H "content-type: application/json" -d "{\"host\":\"127.0.0.1\",\"port\":4454}"
curl -X POST http://localhost:4436/intercept -H "content-type: application/json" -d "{\"action\":\"start\"}"

# connect a client so there's a live session, then inject a script post via the Repeater
.venv\Scripts\python.exe vuln_apps\04_stored_xss\client.py --port 4433
```

In the **UI**: with a session live, use the **Repeater → TO SERVER** box to send
`{"cmd":"post","text":"<script>LEGILIMENS_INJECTED</script>"}`. Then post `{"cmd":"list"}` — the
injected script is now in the guestbook, served to everyone.

> app #1 it *watched*, #2 it *tampered*, #3 it *auto-flagged*, **#4 it *injects*** — using the
> Repeater to slip in traffic the client never sent.

**Honest note:** the script only *executes* when a UI renders the guestbook as HTML (e.g. via
`innerHTML`). The server storing raw markup is the root cause — that's what this app shows.

## The lesson

Never store or serve untrusted input unvalidated. **Validate and escape on the way in** (length
limits, allow-lists, `html.escape`), and **escape on the way out** when rendering. Treat every
byte from a client as hostile — a secure transport carries the attacker's `<script>` just as
faithfully as a real message.
