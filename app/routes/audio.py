"""Audio API routes: bulk upload, list, stream, submit target-language recording.

Note: document keys like text_swahili / audio_swahili / length_swahili are
legacy names from the Swahili-only origin; they now hold the pair's TARGET
language data regardless of language (kept as-is to avoid a data migration)."""
import io
from bson import ObjectId
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Depends

from app.config import settings

from app.auth_contributor import (
    assert_contributor_doc_pair,
    optional_contributor,
    ContributorIdentity,
)
from app.db import get_database, get_gridfs
from app.models import (
    PaginatedListResponse,
    BulkUploadResponse,
    AudioStatsResponse,
    AudioDurationStatsResponse,
    BulkCsvResponse,
)
from app.services.speech_insert import insert_item_from_english_wav
from app.services.speech_parallel_read import (
    COLLECTION,
    build_export_response,
    fetch_audio_stats,
    fetch_audio_stats_durations,
    get_speech_doc,
    list_audio_paginated,
    stream_english_response,
    stream_swahili_response,
)
from app.services.language_pairs import pair_audio_keys
from app.r2_storage import r2_enabled, put_object_bytes

router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.get("/stats/durations", response_model=AudioDurationStatsResponse)
async def audio_stats_durations(
    pair_id: str | None = Query(None, description="Filter by language pair ObjectId"),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Aggregate WAV durations from per-document `length_english` and `length_swahili`.

    Sums English seconds across every document, plus submitted-only and pending-only
    English sums. Sums Swahili seconds only where `status` is `submitted`.
    Empty collection returns zeros.
    """
    eff = contrib.pair_id if contrib else pair_id
    return await fetch_audio_stats_durations(eff)


@router.get("/stats", response_model=AudioStatsResponse)
async def audio_stats(
    pair_id: str | None = Query(None, description="Filter by language pair ObjectId"),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Return total, submitted, and pending counts for progress."""
    eff = contrib.pair_id if contrib else pair_id
    return await fetch_audio_stats(eff)


@router.post("/english/bulk", response_model=BulkUploadResponse)
async def bulk_upload_english(
    files: list[UploadFile] = File(...),
    pair_id: str | None = Query(None),
):
    """Upload multiple English .wav files; store in R2 or GridFS, compute length, create docs."""
    created_ids = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".wav"):
            continue
        data = await f.read()
        if not data:
            continue
        new_id = await insert_item_from_english_wav(
            data,
            text_english="",
            text_swahili=None,
            wav_filename=f.filename or "audio.wav",
            pair_id=pair_id,
        )
        created_ids.append(new_id)
    return BulkUploadResponse(created_ids=created_ids)


@router.post("/english/with-text", response_model=dict)
async def create_english_with_text(
    file: UploadFile = File(...),
    text_english: str = Form(""),
    text_swahili: str = Form(""),
    pair_id: str | None = Query(None),
):
    """Create one item: upload English WAV + optional source texts. Used by script and client upload."""
    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Only .wav files allowed")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    ts = text_swahili.strip() or None
    new_id = await insert_item_from_english_wav(
        data,
        text_english=text_english,
        text_swahili=ts,
        wav_filename=file.filename or "audio.wav",
        pair_id=pair_id,
    )
    return {"id": new_id}


@router.post("/english/speak", response_model=dict)
async def create_english_speak(
    text_english: str = Form(..., min_length=1),
    pair_id: str | None = Query(None),
):
    """Server-side TTS is offline-only; returns 501.

    Edge TTS needs ffmpeg/pydub, unavailable on the serverless host. Generate the
    WAV offline (or use /english/with-text to upload a client-generated WAV).
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Server-side TTS is disabled on this deployment. Upload a "
            "pre-generated WAV via POST /api/audio/english/with-text, or run the "
            "offline import scripts."
        ),
    )


BULK_CSV_DEFAULT_MAX = 50
BULK_CSV_HARD_CAP = 200


@router.post("/parallel/bulk-csv", response_model=BulkCsvResponse)
async def bulk_csv_parallel(
    file: UploadFile = File(...),
    max_rows: int = Query(BULK_CSV_DEFAULT_MAX, ge=1, le=BULK_CSV_HARD_CAP),
    english_column: str = Query("english"),
    swahili_column: str = Query("swahili"),
    pair_id: str | None = Query(None),
):
    """Server-side per-row TTS is offline-only; returns 501.

    This path synthesized a WAV per English line using edge-tts + ffmpeg, which
    are not available on the serverless host. Generate audio offline with
    scripts/import_parallel_csv.py and upload via /english/with-text (or
    /english/bulk), which store pre-generated WAVs.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Server-side TTS synthesis is disabled on this deployment. Use the "
            "offline import scripts (scripts/import_parallel_csv.py) to generate "
            "WAVs locally, then upload them."
        ),
    )


