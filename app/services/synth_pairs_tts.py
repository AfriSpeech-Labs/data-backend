"""TTS for parallel_synth_pairs: pair-aware source/target synthesis."""
from __future__ import annotations

import random

from app.config import settings
from app.services.pair_tts_config import (
    ENGLISH_VOICES,
    SWAHILI_VOICES,
    TtsSideConfig,
    resolve_pair_tts,
)
from app.services.tts_english import EDGE_VOICE, generate_wav_edge_tts


class MmsTtsUnsupportedError(RuntimeError):
    """Raised when a pair requests the MMS engine, which is no longer supported.

    MMS (torch/transformers) synthesis was removed when the API moved to a
    serverless host. Generate MMS audio offline on an admin machine with the
    optional TTS toolchain, then upload the WAVs via the insert endpoints.
    """


def pick_edge_voice(voices: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    """Return (edge_voice_short_name, gender)."""
    if not voices:
        raise ValueError("No Edge voices configured for this side")
    return random.choice(voices)


def maybe_augment_wav(wav: bytes) -> bytes:
    """Placeholder when synth_augment_enabled; torchaudio pipeline not shipped."""
    if settings.synth_augment_enabled:
        pass
    return wav


def _synthesize_side(text: str, side: TtsSideConfig, side_label: str) -> tuple[bytes, str, str, str | None]:
    text = (text or "").strip()
    if not text:
        raise ValueError(f"{side_label} text is required for synthesis")

    engine = side.engine
    if engine == "edge":
        voice, gender = pick_edge_voice(side.edge_voices)
        wav = generate_wav_edge_tts(text, voice)
        wav = maybe_augment_wav(wav)
        return wav, voice, "edge", gender

    if engine == "mms":
        raise MmsTtsUnsupportedError(
            f"{side_label} uses the MMS engine, which is no longer available on the "
            "server. Generate MMS audio offline (scripts/import_synth_pairs_csv.py "
            "with the optional TTS toolchain) and upload the WAVs instead."
        )

    raise ValueError(f'{side_label} TTS engine "{engine}" is not supported.')


def generate_source_wav_for_synth(text: str, pair: dict) -> tuple[bytes, str, str, str | None]:
    """Source-side WAV, voice label, engine, optional gender."""
    cfg = resolve_pair_tts(pair)
    return _synthesize_side(text, cfg.source, "Source")


def generate_target_wav_for_synth(text: str, pair: dict) -> tuple[bytes, str, str, str | None]:
    """Target-side WAV, voice label, engine, optional gender."""
    cfg = resolve_pair_tts(pair)
    return _synthesize_side(text, cfg.target, "Target")


def generate_english_wav_for_synth(text: str, pair: dict | None = None) -> tuple[bytes, str, str, str | None]:
    """Backward-compatible English/source synthesis with default English pair shape."""
    if pair is None:
        pair = {"audio_folder_source": "english", "audio_folder_target": "swahili"}
    return generate_source_wav_for_synth(text, pair)


def generate_swahili_wav_for_synth(text: str, pair: dict | None = None) -> tuple[bytes, str, str, str | None]:
    """Backward-compatible target synthesis; uses pair target config or Swahili defaults."""
    if pair is None:
        pair = {"audio_folder_source": "english", "audio_folder_target": "swahili"}
    return generate_target_wav_for_synth(text, pair)


# Re-export for callers that imported voice lists
__all__ = [
    "ENGLISH_VOICES",
    "SWAHILI_VOICES",
    "EDGE_VOICE",
    "MmsTtsUnsupportedError",
    "generate_english_wav_for_synth",
    "generate_swahili_wav_for_synth",
    "generate_source_wav_for_synth",
    "generate_target_wav_for_synth",
    "maybe_augment_wav",
    "pick_edge_voice",
    "resolve_pair_tts",
]
