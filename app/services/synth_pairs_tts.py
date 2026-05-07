"""TTS for parallel_synth_pairs: English (reuse stack) + Swahili (Edge multi-voice)."""
import random
from typing import Final

from app.config import settings
from app.services.tts_english import EDGE_VOICE, generate_wav_edge_tts, generate_wav_from_text

# (voice_short_name, gender label) — Phase A: Edge only
SWAHILI_VOICES: Final[list[tuple[str, str]]] = [
    ("sw-KE-RafikiNeural", "male"),
    ("sw-KE-ZuriNeural", "female"),
    ("sw-TZ-DaudiNeural", "male"),
    ("sw-TZ-RehemaNeural", "female"),
]


def pick_swahili_voice() -> tuple[str, str]:
    """Return (edge_voice_short_name, gender_sw)."""
    voice, gender = random.choice(SWAHILI_VOICES)
    return voice, gender


def maybe_augment_wav(wav: bytes) -> bytes:
    """Placeholder when synth_augment_enabled; torchaudio pipeline not shipped (plan optional)."""
    if settings.synth_augment_enabled:
        # Reserved: optional torchaudio transforms — off by default
        pass
    return wav


def generate_english_wav_for_synth(text: str) -> tuple[bytes, str]:
    """English WAV bytes and persisted voice label."""
    wav = generate_wav_from_text(text)
    wav = maybe_augment_wav(wav)
    return wav, EDGE_VOICE


def generate_swahili_wav_for_synth(text: str) -> tuple[bytes, str, str, str]:
    """Swahili WAV, voice short name, engine label, gender_sw."""
    engine = (settings.synth_sw_engine or "edge").strip().lower()
    if engine != "edge":
        raise ValueError(
            f'Swahili TTS engine "{engine}" is not implemented; use synth_sw_engine=edge.',
        )
    text = (text or "").strip()
    if not text:
        raise ValueError("Swahili text is required for synthesis")
    voice, gender_sw = pick_swahili_voice()
    wav = generate_wav_edge_tts(text, voice)
    wav = maybe_augment_wav(wav)
    return wav, voice, "edge", gender_sw
