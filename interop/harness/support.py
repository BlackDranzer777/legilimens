"""Bounded process and socket helpers for the loopback interop harness."""

from collections import deque
import os
import signal
import socket
import subprocess
import threading
import time


def bound_socket(port, kind):
    sock = socket.socket(socket.AF_INET, kind)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        return sock
    except BaseException:
        sock.close()
        raise


def port_free(port, kind):
    try:
        with bound_socket(port, kind):
            return True
    except OSError:
        return False


def allocate_ports(kinds):
    # Keep reservations until every port is selected. Startup still detects races
    # after release; neither backend may report READY before binding its sockets.
    sockets = []
    try:
        for kind in kinds.values():
            sockets.append(bound_socket(0, kind))
        return dict(zip(kinds, (s.getsockname()[1] for s in sockets)))
    finally:
        for sock in sockets:
            sock.close()


class Proc:
    def __init__(self, args, cwd, env=None, tree=False):
        self.tree = tree
        self.lines = deque(maxlen=2048)
        self.condition = threading.Condition()
        self.sequence = 0
        self.cursor = 0
        self.eof = False
        self.p = subprocess.Popen(args, cwd=str(cwd), env={**os.environ, **(env or {})},
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  encoding="utf-8", errors="replace",
                                  start_new_session=tree and os.name != "nt")
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self):
        try:
            while line := self.p.stdout.readline(65536):
                with self.condition:
                    self.sequence += 1
                    self.lines.append((self.sequence, line.strip()))
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.eof = True
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return [line for _, line in self.lines]

    def wait_line(self, pred, timeout=30):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for seq, line in self.lines:
                    if seq > self.cursor:
                        self.cursor = seq
                        if pred(line):
                            return line
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.eof:
                    return None
                self.condition.wait(remaining)

    def stop(self):
        if self.p.poll() is None:
            if self.tree and os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.p.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
            elif self.tree:
                os.killpg(self.p.pid, signal.SIGKILL)
            else:
                self.p.kill()
        self.p.wait(timeout=5)
        self.reader.join(timeout=5)
        if self.reader.is_alive():
            raise RuntimeError("Child output reader did not stop")
        self.p.stdout.close()
