"""
Legilimens independent-interoperability harness (target #1: quic-go/webtransport-go).

Starts an independent Go WebTransport target and the real Legilimens backend on
dedicated loopback ports, then runs identical WebTransport cases directly against
the target (baseline) and through the Legilimens proxy, comparing results. Also
exercises capture (real WS), conditional JSON tamper, manual intercept, and
text-datagram replay against a selected live session.

All services are loopback and owned by this process. Certificate verification is
enabled everywhere: the client trusts the exact loopback PEM of whatever it dials,
and the proxy pins the target's certificate hash (no CERT_NONE, no global trust
change, no disabled control auth). Nothing is committed; artifacts stay in scratch.

Usage:
  python interop/harness/run_interop.py --scratch <dir> [--report <path>]
Exit code 0 only if every supported case passed.
"""

import argparse
import base64
import asyncio
import json
import os
import secrets
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wt_client  # noqa: E402
from support import Proc, allocate_ports, port_free

ROOT = Path(__file__).resolve().parents[2]
TARGET_EXE = ROOT / "interop" / "target" / ("interop-target.exe" if os.name == "nt" else "interop-target")

TARGET_ADDR = ""
PORTS = {}
KINDS = {name: socket.SOCK_DGRAM for name in ("upstream", "proxy", "target")}
KINDS.update(ws=socket.SOCK_STREAM, api=socket.SOCK_STREAM)
TOKEN = secrets.token_urlsafe(32)


# ---------- small helpers ----------

def api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{PORTS['api']}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


# ---------- WebTransport case suite (direct or proxied) ----------

DATAGRAMS = {
    "text": b"plain-datagram",
    "unicode": "datagram éü \U0001f30d".encode(),
    "whitespace": b"  \t spaced \n ",
    "empty": b"",
    "binary": bytes([0, 1, 2, 255, 254, 127, 128]),
}


async def dg_roundtrip(c, payload, attempts=5, per_wait=1.5):
    """Send a datagram and wait for its exact echo; retry (datagrams are lossy)."""
    for _ in range(attempts):
        c.send_datagram(payload)
        end = time.time() + per_wait
        while time.time() < end:
            try:
                got = await c.recv_datagram(timeout=max(0.05, end - time.time()))
            except asyncio.TimeoutError:
                break
            if got == payload:
                return True
    return False


async def run_cases(host, port, cafile, authority):
    """Run the supported WebTransport cases against one endpoint. Returns a dict."""
    r = {}
    async with wt_client.open_session(host, port, cafile) as c:
        r["connect"] = await c.connect_session("/echo?room=7&x=1", authority, origin="https://example.test")
        info = await c.next_server_stream(6.0)
        meta = json.loads(info[5:].decode()) if info.startswith(b"INFO:") else {}
        r["path"] = meta.get("path")
        r["query"] = meta.get("query")
        r["origin"] = meta.get("origin")

        r["datagrams"] = {name: await dg_roundtrip(c, payload) for name, payload in DATAGRAMS.items()}
        # Oversized delivery is observational: a timeout alone does not establish its cause.
        r["datagram_large_4k"] = await dg_roundtrip(c, b"L" * 4096, attempts=2, per_wait=1.0)

        # Bidi: single chunk, and multi-chunk with FIN; compare bytes + ordering.
        r["bidi_small"] = (await c.bidi_echo(b"hello-bidi")) == b"hello-bidi"
        big = bytes((i % 256 for i in range(20000)))
        r["bidi_multichunk"] = (await c.bidi_echo(big, chunk_size=1024)) == big

        # Uni: client->server, server echoes on a new uni stream.
        c.open_uni(b"uni-abc")
        srv = await c.next_server_stream(6.0)
        r["uni"] = srv == b"ECHO:uni-abc"
    return r


async def two_client_isolation(host, port, cafile, authority):
    """Two simultaneous sessions must not see each other's stream echoes."""
    async with wt_client.open_session(host, port, cafile) as a, \
               wt_client.open_session(host, port, cafile) as b:
        await a.connect_session("/echo", authority)
        await b.connect_session("/echo", authority)
        await a.next_server_stream(6.0)  # each session's own INFO
        await b.next_server_stream(6.0)
        ea = await a.bidi_echo(b"AAA-only")
        eb = await b.bidi_echo(b"BBB-only")
        return ea == b"AAA-only" and eb == b"BBB-only"


