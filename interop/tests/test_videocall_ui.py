import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

spec = importlib.util.spec_from_file_location("serve_ui", Path(__file__).resolve().parents[1] / "videocall/serve_ui.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class LocalUiTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("VIDEOCALL_WORKLET_TEST") == "1", "opt-in Chrome worklet policy probe")
    def test_actual_serving_policy_allows_local_audio_worklet(self):
        with tempfile.TemporaryDirectory(prefix="videocall-worklet-") as directory:
            dist = Path(directory)
            (dist / "index.html").write_text("<!doctype html><title>Local worklet probe</title>")
            server = ui.serve(dist, 0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                result = subprocess.run([os.environ.get("VIDEOCALL_NODE", "node"),
                    str(Path(__file__).resolve().parents[1] / "videocall/probe-worklet.mjs"),
                    f"http://127.0.0.1:{server.server_port}/"], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    @unittest.skipUnless(os.environ.get("VIDEOCALL_UI_BROWSER_TEST") == "1", "opt-in Chrome fault injection")
    def test_missing_worker_wasm_fails_real_browser_verification(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="videocall-ui-fault-") as directory:
            staged = Path(directory) / "dist"
            shutil.copytree(root / "build/videocall-frontend/dist", staged)
            (staged / "worker_decoder_bg.wasm").unlink()
            report = Path(directory) / "report.json"
            result = subprocess.run([sys.executable, str(root / "interop/videocall/verify_frontend.py"),
                "--node", os.environ.get("VIDEOCALL_NODE", "node"), "--dist", str(staged),
                "--report", str(report)], capture_output=True, text=True, timeout=110)
            self.assertNotEqual(result.returncode, 0)
            evidence = json.loads(report.read_text())
            self.assertFalse(evidence["passed"])
            self.assertTrue(evidence["cleanup"])
            self.assertIn({"path": "/worker_decoder_bg.wasm", "status": 404}, evidence["serverRequests"])

    def test_rejects_nonlocal_or_malformed_transport_endpoints(self):
        for endpoint in ("https://example.com:443", "https://127.0.0.1.example.com:443",
                         "https://user@localhost:443", "http://localhost:443", "https://localhost:0",
                         "https://localhost:443/?x=1", "https://localhost:443/#x"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ui.local_config(8081, 8080, endpoint)

    def test_validates_api_ports(self):
        for port in (0, -1, 65536, "8080;bad"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                ui.local_config(port, 8080, "https://127.0.0.1:4433")

    def test_served_overlay_csp_mime_and_missing_assets(self):
        with tempfile.TemporaryDirectory() as original:
            dist = Path(original)
            (dist / "index.html").write_text("<html>fixture</html>")
            (dist / "test.wasm").write_bytes(b"\x00asm")
            (dist / "config.js").write_text("unchanged")
            staged = ui.stage(dist, 8121, 8122, "https://127.0.0.1:8123")
            server = ui.serve(staged, 0, 8121, 8122, "https://127.0.0.1:8123")
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                with urlopen(origin + "/") as response:
                    csp = response.headers['Content-Security-Policy']
                    self.assertIn("default-src 'none'", csp)
                    self.assertIn("script-src 'self' blob:", csp)
                    self.assertNotIn("matomo", csp)
                    self.assertIn("https://127.0.0.1:8123", csp)
                with urlopen(origin + "/test.wasm") as response:
                    self.assertEqual(response.headers.get_content_type(), "application/wasm")
                with urlopen(origin + "/favicon.ico") as response:
                    self.assertEqual(response.status, 204)
                with self.assertRaises(HTTPError) as error:
                    urlopen(origin + "/missing.js")
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
                self.assertIn({"path": "/missing.js", "status": 404}, server.requests)
                self.assertEqual((dist / "config.js").read_text(), "unchanged")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                shutil.rmtree(staged)
