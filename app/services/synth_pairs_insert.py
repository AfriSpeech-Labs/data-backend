"""Insert parallel_synth_pairs documents (dual TTS) into isolated collection + GridFS."""
import io
from datetime import datetime, timezone

from bson import ObjectId

from app.config import settings
from app.db import get_database, get_gridfs_synth
from app.services.audio_duration import get_wav_duration_seconds


def _serialize_id(oid: ObjectId) -> str:
    return str(oid)


def _collection():
    return get_database()[settings.synth_pairs_collection]


async def insert_synth_pair(
    wav_english: bytes,
    wav_swahili: bytes,
    text_english: str,
    text_swahili: str,
    tts_english_voice: str,
    tts_swahili_voice: str,
    tts_swahili_engine: str,
    gender_sw: str | None,
    csv_row: int | None = None,
    language_pair_id: ObjectId | None = None,
) -> str:
    gridfs = get_gridfs_synth()
    try:
        len_en = get_wav_duration_seconds(wav_english)
    except Exception:
        len_en = 0.0
    try:
        len_sw = get_wav_duration_seconds(wav_swahili)
    except Exception:
        len_sw = 0.0

    en_id = await gridfs.upload_from_stream(
        "english.wav",
        io.BytesIO(wav_english),
        metadata={"language": "english", "module": "synth_pairs"},
    )
    sw_id = await gridfs.upload_from_stream(
        "swahili.wav",
        io.BytesIO(wav_swahili),
        metadata={"language": "swahili", "module": "synth_pairs"},
    )

    now = datetime.now(timezone.utc)
    doc = {
        "audio_english": en_id,
        "audio_swahili": sw_id,
        "length_english": len_en,
        "length_swahili": len_sw,
        "text_english": text_english.strip(),
        "text_swahili": text_swahili.strip(),
        "tts_english_voice": tts_english_voice,
        "tts_swahili_voice": tts_swahili_voice,
        "tts_swahili_engine": tts_swahili_engine,
        "gender_sw": gender_sw,
        "validation_status": "pending",
        "validated_at": None,
        "validated_by": None,
        "validation_note": None,
        "created_at": now,
        "source": "bulk_csv",
        "csv_row": csv_row,
    }
    if language_pair_id is not None:
        doc["language_pair_id"] = language_pair_id
    result = await _collection().insert_one(doc)
    return _serialize_id(result.inserted_id)
