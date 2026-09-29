"""Generate distinct, deterministic synthetic media fixtures for two participants.

Video: 640x360 Y4M (I420, full-range) at 10 fps. Each participant has a distinct
identity background colour and centred letter, PLUS an advancing progress bar and a
per-frame counter so a receiver can prove it sees *live decoded* frames from the other
participant (identity + advancing sequence), not a frozen image or self-preview.

Audio: 48 kHz 16-bit mono WAV with a participant-specific repeating tone/silence
sequence (distinct base frequencies), long enough to distinguish from join beeps.

Chrome consumes these with --use-file-for-fake-video-capture / -audio-capture.
Parameters, seed, durations, formats and SHA-256 hashes are written to fixtures.json.
"""
import argparse
import hashlib
import json
import struct
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT, FPS = 640, 360, 10
VIDEO_SECONDS = 6           # loops in the browser; counter cycles across this span
AUDIO_SECONDS = 6
AUDIO_RATE = 48000

# Per-participant identity: (bg colour, letter). Audio identity is the signature below.
PARTICIPANTS = {
    "A": {"color": (210, 45, 45), "letter": "A"},   # red
    "B": {"color": (45, 90, 215), "letter": "B"},   # blue
}

# Deterministic, run-specific / participant-specific audio signature.
#
# Harmonic-safe alphabets: all tones live in [900,1500] Hz, so their 2nd harmonics
# (>=1800 Hz) fall OUTSIDE the analysis band -> no self/other-harmonic confusion. A
# occupies the lower sub-band and B the upper, so the receiver can separate participant
# identity by band before decoding the sequence. (The legacy 440/880 Hz tones were
# octave-related and are deliberately NOT reused.) These constants MUST match
# interop/videocall/media-audio.mjs (ALPHABETS / CONTROL_HZ / SIGNATURE_VERSION / timing).
AUDIO_AMPLITUDE = 0.5
SIGNATURE = {
    "version": "sig-v1",
    "alphabets": {"A": [943, 1013, 1087, 1163], "B": [1237, 1307, 1381, 1459]},
    "controlHz": [800, 1200, 1600],
    "toneSec": 0.30, "gapSec": 0.15, "sequenceLength": 12,
}


def derive_sequence(run_id, participant, alphabet, length):
    """Per-index sha256 -> first byte mod alphabet size, with a rotate so no two
    consecutive symbols repeat (guarantees a detectable transition every slot).
    MUST stay byte-for-byte identical to deriveSequence() in media-audio.mjs."""
    seq = []
    prev = -1
    for i in range(length):
        digest = hashlib.sha256(f"{run_id}:{participant}:{SIGNATURE['version']}:{i}".encode()).digest()
        idx = digest[0] % len(alphabet)
        if idx == prev:
            idx = (idx + 1) % len(alphabet)
        seq.append(alphabet[idx])
        prev = idx
    return seq


