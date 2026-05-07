"""Audio API routes: bulk upload, list, stream, submit Swahili."""
import asyncio
import csv
import io
import os
from bson import ObjectId
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Depends

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
    BulkCsvErrorItem,
)
from app.services.audio_duration import get_wav_duration_seconds
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
from app.services.tts_english import generate_wav_from_text
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
    """Create one item from English text: backend TTS generates WAV, stores text + audio. Requires pyttsx3."""
    text_english = text_english.strip()
    if not text_english:
        raise HTTPException(status_code=400, detail="text_english is required")
    try:
        wav_data = await asyncio.to_thread(generate_wav_from_text, text_english)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    new_id = await insert_item_from_english_wav(
        wav_data,
        text_english=text_english,
        text_swahili=None,
        wav_filename="english.wav",
        pair_id=pair_id,
    )
    return {"id": new_id}


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
    """Bulk import parallel English/Swahili text: TTS per English line, store text_english, text_swahili, audio_english.

    CSV must have a header row with columns english_column and swahili_column (defaults: english, swahili).
    Max max_rows data rows per request (default 50, cap 200); split large files into multiple uploads.

    **Swagger / browser note:** Each row runs TTS synchronously, so large max_rows (e.g. 150) can take many minutes.
    Browsers and proxies often close the connection before the response → OpenAPI "Try it out" may show **Failed to fetch**
    even though the server is still working. Prefer **small max_rows** (5–15) when testing in /docs, or use **curl**
    with a long `--max-time`, or **scripts/import_parallel_csv.py** (default 900s timeout per chunk).
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files allowed")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid UTF-8: {e}") from e
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV has no header row")
    headers_lower = {h.strip().lower(): h for h in reader.fieldnames if h}
    en_key = english_column.strip().lower()
    sw_key = swahili_column.strip().lower()
    if en_key not in headers_lower:
        raise HTTPException(
            status_code=400,
            detail=f"Missing column '{english_column}'. Found: {list(reader.fieldnames)}",
        )
    if sw_key not in headers_lower:
        raise HTTPException(
            status_code=400,
            detail=f"Missing column '{swahili_column}'. Found: {list(reader.fieldnames)}",
        )
    col_en = headers_lower[en_key]
    col_sw = headers_lower[sw_key]
    rows: list[tuple[int, str, str]] = []
    for i, row in enumerate(reader, start=1):
        en = (row.get(col_en) or "").strip()
        sw = (row.get(col_sw) or "").strip()
        if not en and not sw:
            continue
        rows.append((i, en, sw))
    if len(rows) > max_rows:
        raise HTTPException(
            status_code=400,
            detail=(
                f"CSV has {len(rows)} non-empty data rows; max per request is {max_rows}. "
                f"Split into chunks of ≤{max_rows} rows (repeat header in each chunk) and POST each."
            ),
        )
    created_ids: list[str] = []
    errors: list[BulkCsvErrorItem] = []
    for row_index, en, sw in rows:
        if not en:
            errors.append(BulkCsvErrorItem(row=row_index, detail="Empty English text"))
            continue
        sw_val = sw if sw else None
        try:
            wav_data = await asyncio.to_thread(generate_wav_from_text, en)
        except ValueError as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=str(e)))
            continue
        except RuntimeError as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=str(e)))
            continue
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"TTS error: {e!s}"))
            continue
        try:
            new_id = await insert_item_from_english_wav(
                wav_data,
                text_english=en,
                text_swahili=sw_val,
                wav_filename="english.wav",
                pair_id=pair_id,
            )
            created_ids.append(new_id)
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"DB/GridFS: {e!s}"))
    processed = len(rows)
    failed = len(errors)
    return BulkCsvResponse(
        created_ids=created_ids,
        errors=errors,
        processed=processed,
        failed=failed,
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
    """Upload Swahili bytes to R2 or GridFS and update doc. Preserves existing text_swahili (e.g. from CSV)."""
    from app.services.speech_parallel_read import _pair_for_doc

    try:
        length_swahili = get_wav_duration_seconds(file_data)
    except Exception:
        length_swahili = 0.0
    db = get_database()
    pair = await _pair_for_doc(doc)
    oid_str = str(doc["_id"])
    payload: dict = {
        "length_swahili": length_swahili,
        "status": "submitted",
    }
    if contributor_user_id:
        try:
            payload["submitted_by_user_id"] = ObjectId(contributor_user_id)
        except Exception:
            pass

    is_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
    if is_railway and not r2_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Audio storage is not configured (R2 required on Railway). "
                "Set R2_ENDPOINT_URL, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY."
            ),
        )

    if (r2_enabled() or is_railway) and pair:
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
