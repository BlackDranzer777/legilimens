"""Fail-closed aggregation for the media subprocess and owned-resource runner."""
from copy import deepcopy


def driver_result(data, exit_code):
    result = deepcopy(data) if isinstance(data, dict) else {}
    result["exit"] = exit_code
    expected_exit = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}.get(result.get("status"))
    if expected_exit is None or exit_code != expected_exit:
        result.update(passed=False, videoPassed=False, status="FAIL",
                      driverError="Missing report or inconsistent/nonzero child exit")
        return result
    complete = all(result.get(media, {}).get(direction, {}).get("status") == "PASS"
                   for media in ("video", "audio") for direction in ("aToB", "bToA"))
    controls = all(result.get("controls", {}).get(sender, {}).get("status") == "PASS"
                   for sender in ("A", "B"))
    audio_controls = all(result.get("controls", {}).get(sender, {}).get("audioReceipt", {}).get("status") == "PASS"
                         for sender in ("A", "B"))
    healthy = (result.get("cleanup") is True and result.get("errors") == []
               and not result.get("error") and result.get("serverAttribution") is True)
    if result.get("passed") is True and not (exit_code == 0 and complete and controls and audio_controls and healthy):
        result.update(passed=False, videoPassed=False, status="FAIL", driverError="Incomplete evidence claimed success")
    if not healthy or not controls:
        result["videoPassed"] = False
    return result


def video_baseline_ready(result):
    # A deliberate exit 2 may carry useful video-only evidence; never an A/V pass.
    return (result.get("videoPassed") is True and result.get("status") in {"PASS", "BLOCKED"}
            and result.get("exit") == (0 if result.get("status") == "PASS" else 2)
            and not result.get("driverError"))


def run_verdict(routes, cleanup, error=None):
    required = [routes.get(route, {}) for route in ("direct", "proxied")]
    passed = cleanup is True and not error and all(r.get("passed") is True and r.get("exit") == 0 for r in required)
    failed = cleanup is not True or bool(error) or any(r.get("status") == "FAIL" for r in required)
    return {"passed": passed, "status": "PASS" if passed else "FAIL" if failed else "BLOCKED",
            "videoPassed": cleanup is True and not error and all(video_baseline_ready(r) for r in required)}