@router.get("", response_model=PaginatedListResponse)
async def list_audio(
    page: int = 1,
    limit: int = 20,
    pair_id: str | None = Query(None),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Paginated list of speech items."""
    eff_pair = contrib.pair_id if contrib else pair_id
    return await list_audio_paginated(page, limit, eff_pair)


@router.get("/export")
async def export_dataset(
    limit: int = 500,
    submitted_only: bool = False,
    pair_id: str | None = Query(None),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Export dataset as ZIP: English/Swahili WAVs + metadata.json. Use ?submitted_only=true for only submitted items. Optional ?limit= (max 500)."""
    eff = contrib.pair_id if contrib else pair_id
    return await build_export_response(limit, submitted_only, pair_id=eff)


@router.get("/{id}/english")
async def stream_english(
    id: str,
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Stream English .wav for playback."""
    doc = await get_speech_doc(id)
    assert_contributor_doc_pair(doc, contrib)
    return await stream_english_response(id, doc=doc)


@router.get("/{id}/swahili")
async def stream_swahili(
    id: str,
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Stream submitted Swahili .wav for playback."""
    doc = await get_speech_doc(id)
    assert_contributor_doc_pair(doc, contrib)
    return await stream_swahili_response(id, doc=doc)


async def _save_swahili_for_doc(
    doc: dict,
    file_data: bytes,
    filename: str,
    form_text_swahili: str | None = None,
    contributor_user_id: str | None = None,
) -> dict:
    """Store the target-language recording to R2/GridFS and mark the item submitted.

    ``text_swahili`` (legacy key) holds the target transcript; it is preserved when
    already set (e.g. from a speech-prompt import) and only back-filled from the
    submitted form text when the item had no transcript."""
    from app.services.speech_parallel_read import _pair_for_doc
    from app.services.audio_quality import assert_quality, expected_seconds_for_text

    # Quality guard: enforce the four cheap checks before storing. Length is
    # keyed off the target-language transcript (reference text if present, else
    # the text submitted with this recording); skipped when there is no text.
    existing_target = doc.get("text_swahili")
    target_text = (
        str(existing_target).strip()
        if existing_target is not None and str(existing_target).strip()
        else (form_text_swahili or "").strip()
    )
    expected_seconds = expected_seconds_for_text(target_text)
    metrics = assert_quality(file_data, expected_seconds)

    db = get_database()
    pair = await _pair_for_doc(doc)
    oid_str = str(doc["_id"])
    payload: dict = {
        "length_swahili": metrics["duration_s"],
        "status": "submitted",
        # ASR/TTS/TTST metadata: speaker + measured audio quality.
        "sample_rate": metrics["sample_rate"],
        "duration": metrics["duration_s"],
        "peak": metrics["peak_ratio"],
        "clipped_ratio": metrics["clipped_ratio"],
    }
    if contributor_user_id:
        try:
            payload["submitted_by_user_id"] = ObjectId(contributor_user_id)
        except Exception:
            pass

    if settings.require_r2 and not r2_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Audio storage is not configured (R2 required in this environment). "
                "Set R2_ENDPOINT_URL, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY."
            ),
        )

    if (r2_enabled() or settings.require_r2) and pair:
        key_tgt, _ = pair_audio_keys(pair, oid_str, "target")
        await put_object_bytes(key_tgt, file_data)
        payload["r2_key_target"] = key_tgt
        payload["audio_swahili"] = None
    else:
        gridfs = get_gridfs()
        file_id = await gridfs.upload_from_stream(
            filename or "swahili.wav",
            io.BytesIO(file_data),
            metadata={"language": "swahili"},
        )
        payload["audio_swahili"] = file_id
        payload["r2_key_target"] = None

    existing = doc.get("text_swahili")
    has_reference = existing is not None and str(existing).strip() != ""
    if not has_reference and form_text_swahili and form_text_swahili.strip():
        payload["text_swahili"] = form_text_swahili.strip()
    await db[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": payload})
    return {"status": "submitted", "id": str(doc["_id"])}


@router.put("/{id}/swahili")
async def replace_swahili(
    id: str,
    file: UploadFile = File(...),
    text_swahili: str = Form(""),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Replace existing Swahili .wav for this item (full overwrite). Use for resubmit."""
    doc = await get_speech_doc(id)
    assert_contributor_doc_pair(doc, contrib)
    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Only .wav files allowed")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    return await _save_swahili_for_doc(
        doc,
        data,
        file.filename or "swahili.wav",
        form_text_swahili=text_swahili.strip() or None,
        contributor_user_id=contrib.sub if contrib else None,
    )


@router.post("/{id}/swahili")
async def submit_swahili(
    id: str,
    file: UploadFile = File(...),
    text_swahili: str = Form(""),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    """Upload Swahili .wav for this item (first-time submit). Returns 400 if already submitted."""
    doc = await get_speech_doc(id)
    assert_contributor_doc_pair(doc, contrib)
    if doc.get("status") == "submitted":
        raise HTTPException(status_code=400, detail="Already submitted. Use PUT /api/audio/{id}/swahili to replace.")
    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Only .wav files allowed")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    return await _save_swahili_for_doc(
        doc,
        data,
        file.filename or "swahili.wav",
        form_text_swahili=text_swahili.strip() or None,
        contributor_user_id=contrib.sub if contrib else None,
    )
