"""Server-side audio quality guard (stdlib only — no numpy, keeps bundle lean).

Enforces the four cheap checks on upload: sample rate, duration band, peak
level, and clipping. SNR/noise is NOT checked server-side (client-only).

Thresholds mirror the web client (`web/src/lib/audioQuality.ts` /
`web/src/lib/expectedDuration.ts`); keep the numbers in sync.
"""
from __future__ import annotations

import io
import wave

from fastapi import HTTPException

# Thresholds (match the web client exactly).
SAMPLE_RATE_FLOOR = 24000  # Hz
PEAK_FLOOR = 0.05  # normalized -1..1
CLIPPED_RATIO_CEIL = 0.001  # 0.1%
CLIP_LEVEL = 0.99  # |sample| >= this counts as clipped

# Expected-duration band (match expectedDuration.ts).
SECONDS_PER_WORD = 0.4
DURATION_BUFFER_SECONDS = 1.0

# Exact client-facing messages.
MSG_FORMAT = "Your device recorded in low quality"
MSG_LEVEL = "Too quiet: move closer…"
MSG_DISTORTION = "Too loud or too close…"
MSG_LENGTH = "Recording cut off, or a long silence…"

_INT16_MAX = 32767.0
_INT32_MAX = 2147483647.0
_INT8_MID = 128.0


def count_words(text: str | None) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    return len(t.split())


def expected_seconds_for_text(
    text: str | None, seconds_per_word: float = SECONDS_PER_WORD
) -> float | None:
    """Expected speech seconds from a transcript; None when there is no text."""
    n = count_words(text)
    if n == 0:
        return None
    return n * seconds_per_word


def duration_band(
    expected_seconds: float, buffer_seconds: float = DURATION_BUFFER_SECONDS
) -> tuple[float, float]:
    return (
        max(0.0, 0.5 * expected_seconds - buffer_seconds),
        1.5 * expected_seconds + buffer_seconds,
    )


def _normalize_peak_and_clip(
    frames: bytes, sample_width: int, n_channels: int
) -> tuple[float, float]:
    """Return (peak_ratio, clipped_ratio) over all samples, normalized to 0..1."""
    import array

    if sample_width == 2:
        a = array.array("h")  # signed 16-bit
        a.frombytes(frames)
        denom = _INT16_MAX
        samples = a
        peak = 0.0
        clipped = 0
        total = len(samples)
        for s in samples:
            v = abs(s) / denom
            if v > peak:
                peak = v
            if v >= CLIP_LEVEL:
                clipped += 1
        return peak, (clipped / total if total else 0.0)

    if sample_width == 4:
        a = array.array("i")  # signed 32-bit
        a.frombytes(frames)
        denom = _INT32_MAX
        peak = 0.0
        clipped = 0
        total = len(a)
        for s in a:
            v = abs(s) / denom
            if v > peak:
                peak = v
            if v >= CLIP_LEVEL:
                clipped += 1
        return peak, (clipped / total if total else 0.0)

    if sample_width == 1:
        # 8-bit PCM is unsigned, centered at 128.
        peak = 0.0
        clipped = 0
        total = len(frames)
        for b in frames:
            v = abs(b - _INT8_MID) / _INT8_MID
            if v > peak:
                peak = v
            if v >= CLIP_LEVEL:
                clipped += 1
        return peak, (clipped / total if total else 0.0)

    # Unknown width: treat as unanalyzable rather than guessing.
    return 1.0, 0.0


def analyze_wav(data: bytes) -> dict:
    """Parse a WAV and return {sample_rate, duration_s, peak_ratio, clipped_ratio}.

    Raises HTTPException(400) if the bytes are not a readable WAV.
    """
    try:
        with io.BytesIO(data) as buf, wave.open(buf, "rb") as wav:
            sample_rate = wav.getframerate()
            n_frames = wav.getnframes()
            sample_width = wav.getsampwidth()
            n_channels = wav.getnchannels()
            raw = wav.readframes(n_frames)
    except (wave.Error, EOFError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid WAV file: {e}") from e

    duration_s = n_frames / float(sample_rate) if sample_rate else 0.0
    peak_ratio, clipped_ratio = _normalize_peak_and_clip(
        raw, sample_width, n_channels
    )
    return {
        "sample_rate": sample_rate,
        "duration_s": duration_s,
        "peak_ratio": peak_ratio,
        "clipped_ratio": clipped_ratio,
    }


def assert_quality(data: bytes, expected_seconds: float | None) -> dict:
    """Enforce the four cheap checks; raise HTTPException(422) on failure.

    Returns the analysis dict on success so callers can persist the metrics.
    ``expected_seconds=None`` skips the length check (no transcript).
    """
    metrics = analyze_wav(data)

    if metrics["sample_rate"] < SAMPLE_RATE_FLOOR:
        raise HTTPException(status_code=422, detail=MSG_FORMAT)
    if metrics["peak_ratio"] < PEAK_FLOOR:
        raise HTTPException(status_code=422, detail=MSG_LEVEL)
    if metrics["clipped_ratio"] >= CLIPPED_RATIO_CEIL:
        raise HTTPException(status_code=422, detail=MSG_DISTORTION)
    if expected_seconds is not None:
        lo, hi = duration_band(expected_seconds)
        if metrics["duration_s"] < lo or metrics["duration_s"] > hi:
            raise HTTPException(status_code=422, detail=MSG_LENGTH)

    return metrics