async def reconnect(host, port, cafile, authority):
    async with wt_client.open_session(host, port, cafile) as c:
        await c.connect_session("/echo", authority)
        await c.next_server_stream(6.0)
        first = (await c.bidi_echo(b"first")) == b"first"
    async with wt_client.open_session(host, port, cafile) as c2:
        await c2.connect_session("/echo", authority)
        await c2.next_server_stream(6.0)
        second = (await c2.bidi_echo(b"second")) == b"second"
    return first and second


# ---------- independent observation and control-plane checks ----------

class Capture:
    async def __aenter__(self):
        import websockets
        self.events = []
        self.error = None
        self.ws = await websockets.connect(f"ws://127.0.0.1:{PORTS['ws']}/", max_size=2**20)
        try:
            await self.ws.send(json.dumps({"type": "authenticate", "token": TOKEN}))
            ack = json.loads(await asyncio.wait_for(self.ws.recv(), 5))
            if ack.get("type") != "authenticated":
                raise RuntimeError("Capture authentication failed")
            self.epoch, self.sequence = ack.get("epoch"), ack.get("sequence")
        except BaseException:
            await self.ws.close()
            raise
        self.task = asyncio.create_task(self._read())
        return self

    async def _read(self):
        try:
            async for raw in self.ws:
                envelope = json.loads(raw)
                if "event" not in envelope:
                    continue
                if envelope["event"].get("type") == "resource":
                    raise RuntimeError("Capture resource warning")
                seq = envelope.get("sequence")
                if envelope.get("epoch") != self.epoch or seq != self.sequence + 1:
                    raise RuntimeError("Capture sequence gap")
                self.sequence = seq
                if len(self.events) >= 2048:
                    raise RuntimeError("Capture observation capacity exceeded")
                self.events.append(envelope["event"])
        except Exception as exc:
            self.error = str(exc)

    async def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error:
                raise RuntimeError(self.error)
            for event in self.events:
                if predicate(event):
                    return event
            await asyncio.sleep(0.02)
        raise TimeoutError("Expected capture event was not observed")

    async def __aexit__(self, *args):
        await self.ws.close()
        await self.task
        if self.error and not args[0]:
            raise RuntimeError(self.error)


def receipts(target, query):
    return [base64.b64decode(r["data"] or "") for line in target.snapshot()
            if line.startswith("RECEIPT ")
            for r in [json.loads(line[len("RECEIPT "):])] if r["query"] == query]


