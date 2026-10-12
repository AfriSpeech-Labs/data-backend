"""Insert speech_parallel documents.

Document keys text_english / text_swahili / length_* / audio_* are legacy names
(Swahili-only origin). They now hold source and TARGET-language data for any pair;
kept as-is to avoid a DB migration (see Option B)."""
import io

from bson import ObjectId

from app.config import settings

from app.db import get_database, get_gridfs
from app.services.audio_duration import get_wav_duration_seconds

COLLECTION = "speech_parallel"


def _serialize_id(oid: ObjectId) -> str:
    return str(oid)


async def insert_item_from_english_wav(
    wav_bytes: bytes,
    text_english: str,
    text_swahili: str | None = None,
    wav_filename: str = "english.wav",
    pair_id: str | None = None,
) -> str:
    """Upload English WAV to R2 (if configured) or GridFS; insert one pending doc."""
    from app.r2_storage import put_object_bytes, r2_enabled
    from app.services.language_pairs import pair_audio_keys, resolve_pair_id

    try:
        length_english = get_wav_duration_seconds(wav_bytes)
    except Exception:
        length_english = 0.0
    pair_oid, pair = await resolve_pair_id(pair_id)
    te = (text_english or "").strip() or None
    ts = None
    if text_swahili is not None:
        ts = text_swahili.strip() or None

    db = get_database()
    oid = ObjectId()
    id_str = str(oid)
    doc: dict = {
        "_id": oid,
        "pair_id": pair_oid,
        "audio_swahili": None,
        "length_english": length_english,
        "length_swahili": None,
        "status": "pending",
        "text_english": te,
        "text_swahili": ts,
    }

    if settings.require_r2 and not r2_enabled():
        raise RuntimeError(
            "R2 is required in this environment. Set R2_ENDPOINT_URL, R2_BUCKET_NAME, "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (or REQUIRE_R2=false for local dev)."
        )

    if r2_enabled() or settings.require_r2:
        key_src, _ = pair_audio_keys(pair, id_str, "source")
        await put_object_bytes(key_src, wav_bytes)
        doc["r2_key_source"] = key_src
    else:
        gridfs = get_gridfs()
        file_id = await gridfs.upload_from_stream(
            wav_filename,
            io.BytesIO(wav_bytes),
            metadata={"language": "english"},
        )
        doc["audio_english"] = file_id

    await db[COLLECTION].insert_one(doc)
    return _serialize_id(oid)


async def insert_speech_prompt(
    transcript: str,
    translation: str | None = None,
    pair_oid: ObjectId | None = None,
    csv_row: int | None = None,
) -> str:
    """Create one audio-less, pending speech prompt item.

    Transcript-first flow: the admin seeds the target-language sentence
    (``transcript``, stored in legacy key ``text_swahili``) that the contributor
    will read aloud, plus an optional source/English translation (stored in
    ``text_english`` for TTST). No audio is attached — the contributor's
    recording is added later via the submit endpoint.

    ``pair_oid`` must be a resolved language-pair ObjectId.
    """
    t = (transcript or "").strip()
    if not t:
        raise ValueError("transcript is required")
    tr = (translation or "").strip() or None

    db = get_database()
    oid = ObjectId()
    doc: dict = {
        "_id": oid,
        "pair_id": pair_oid,
        "status": "pending",
        # Legacy keys kept for compatibility (Option B): text_swahili = target
        # transcript (ASR label / read-aloud prompt), text_english = source.
        "text_swahili": t,
        "text_english": tr,
        "length_english": None,
        "length_swahili": None,
        "audio_english": None,
        "audio_swahili": None,
        "source": "speech_prompt_csv",
        "csv_row": csv_row,
    }
    await db[COLLECTION].insert_one(doc)
    return _serialize_id(oid)
