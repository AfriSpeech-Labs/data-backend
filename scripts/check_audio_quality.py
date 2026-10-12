"""Dependency-free verification of app.services.audio_quality.

Builds tiny in-memory WAVs with stdlib `wave` and asserts the backend guard
accepts/rejects correctly. No pytest / numpy. Exits non-zero on failure.

Usage (from backend/):  python scripts/check_audio_quality.py
"""
import io
import math
import os
import struct
import sys
import wave

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException  # noqa: E402

from app.services import audio_quality as aq  # noqa: E402


def make_wav(
    *,
    sample_rate: int = 24000,
    duration_s: float = 2.0,
    amplitude: float = 0.3,
    clip_fraction: float = 0.0,
    sample_width: int = 2,
) -> bytes:
    """Generate a mono sine WAV. ``clip_fraction`` forces that fraction of
    samples to full-scale (to exercise the clipping check)."""
    n = int(sample_rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        max_int = (1 << (8 * sample_width - 1)) - 1
        clip_every = int(1 / clip_fraction) if clip_fraction > 0 else 0
        frames = bytearray()
        for i in range(n):
            if clip_every and (i % clip_every == 0):
                val = max_int  # full-scale => clipped
            else:
                val = int(amplitude * max_int * math.sin(2 * math.pi * 220 * i / sample_rate))
            if sample_width == 2:
                frames += struct.pack("<h", max(-max_int, min(max_int, val)))
            elif sample_width == 4:
                frames += struct.pack("<i", max(-max_int, min(max_int, val)))
            else:
                frames += struct.pack("<B", 128 + int(val / 256))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def expect_pass(name, data, expected_seconds):
    try:
        aq.assert_quality(data, expected_seconds)
        print(f"  PASS  {name}")
        return 0
    except HTTPException as e:
        print(f"  FAIL  {name}: unexpected reject ({e.detail})")
        return 1


def expect_reject(name, data, expected_seconds, want_detail):
    try:
        aq.assert_quality(data, expected_seconds)
        print(f"  FAIL  {name}: expected reject, got accept")
        return 1
    except HTTPException as e:
        if e.status_code == 422 and e.detail == want_detail:
            print(f"  PASS  {name}")
            return 0
        print(f"  FAIL  {name}: got {e.status_code} {e.detail!r}, wanted 422 {want_detail!r}")
        return 1


def main():
    failures = 0

    # Good clip: 24 kHz, ~2 s, audible, no clipping. Transcript ~5 words.
    good = make_wav(sample_rate=24000, duration_s=2.0, amplitude=0.3)
    expected = aq.expected_seconds_for_text("one two three four five")  # 5*0.4 = 2.0
    failures += expect_pass("good clip accepted", good, expected)

    # Format: 16 kHz < 24 kHz floor.
    low_sr = make_wav(sample_rate=16000, duration_s=2.0, amplitude=0.3)
    failures += expect_reject("low sample rate rejected", low_sr, expected, aq.MSG_FORMAT)

    # Level: peak below 0.05.
    quiet = make_wav(sample_rate=24000, duration_s=2.0, amplitude=0.01)
    failures += expect_reject("too quiet rejected", quiet, expected, aq.MSG_LEVEL)

    # Distortion: >=0.1% clipped samples.
    clipped = make_wav(sample_rate=24000, duration_s=2.0, amplitude=0.3, clip_fraction=0.02)
    failures += expect_reject("clipping rejected", clipped, expected, aq.MSG_DISTORTION)

    # Length: 2 s clip but transcript expects ~20 words (8 s) -> out of band.
    long_expected = aq.expected_seconds_for_text(" ".join(["w"] * 20))  # 8.0s
    failures += expect_reject("too short for transcript rejected", good, long_expected, aq.MSG_LENGTH)

    # Length skipped when no transcript: a 2 s clip with None expected passes.
    failures += expect_pass("no-transcript skips length check", good, None)

    # Band math sanity.
    lo, hi = aq.duration_band(2.0)
    if not (abs(lo - 0.0) < 1e-9 and abs(hi - 4.0) < 1e-9):
        print(f"  FAIL  band math: got ({lo},{hi}), wanted (0.0,4.0)")
        failures += 1
    else:
        print("  PASS  band math")

    print()
    if failures:
        print(f"{failures} check(s) FAILED")
        sys.exit(1)
    print("All audio_quality checks passed.")


if __name__ == "__main__":
    main()
