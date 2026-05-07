"""Insert parallel_synth_pairs documents (dual TTS) into isolated collection + GridFS."""
import io
import os
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
    from app.r2_storage import put_object_bytes, r2_enabled
    from app.services.language_pairs import get_pair_by_id, resolve_default_pair, synth_audio_keys

    is_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
    if is_railway and not r2_enabled():
        raise RuntimeError(
            "R2 is required on Railway. Set R2_ENDPOINT_URL, R2_BUCKET_NAME, "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY."
        )

    gridfs = get_gridfs_synth()
    try:
        len_en = get_wav_duration_seconds(wav_english)
    except Exception:
        len_en = 0.0
    try:
        len_sw = get_wav_duration_seconds(wav_swahili)
    except Exception:
        len_sw = 0.0

    # Resolve a language pair for stable R2 key naming.
    if language_pair_id is not None:
        pair = await get_pair_by_id(language_pair_id)
        if not pair:
            pair = await resolve_default_pair()
    else:
        pair = await resolve_default_pair()

    en_id = None
    sw_id = None
    r2_src = None
    r2_tgt = None
    if r2_enabled() or is_railway:
        # Store synth audio in R2 (required on Railway).
        oid_for_key = ObjectId()
        id_str_for_key = str(oid_for_key)
        r2_src, _ = synth_audio_keys(pair, id_str_for_key, "source")
        r2_tgt, _ = synth_audio_keys(pair, id_str_for_key, "target")
        await put_object_bytes(r2_src, wav_english)
        await put_object_bytes(r2_tgt, wav_swahili)
        synth_id = oid_for_key
    else:
        # Dev fallback: keep supporting GridFS until migration is complete.
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
        synth_id = ObjectId()

    now = datetime.now(timezone.utc)
    doc = {
        "_id": synth_id,
        "audio_english": en_id,
        "audio_swahili": sw_id,
        "r2_key_source": r2_src,
        "r2_key_target": r2_tgt,
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
    await _collection().insert_one(doc)
    return _serialize_id(synth_id)
