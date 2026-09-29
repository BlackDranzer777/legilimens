"""Media runner verdict tests without Docker or browser prerequisites."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "videocall"))
from media_results import driver_result, run_verdict, video_baseline_ready


def good():
    return {"passed": True, "status": "PASS", "videoPassed": True, "cleanup": True,
            "errors": [], "serverAttribution": True,
            "video": {"aToB": {"status": "PASS"}, "bToA": {"status": "PASS"}},
            "audio": {"aToB": {"status": "PASS"}, "bToA": {"status": "PASS"}},
            "controls": {s: {"status": "PASS", "audioReceipt": {"status": "PASS"}} for s in ("A", "B")}}


class MediaVerdictTests(unittest.TestCase):
    def test_nonzero_exit_cannot_pass(self):
        for code in (1, 2, -9, None):
            with self.subTest(code=code):
                result = driver_result(good(), code)
                self.assertFalse(result["passed"])
                self.assertFalse(video_baseline_ready(result))

    def test_missing_report_cannot_pass(self):
        for data in (None, {}, [], {"passed": True}):
            self.assertFalse(driver_result(data, 0)["passed"])

    def test_blocked_audio_is_not_complete_success(self):
        data = good()
        data["audio"]["bToA"]["status"] = "BLOCKED"
        self.assertFalse(driver_result(data, 0)["passed"])

    def test_mic_ui_without_remote_audio_control_evidence_cannot_pass(self):
        data = good()
        del data["controls"]["A"]["audioReceipt"]
        self.assertFalse(driver_result(data, 0)["passed"])

    def test_explicit_video_only_result_can_guide_comparison_but_not_pass(self):
        data = good()
        data.update(passed=False, status="BLOCKED")
        data["audio"] = {d: {"status": "BLOCKED"} for d in ("aToB", "bToA")}
        result = driver_result(data, 2)
        self.assertTrue(video_baseline_ready(result))
        verdict = run_verdict({"direct": result, "proxied": result}, True)
        self.assertTrue(verdict["videoPassed"])
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["status"], "BLOCKED")

    def test_cleanup_and_missing_routes_fail_closed(self):
        result = driver_result(good(), 0)
        self.assertTrue(run_verdict({"direct": result, "proxied": result}, True)["passed"])
        self.assertFalse(run_verdict({"direct": result}, True)["passed"])
        self.assertFalse(run_verdict({"direct": result, "proxied": result}, False)["passed"])
        self.assertFalse(run_verdict({"direct": result, "proxied": result}, True, "exception")["passed"])

    def test_missing_controls_and_runtime_errors_invalidate_video_baseline(self):
        for change in ({"controls": {}}, {"errors": ["unreachable"]}, {"cleanup": False}, {"serverAttribution": False}):
            with self.subTest(change=change):
                data = good()
                data.update(copy.deepcopy(change))
                result = driver_result(data, 0)
                self.assertFalse(result["passed"])
                self.assertFalse(video_baseline_ready(result))


if __name__ == "__main__":
    unittest.main()