def _font(size):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgb_to_i420(rgb: np.ndarray) -> bytes:
    r = rgb[:, :, 0].astype(np.float32); g = rgb[:, :, 1].astype(np.float32); b = rgb[:, :, 2].astype(np.float32)
    y = (0.299 * r + 0.587 * g + 0.114 * b)
    u = (-0.168736 * r - 0.331264 * g + 0.5 * b + 128.0)
    v = (0.5 * r - 0.418688 * g - 0.081312 * b + 128.0)
    y = np.clip(y, 0, 255).astype(np.uint8)
    # 2x2 average downsample for U and V planes
    u2 = np.clip(u.reshape(HEIGHT // 2, 2, WIDTH // 2, 2).mean(axis=(1, 3)), 0, 255).astype(np.uint8)
    v2 = np.clip(v.reshape(HEIGHT // 2, 2, WIDTH // 2, 2).mean(axis=(1, 3)), 0, 255).astype(np.uint8)
    return y.tobytes() + u2.tobytes() + v2.tobytes()


def make_video(path: Path, spec, run_id, frames):
    big = _font(180); small = _font(28)
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F{FPS}:1 Ip A1:1 C420jpeg\n".encode())
        for i in range(frames):
            img = Image.new("RGB", (WIDTH, HEIGHT), spec["color"])
            d = ImageDraw.Draw(img)
            d.text((WIDTH // 2 - 60, HEIGHT // 2 - 110), spec["letter"], fill=(255, 255, 255), font=big)
            d.text((12, 8), f"{run_id} f={i:03d}", fill=(255, 255, 255), font=small)
            # Machine-readable run/participant identity, separate from the motion bar.
            marker = int(spec["marker"], 16)
            for bit in range(16):
                value = 245 if marker & (1 << (15 - bit)) else 10
                x = 12 + bit * 32
                d.rectangle([x, HEIGHT - 80, x + 31, HEIGHT - 65], fill=(value,) * 3)
            # advancing bar (fills left->right across the loop) proves live progression
            bar_w = int((i + 1) / frames * (WIDTH - 24))
            d.rectangle([12, HEIGHT - 40, 12 + bar_w, HEIGHT - 16], fill=(255, 255, 255))
            f.write(b"FRAME\n")
            f.write(_rgb_to_i420(np.asarray(img)))


def make_audio(path: Path, sequence_hz, tone_sec, gap_sec):
    """Render the tone sequence: each symbol is `tone_sec` of its frequency followed by
    `gap_sec` of silence. Any remainder of the AUDIO_SECONDS file is trailing silence,
    which Chrome loops — so the sequence and a truncated silent tail repeat every loop.
    The receiver-side evaluator matches the sequence cyclically and treats the silent
    tail / loop boundary as expected (it does NOT require a mostly-non-silent file)."""
    total = AUDIO_RATE * AUDIO_SECONDS
    out = np.zeros(total, dtype=np.float32)
    period = int(round((tone_sec + gap_sec) * AUDIO_RATE))
    seg = int(round(tone_sec * AUDIO_RATE))
    cursor = 0
    for freq in sequence_hz:
        a = cursor
        b = min(a + seg, total)
        if a >= total:
            break
        t = np.arange(b - a) / AUDIO_RATE
        out[a:b] += AUDIO_AMPLITUDE * np.sin(2 * np.pi * freq * t)
        cursor += period
    pcm16 = (np.clip(out, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(AUDIO_RATE)
        w.writeframes(pcm16.tobytes())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def generate(out_dir: Path, run_id: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = VIDEO_SECONDS * FPS
    manifest = {"runId": run_id, "video": {"width": WIDTH, "height": HEIGHT, "fps": FPS, "frames": frames,
                "format": "y4m/I420/C420jpeg", "seconds": VIDEO_SECONDS},
                "audio": {"rate": AUDIO_RATE, "seconds": AUDIO_SECONDS, "format": "wav/pcm_s16le/mono",
                          "signature": {"version": SIGNATURE["version"], "controlHz": SIGNATURE["controlHz"],
                                        "toneSec": SIGNATURE["toneSec"], "gapSec": SIGNATURE["gapSec"],
                                        "sequenceLength": SIGNATURE["sequenceLength"]}},
                "participants": {}}
    for name, base in PARTICIPANTS.items():
        spec = {**base, "marker": hashlib.sha256(f"{run_id}:{name}".encode()).hexdigest()[:4]}
        alphabet = SIGNATURE["alphabets"][name]
        sequence = derive_sequence(run_id, name, alphabet, SIGNATURE["sequenceLength"])
        vp = out_dir / f"{name}.y4m"; ap = out_dir / f"{name}.wav"
        make_video(vp, spec, run_id, frames)
        make_audio(ap, sequence, SIGNATURE["toneSec"], SIGNATURE["gapSec"])
        manifest["participants"][name] = {
            "video": str(vp), "audio": str(ap), "color": spec["color"], "letter": spec["letter"],
            "marker": spec["marker"], "videoSha256": sha256(vp), "audioSha256": sha256(ap),
            "tones": alphabet,  # legacy compatibility for verify-fixtures expectTones
            "signature": {"version": SIGNATURE["version"], "alphabetHz": alphabet,
                          "controlHz": SIGNATURE["controlHz"], "sequenceHz": sequence,
                          "toneSec": SIGNATURE["toneSec"], "gapSec": SIGNATURE["gapSec"],
                          "sequenceLength": SIGNATURE["sequenceLength"], "sampleRate": AUDIO_RATE}}
    (out_dir / "fixtures.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", default="run")
    args = ap.parse_args()
    m = generate(Path(args.out).resolve(), args.run_id)
    print(json.dumps({k: {"videoSha256": v["videoSha256"][:16], "audioSha256": v["audioSha256"][:16]}
                      for k, v in m["participants"].items()}, indent=2))


if __name__ == "__main__":
    main()
