"""Compute duration of WAV file in seconds using stdlib wave."""
import io
import wave


def get_wav_duration_seconds(data: bytes) -> float:
    with io.BytesIO(data) as buf:
        with wave.open(buf, "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate) if rate else 0.0