async def wait_receipt(target, query, expected, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if expected in receipts(target, query):
            return True
        await asyncio.sleep(0.02)
    return False


async def received_exact(client, expected, timeout=1):
    # Do not discard mismatched datagrams: unexpected bytes are evidence of failure.
    try:
        return await client.recv_datagram(timeout) == expected
    except asyncio.TimeoutError:
        return False


async def received_nothing(client, timeout=0.3):
    try:
        await client.recv_datagram(timeout)
        return False
    except asyncio.TimeoutError:
        return True


def valid_capture(event, marker, session_id, target):
    need = {"id", "type", "direction", "payload", "payloadEncoding", "rawSize", "timestamp", "sessionId", "flag"}
    return (need.issubset(event) and event["type"] == "datagram"
            and event["direction"] == "incoming" and event["payload"] == marker
            and event["payloadEncoding"] == "utf8" and event["rawSize"] == len(marker.encode())
            and event["sessionId"] == session_id and event.get("target") == target
            and bool(session_id))


def replay_delivered_to_selected(selected, observed):
    return (selected in observed and observed[selected] is True
            and all(value is False for sid, value in observed.items() if sid != selected)
            and len(observed) == 2)


async def control_cases(cert, target):
    result = {}
    authority = f"127.0.0.1:{PORTS['proxy']}"
    def session():
        return wt_client.open_session("127.0.0.1", PORTS["proxy"], cert)

    # Map clients to backend UUIDs using distinct captured payloads, never list order.
    async with Capture() as cap, session() as a, session() as b:
        mapping = {}
        for label, client in (("a", a), ("b", b)):
            assert await client.connect_session(f"/echo?client={label}", authority)
            await client.next_server_stream()
            marker = f"identify-{label}-{secrets.token_hex(8)}"
            assert await dg_roundtrip(client, marker.encode())
            event = await cap.wait(lambda e: e.get("payload") == marker and e.get("direction") == "incoming")
            sid = event.get("sessionId")
            assert valid_capture(event, marker, sid, TARGET_ADDR), "Capture metadata mismatch"
            mapping[label] = sid
        assert mapping["a"] != mapping["b"], "Two clients share a captured session ID"
        _, listed = api("GET", "/sessions")
        assert all(any(item["id"] == sid and item["target"] == TARGET_ADDR
                       for item in listed["items"]) for sid in mapping.values())
        result["capture_exact_metadata"] = True
        replay_checks = []
        for label in ("a", "b"):
            for direction in ("incoming", "outgoing"):
                payload = f"replay-{label}-{direction}-{secrets.token_hex(8)}"
                status, response = api("POST", "/replay", {"sessionId": mapping[label],
                    "direction": direction, "messageType": "datagram", "payload": payload})
                assert status == 200 and response.get("ok") is True, "Replay rejected"
                selected, other = (a, b) if label == "a" else (b, a)
                delivered, isolated = await asyncio.gather(
                    received_exact(selected, payload.encode()), received_nothing(other, 1))
                other_label = "b" if label == "a" else "a"
                observation = {mapping[label]: delivered, mapping[other_label]: not isolated}
                replay_checks.append(replay_delivered_to_selected(mapping[label], observation))
                if direction == "incoming":
                    replay_checks.append(await wait_receipt(target, f"client={label}", payload.encode()))
                replay_checks.append(payload.encode() not in receipts(target, f"client={'b' if label == 'a' else 'a'}"))
        result["replay_to_selected_session"] = all(replay_checks)
        result["capture_event_count"] = len(cap.events)

    rule = {"enabled": True, "field": "score", "value": "99999",
            "matchField": "player", "matchValue": "Seeker"}
    api("POST", "/tamper", rule)
    try:
        async with session() as c:
            assert await c.connect_session("/echo?case=tamper", authority)
            await c.next_server_stream()
            original = {"score": 10, "player": "Seeker", "keep": {"text": "unchanged"}}
            c.send_datagram(json.dumps(original).encode())
            echo = await c.recv_datagram(3)
            expected = {**original, "score": 99999}
            result["tamper_match_rewritten"] = (
                json.loads(echo) == expected and await wait_receipt(target, "case=tamper", echo))
            nonmatch = b' { "score": 10, "player": "Other", "keep": "unchanged" } '
            c.send_datagram(nonmatch)
            result["tamper_nonmatch_preserved"] = (
                await received_exact(c, nonmatch, 3)
                and await wait_receipt(target, "case=tamper", nonmatch))
    finally:
        api("POST", "/tamper", {**rule, "enabled": False})

    async with session() as c:
        assert await c.connect_session("/echo?case=manual", authority)
        await c.next_server_stream()
        api("POST", "/intercept/manual", {"enabled": True, "directions": ["incoming"],
                                          "types": ["datagram"], "timeoutMs": 10000})
        try:
            for mode in ("forward", "edit", "drop"):
                payload = f"manual-{mode}-{secrets.token_hex(8)}"
                c.send_datagram(payload.encode())
                deadline = time.monotonic() + 4
                item = None
                while time.monotonic() < deadline:
                    _, queue = api("GET", "/intercept/queue")
                    item = next((i for i in queue["items"] if i["payload"] == payload), None)
                    if item:
                        break
                    await asyncio.sleep(0.02)
                assert item, "Manual intercept never queued"
                held = await received_nothing(c) and payload.encode() not in receipts(target, "case=manual")
                edited = payload + "-edited"
                body = {"action": "drop" if mode == "drop" else "forward"}
                if mode == "edit":
                    body["payload"] = edited
                api("POST", f"/intercept/{item['id']}/decision", body)
                if mode == "drop":
                    ok = await received_nothing(c, 0.5) and payload.encode() not in receipts(target, "case=manual")
                else:
                    expected = (edited if mode == "edit" else payload).encode()
                    ok = await received_exact(c, expected, 3) and await wait_receipt(target, "case=manual", expected)
                    if mode == "edit":
                        ok = ok and payload.encode() not in receipts(target, "case=manual")
                result[f"manual_{mode}"] = held and ok
        finally:
            api("POST", "/intercept/manual", {"enabled": False})
        # A successful sentinel after drop distinguishes a live connection from a dead one.
        result["manual_connection_survives"] = await dg_roundtrip(c, b"after-manual")
    return result


EXPECTED = {"connect": True, "path": "/echo", "query": "room=7&x=1",
            "origin": "https://example.test", "uni": True, "bidi_small": True, "bidi_multichunk": True}
CONTROL = ("capture_exact_metadata", "tamper_match_rewritten", "tamper_nonmatch_preserved",
           "replay_to_selected_session", "manual_forward", "manual_edit", "manual_drop",
           "manual_connection_survives")


def _compare(baseline, proxied):
    bc, pc = baseline.get("cases", {}), proxied.get("cases", {})
    out = {key: bc.get(key) == pc.get(key) for key in EXPECTED}
    out.update({f"datagram.{name}": bc.get("datagrams", {}).get(name) == pc.get("datagrams", {}).get(name)
                for name in DATAGRAMS})
    out.update({key: baseline.get(key) == proxied.get(key) for key in ("two_client_isolation", "reconnect")})
    return out


def _summarize(report):
    checks = []
    for side in ("baseline", "proxied"):
        record = report.get(side, {})
        cases = record.get("cases", {})
        for key, expected in EXPECTED.items():
            value = cases.get(key)
            checks.append((f"{side}.{key}", value is True if expected is True else value == expected))
        for name in DATAGRAMS:
            checks.append((f"{side}.datagram.{name}", cases.get("datagrams", {}).get(name) is True))
        for key in ("two_client_isolation", "reconnect"):
            checks.append((f"{side}.{key}", record.get(key) is True))
    # Recompute instead of trusting a potentially stale serialized comparison.
    checks.extend((f"compare.{key}", value is True) for key, value in
                  _compare(report.get("baseline", {}), report.get("proxied", {})).items())
    checks.extend((f"control.{key}", report.get("control", {}).get(key) is True) for key in CONTROL)
    checks.append(("cleanup", report.get("cleanup") is True))
    checks.append(("execution", not report.get("error")))
    if report.get("browser_requested"):
        browser = report.get("browser", {})
        checks.append(("browser", browser.get("passed") is True))
        checks.append(("browser.capture_roundtrip", browser.get("captureRoundtrip") is True))
        for side in ("direct", "proxied"):
            cases = browser.get(side, {})
            datagrams = cases.get("datagrams", [])
            checks.append((f"browser.{side}.nonempty_datagrams",
                           len(datagrams) == 4 and all(value is True for value in datagrams)))
            for key in ("emptyDatagram", "datagramAfterEmpty", "bidiSmall", "bidiMultichunk", "uni", "twoTabs", "reconnect"):
                checks.append((f"browser.{side}.{key}", cases.get(key) is True))
    return checks


async def amain(args):
    global PORTS, TARGET_ADDR
    report = {"target": {}, "baseline": {}, "proxied": {}, "control": {},
              "browser_requested": bool(getattr(args, "browser", False))}
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    PORTS = allocate_ports(KINDS)
    TARGET_ADDR = f"127.0.0.1:{PORTS['upstream']}"
    procs = []
    with tempfile.TemporaryDirectory(prefix="run-", dir=scratch) as run_dir:
        try:
            target_cert = str(Path(run_dir) / "target.pem")
            target = Proc([str(TARGET_EXE), "-addr", TARGET_ADDR, "-certout", target_cert], ROOT)
            procs.append(target)
            hash_line = target.wait_line(lambda line: line.startswith("CERTHASH "), 20)
            ready = target.wait_line(lambda line: line == "READY", 10)
            if not hash_line or not ready or not Path(target_cert).exists():
                raise RuntimeError("Target startup failed: " + "\n".join(target.snapshot()[-10:]))
            chash = hash_line.split()[1]
            report["target"] = {"stack": "quic-go/webtransport-go", "version": "v0.13.0", "addr": TARGET_ADDR}
            report["ports"] = PORTS.copy()

            certs_dir = str(Path(run_dir) / "backend-certs")
            backend = Proc([sys.executable, str(ROOT / "python" / "backend.py"),
                            "--port-proxy", str(PORTS["proxy"]), "--port-target", str(PORTS["target"]),
                            "--port-ws", str(PORTS["ws"]), "--port-api", str(PORTS["api"])], ROOT,
                           env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                                "LEGILIMENS_CONTROL_TOKEN": TOKEN, "LEGILIMENS_CERTS_DIR": certs_dir,
                                "LEGILIMENS_PARENT_PID": str(os.getpid())})
            procs.append(backend)
            if not backend.wait_line(lambda line: line.startswith("READY"), 30):
                # Backend output may contain credentials. Do not put it in the report.
                raise RuntimeError("Backend did not become ready within 30 seconds")
            proxy_cert = str(Path(certs_dir) / "cert.pem")
            api("POST", "/target", {"host": "127.0.0.1", "port": PORTS["upstream"], "certHash": chash})
            api("POST", "/intercept", {"action": "start"})
            for side, port, cert in (("baseline", PORTS["upstream"], target_cert),
                                     ("proxied", PORTS["proxy"], proxy_cert)):
                authority = f"127.0.0.1:{port}"
                report[side]["cases"] = await run_cases("127.0.0.1", port, cert, authority)
                report[side]["two_client_isolation"] = await two_client_isolation("127.0.0.1", port, cert, authority)
                report[side]["reconnect"] = await reconnect("127.0.0.1", port, cert, authority)
            report["control"] = await control_cases(proxy_cert, target)
            if report["browser_requested"]:
                report["browser"] = await run_browser(args, chash)
                report["browser"]["targetDatagramsBase64"] = [base64.b64encode(data).decode()
                    for data in receipts(target, "room=7&x=1&driver=browser")]
                report["browser"]["minimalProbeTargetDatagramsBase64"] = [base64.b64encode(data).decode()
                    for data in receipts(target, "case=minimal-empty")]
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}".replace(TOKEN, "[redacted]")
        finally:
            errors = []
            for proc in reversed(procs):
                try:
                    proc.stop()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if all(port_free(PORTS[name], kind) for name, kind in KINDS.items()):
                    break
                await asyncio.sleep(0.1)
            report["cleanup"] = not errors and all(port_free(PORTS[name], kind) for name, kind in KINDS.items())
            if errors:
                report["cleanup_errors"] = errors
    report["compare"] = _compare(report["baseline"], report["proxied"])
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


