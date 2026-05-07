"""Paths inside dataset export ZIPs: data/audio/{english|swahili}/, data/csv/."""

DATA = "data"
AUDIO_ENGLISH = f"{DATA}/audio/english"
AUDIO_SWAHILI = f"{DATA}/audio/swahili"
CSV_DIR = f"{DATA}/csv"
METADATA_JSON = f"{CSV_DIR}/metadata.json"
METADATA_CSV = f"{CSV_DIR}/metadata.csv"


def zip_path_english_wav(id_str: str) -> str:
    return f"{AUDIO_ENGLISH}/{id_str}_english.wav"


def zip_path_swahili_wav(id_str: str) -> str:
    return f"{AUDIO_SWAHILI}/{id_str}_swahili.wav"


def zip_paths_for_pair(pair: dict, id_str: str) -> tuple[str, str]:
    """ZIP-internal paths using pair folder + suffix (inside ``data/audio/``)."""
    src_folder = pair.get("audio_folder_source", "english")
    tgt_folder = pair.get("audio_folder_target", "swahili")
    sfx_s = pair.get("file_suffix_source", "en")
    sfx_t = pair.get("file_suffix_target", "sw")
    src = f"{DATA}/audio/{src_folder}/{id_str}_{sfx_s}.wav"
    tgt = f"{DATA}/audio/{tgt_folder}/{id_str}_{sfx_t}.wav"
    return src, tgt
