"""Resolve source/target TTS engines and voices from a language_pairs document."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# (edge_voice_short_name, gender label)
ENGLISH_VOICES: Final[list[tuple[str, str]]] = [
    ("en-US-JennyNeural", "female"),
    ("en-US-GuyNeural", "male"),
    ("en-US-AriaNeural", "female"),
    ("en-US-DavisNeural", "male"),
    ("en-GB-SoniaNeural", "female"),
    ("en-GB-RyanNeural", "male"),
    ("en-GH-AmaNeural", "female"),
    ("en-GH-KofiNeural", "male"),
]

SWAHILI_VOICES: Final[list[tuple[str, str]]] = [
    ("sw-KE-RafikiNeural", "male"),
    ("sw-KE-ZuriNeural", "female"),
    ("sw-TZ-DaudiNeural", "male"),
    ("sw-TZ-RehemaNeural", "female"),
]

# audio_folder_target (lower) -> default HuggingFace MMS model id
TARGET_MMS_PRESETS: Final[dict[str, str]] = {
    "twi": "waxal-benchmarking/mms-tts-twi",
    "akan": "facebook/mms-tts-aka",
    "aka": "facebook/mms-tts-aka",
    "fante": "facebook/mms-tts-aka",
    "fat": "facebook/mms-tts-aka",
    "ga": "facebook/mms-tts-gaa",
    "ewe": "facebook/mms-tts-ewe",
    "kusaal": "facebook/mms-tts-kus",
    "kus": "facebook/mms-tts-kus",
}


@dataclass(frozen=True)
class TtsSideConfig:
    engine: str  # "edge" | "mms"
    edge_voices: tuple[tuple[str, str], ...]  # (voice, gender)
    mms_model: str | None = None


@dataclass(frozen=True)
class TtsPairConfig:
    source: TtsSideConfig
    target: TtsSideConfig


def _norm_folder(name: str) -> str:
    return (name or "").strip().lower()


def _voice_list_from_doc(raw: object) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    return [str(v) for v in raw if str(v).strip()]


def _voices_from_strings(names: list[str]) -> tuple[tuple[str, str], ...]:
    return tuple(((n.strip(), "unknown") for n in names if n.strip()))


def _merge_edge_voices(
    explicit: list[str] | None,
    preset: list[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    if explicit:
        voices = _voices_from_strings(explicit)
        if voices:
            return voices
    return tuple(preset)


def _resolve_source_side(pair: dict, src_folder: str) -> TtsSideConfig:
    src_engine = (pair.get("tts_source_engine") or "").strip().lower()
    src_voices_list = _voice_list_from_doc(pair.get("tts_source_voices"))
    src_mms = (pair.get("tts_source_mms_model") or "").strip() or None

    if src_engine == "mms":
        if not src_mms:
            raise ValueError(
                f"Source language '{src_folder}' uses MMS but tts_source_mms_model is not set on the pair."
            )
        return TtsSideConfig(engine="mms", edge_voices=tuple(), mms_model=src_mms)

    # English source: always Edge with M/F pool (explicit pair config can override voices)
    if src_folder == "english" or not src_engine:
        voices = _merge_edge_voices(src_voices_list, ENGLISH_VOICES)
        return TtsSideConfig(engine="edge", edge_voices=voices)

    if src_engine == "edge":
        if src_voices_list:
            voices = _voices_from_strings(src_voices_list)
            if not voices:
                raise ValueError("tts_source_voices is empty for edge source engine.")
            return TtsSideConfig(engine="edge", edge_voices=voices)
        voices = _merge_edge_voices(None, ENGLISH_VOICES)
        return TtsSideConfig(engine="edge", edge_voices=voices)

    raise ValueError(
        f"Source TTS engine '{src_engine}' is not supported. Use 'edge' or 'mms'."
    )


def _resolve_target_side(pair: dict, tgt_folder: str) -> TtsSideConfig:
    tgt_engine = (pair.get("tts_target_engine") or "").strip().lower()
    tgt_voices_list = _voice_list_from_doc(pair.get("tts_target_voices"))
    explicit_mms = (pair.get("tts_target_mms_model") or "").strip() or None

    if tgt_engine == "mms" or (not tgt_engine and explicit_mms):
        model = explicit_mms or TARGET_MMS_PRESETS.get(tgt_folder)
        if not model and tgt_folder == "twi":
            model = "facebook/mms-tts-aka"
        if not model:
            raise ValueError(
                f"Target language '{tgt_folder}' requires MMS but no tts_target_mms_model is set "
                "and no folder preset exists. Configure the language pair TTS settings."
            )
        return TtsSideConfig(engine="mms", edge_voices=tuple(), mms_model=model)

    if tgt_voices_list:
        voices = _voices_from_strings(tgt_voices_list)
        if voices:
            return TtsSideConfig(engine="edge", edge_voices=voices)

    if tgt_engine == "edge" or tgt_folder == "swahili":
        return TtsSideConfig(engine="edge", edge_voices=tuple(SWAHILI_VOICES))

    preset_mms = TARGET_MMS_PRESETS.get(tgt_folder)
    if preset_mms:
        return TtsSideConfig(engine="mms", edge_voices=tuple(), mms_model=preset_mms)

    raise ValueError(
        f"Target TTS is not configured for folder '{tgt_folder}'. "
        "Set tts_target_engine and tts_target_voices (Edge) or tts_target_mms_model (MMS) on the pair."
    )


def resolve_pair_tts(pair: dict) -> TtsPairConfig:
    """Build TTS config; explicit pair fields win over folder presets."""
    src_folder = _norm_folder(pair.get("audio_folder_source", "english"))
    tgt_folder = _norm_folder(pair.get("audio_folder_target", "swahili"))
    return TtsPairConfig(
        source=_resolve_source_side(pair, src_folder),
        target=_resolve_target_side(pair, tgt_folder),
    )