async def run_browser(args, target_hash):
    _, cert = api("GET", "/cert-hash")
    output = Path(args.scratch).resolve() / "browser"
    output.mkdir(parents=True, exist_ok=True)
    config = {"api": f"http://127.0.0.1:{PORTS['api']}", "target": TARGET_ADDR,
              "proxy": f"127.0.0.1:{PORTS['proxy']}", "targetHash": target_hash,
              "proxyHash": cert["hash"], "token": TOKEN, "output": str(output)}
    result_path = output / "result.json"
    result_path.unlink(missing_ok=True)
    proc = Proc([args.node, str(ROOT / "interop" / "harness" / "browser.mjs")], ROOT,
                env={"INTEROP_BROWSER_CONFIG": json.dumps(config)}, tree=True)
    try:
        code = await asyncio.to_thread(proc.p.wait, timeout=120)
        if not result_path.exists():
            raise RuntimeError("Browser checks failed: " + "\n".join(proc.snapshot()[-12:]))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["passed"] = code == 0 and result.get("passed") is True
        return result
    finally:
        proc.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--report", default="")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--node", default="node")
    args = ap.parse_args()
    report = asyncio.run(amain(args))
    checks = _summarize(report)
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if report.get("error"):
        print(report["error"])
    if report.get("browser", {}).get("error"):
        print(report["browser"]["error"])
    failed = [name for name, ok in checks if not ok]
    print("RESULT:", "ALL REQUIRED CASES PASS" if not failed else f"FAILURES: {failed}")
    if not args.browser:
        print("Browser interoperability and capture UI: NOT RUN (use --browser)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
