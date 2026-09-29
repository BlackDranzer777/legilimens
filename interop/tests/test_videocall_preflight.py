import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("videocall_preflight", Path(__file__).resolve().parents[1] / "videocall/preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightTests(unittest.TestCase):
    def test_wrong_revision_rejected(self):
        with patch.object(preflight, "command", return_value="wrong"):
            with self.assertRaisesRegex(ValueError, "Wrong source revision"):
                preflight.inspect_source(Path.cwd(), {"commit": "pinned"})

    def test_dirty_source_rejected(self):
        with patch.object(preflight, "command", side_effect=["pinned", " M Cargo.lock"]):
            with self.assertRaisesRegex(ValueError, "checkout has changes"):
                preflight.inspect_source(Path.cwd(), {"commit": "pinned"})

    def test_missing_source_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(preflight, "command", side_effect=["pinned", ""]):
                with self.assertRaisesRegex(ValueError, "Missing target source"):
                    preflight.inspect_source(Path(directory), {"commit": "pinned", "requiredFiles": ["Cargo.lock"]})

    def test_engine_unavailable_is_not_success(self):
        with patch.object(preflight, "command", side_effect=RuntimeError("unavailable")):
            self.assertIs(preflight.inspect_docker()["available"], False)

    def test_windows_engine_is_not_linux(self):
        with patch.object(preflight, "command", return_value='{"Os":"windows"}'):
            self.assertIs(preflight.inspect_docker()["available"], False)

    def test_linux_engine_identified(self):
        with patch.object(preflight, "command", return_value='{"Os":"linux","Version":"test"}'):
            self.assertEqual(preflight.inspect_docker(), {"available": True, "version": "test", "os": "linux"})
