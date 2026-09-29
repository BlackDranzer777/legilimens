"""Deterministic per-run fixture identity without launching a browser."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "videocall"))
import media_fixtures


class MediaFixtureTests(unittest.TestCase):
    def test_markers_are_bound_to_run_and_participant(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(media_fixtures, "FPS", 2), patch.object(media_fixtures, "VIDEO_SECONDS", 1):
            first = media_fixtures.generate(Path(tmp) / "one", "run-one")
            second = media_fixtures.generate(Path(tmp) / "two", "run-two")
            for label in ("A", "B"):
                p = first["participants"][label]
                self.assertEqual(p["marker"], hashlib.sha256(f"run-one:{label}".encode()).hexdigest()[:4])
                self.assertNotEqual(p["marker"], second["participants"][label]["marker"])
                self.assertEqual(media_fixtures.sha256(Path(p["video"])), p["videoSha256"])

    def test_signature_sequence_is_deterministic_run_and_participant_specific(self):
        # Locked cross-language vector: must equal deriveSequence() in media-audio.mjs.
        self.assertEqual(
            media_fixtures.derive_sequence("run-one", "A", media_fixtures.SIGNATURE["alphabets"]["A"], 12),
            [1087, 1013, 1163, 943, 1087, 1163, 943, 1013, 1087, 1163, 1013, 943])
        a1 = media_fixtures.derive_sequence("run-one", "A", media_fixtures.SIGNATURE["alphabets"]["A"], 12)
        a2 = media_fixtures.derive_sequence("run-two", "A", media_fixtures.SIGNATURE["alphabets"]["A"], 12)
        b1 = media_fixtures.derive_sequence("run-one", "B", media_fixtures.SIGNATURE["alphabets"]["B"], 12)
        self.assertNotEqual(a1, a2)                       # run-specific
        self.assertNotEqual(a1, b1)                       # participant-specific
        for seq, letter in ((a1, "A"), (a2, "A"), (b1, "B")):
            for i in range(1, len(seq)):
                self.assertNotEqual(seq[i], seq[i - 1])   # no consecutive repeats
            self.assertTrue(all(f in media_fixtures.SIGNATURE["alphabets"][letter] for f in seq))

    def test_generate_records_signature_and_wav_matches_the_sequence(self):
        import wave
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            man = media_fixtures.generate(Path(tmp) / "run", "run-one")
            sig = man["participants"]["A"]["signature"]
            self.assertEqual(sig["sequenceHz"],
                             media_fixtures.derive_sequence("run-one", "A", sig["alphabetHz"], 12))
            self.assertEqual(man["participants"]["A"]["audioSha256"],
                             media_fixtures.sha256(Path(man["participants"]["A"]["audio"])))
            with wave.open(man["participants"]["A"]["audio"], "rb") as w:
                rate = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
            self.assertEqual(rate, media_fixtures.AUDIO_RATE)
            period = int(round((sig["toneSec"] + sig["gapSec"]) * rate))

            def goertzel(x, freq):
                k = 2 * np.cos(2 * np.pi * freq / rate)
                s1 = s2 = 0.0
                for v in x:
                    s0 = v + k * s1 - s2
                    s2, s1 = s1, s0
                return max(0.0, s1 * s1 + s2 * s2 - k * s1 * s2) / (len(x) ** 2)

            for i in range(3):  # first few slots: dominant tone must equal the sequence
                seg = pcm[i * period:i * period + 2400]
                dominant = max(sig["alphabetHz"], key=lambda f: goertzel(seg, f))
                self.assertEqual(dominant, sig["sequenceHz"][i])

    def test_i420_luma_contains_the_recorded_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.y4m"
            media_fixtures.make_video(path, {**media_fixtures.PARTICIPANTS["A"], "marker": "9a12"}, "fixture", 1)
            with path.open("rb") as video:
                self.assertTrue(video.readline().startswith(b"YUV4MPEG2"))
                self.assertEqual(video.readline(), b"FRAME\n")
                y = video.read(640 * 360)
            marker = 0
            for bit in range(16):
                marker = (marker << 1) | (y[288 * 640 + 28 + bit * 32] > 128)
            self.assertEqual(marker, 0x9a12)


if __name__ == "__main__":
    unittest.main()
