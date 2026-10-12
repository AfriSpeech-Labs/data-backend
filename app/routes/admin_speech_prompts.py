"""Admin API: transcript-first speech prompts (audio-less seeding of speech_parallel).

Lets an admin upload target-language sentences (the read-aloud transcript) plus an
optional source/English translation. Each row becomes a ``pending`` speech_parallel
item with no audio; contributors later record against it.

Uses legacy document keys (Option B — no DB migration): ``text_swahili`` holds the
target transcript, ``text_english`` the optional source/translation.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.auth_admin import require_admin
from app.db import get_database
from app.models import BulkCsvErrorItem, BulkCsvResponse
from app.services.language_pairs import get_pair_by_id
from app.services.speech_insert import insert_speech_prompt
from app.services.speech_parallel_read import fetch_audio_stats

router = APIRouter(
    prefix="/api/admin/speech-prompts",
    tags=["admin-speech-prompts"],
    dependencies=[Depends(require_admin)],
)

COLLECTION = "speech_parallel"
BULK_DEFAULT_MAX = 50
BULK_HARD_CAP = 200


def _coll():
    return get_database()[COLLECTION]


async def _require_pair(pair_id: str) -> tuple[ObjectId, dict]:
    pid = (pair_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="pair_id is required")
    try:
        oid = ObjectId(pid)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pair_id") from None
    pair = await get_pair_by_id(oid)
    if not pair:
        raise HTTPException(status_code=404, detail="Language pair not found")
    return oid, pair


class SpeechPromptRow(BaseModel):
    id: str
    transcript: str | None = None  # target-language text (legacy key text_swahili)
    translation: str | None = None  # optional source/English (legacy key text_english)
    status: str
    has_audio: bool
    created_at: datetime | None = None


class SpeechPromptListResponse(BaseModel):
    items: list[SpeechPromptRow]
    total: int
    page: int
    limit: int


@router.post("/bulk-csv", response_model=BulkCsvResponse)
async def speech_prompts_bulk_csv(
    file: UploadFile = File(...),
    pair_id: str = Query(..., description="language_pairs ObjectId"),
    max_rows: int = Query(BULK_DEFAULT_MAX, ge=1, le=BULK_HARD_CAP),
    transcript_column: str | None = Query(
        None,
        description="CSV column for the target-language transcript (required). Defaults to the pair's target folder name.",
    ),
    translation_column: str | None = Query(
        None,
        description="CSV column for the optional source/English translation. Defaults to the pair's source folder name.",
    ),
):
    """Seed pending, audio-less speech prompts from a two-column sheet.

    Transcript (target language) is required per row; translation (source/English)
    is optional and may be blank.
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

    pair_oid, pair = await _require_pair(pair_id)
    pair_target = (pair.get("audio_folder_target", "target") or "target").strip().lower()
    pair_source = (pair.get("audio_folder_source", "source") or "source").strip().lower()
    tcol = (transcript_column or "").strip() or pair_target
    scol = (translation_column or "").strip() or pair_source

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV has no header row")
    headers_lower = {h.strip().lower(): h for h in reader.fieldnames if h}
    t_key = tcol.lower()
    if t_key not in headers_lower:
        raise HTTPException(
            status_code=400,
            detail=f"Missing transcript column '{tcol}'. Found: {list(reader.fieldnames)}",
        )
    col_t = headers_lower[t_key]
    # Translation column is optional: if absent, every row has no translation.
    s_key = scol.lower()
    col_s = headers_lower.get(s_key)

    rows: list[tuple[int, str, str]] = []
    for i, row in enumerate(reader, start=1):
        transcript = (row.get(col_t) or "").strip()
        translation = (row.get(col_s) or "").strip() if col_s else ""
        if not transcript and not translation:
            continue
        rows.append((i, transcript, translation))

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
    for row_index, transcript, translation in rows:
        if not transcript:
            errors.append(
                BulkCsvErrorItem(row=row_index, detail="Empty transcript (target text is required)")
            )
            continue
        try:
            new_id = await insert_speech_prompt(
                transcript=transcript,
                translation=translation or None,
                pair_oid=pair_oid,
                csv_row=row_index,
            )
            created_ids.append(new_id)
        except ValueError as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=str(e)))
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"DB: {e!s}"))

    return BulkCsvResponse(
        created_ids=created_ids,
        errors=errors,
        processed=len(rows),
        failed=len(errors),
    )


@router.get("/stats")
async def speech_prompts_stats(
    pair_id: str = Query(..., description="language_pairs ObjectId"),
):
    """Total / submitted / pending speech items for this pair."""
    pair_oid, _ = await _require_pair(pair_id)
    return await fetch_audio_stats(str(pair_oid))


@router.get("", response_model=SpeechPromptListResponse)
async def speech_prompts_list(
    pair_id: str = Query(..., description="language_pairs ObjectId"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="pending | submitted"),
    q: str | None = Query(None, description="Substring match on transcript or translation"),
):
    pair_oid, _ = await _require_pair(pair_id)
    flt: dict = {"pair_id": pair_oid}
    if status in ("pending", "submitted"):
        flt["status"] = status
    qq = (q or "").strip()
    if qq:
        import re

        rx = {"$regex": re.escape(qq), "$options": "i"}
        flt["$or"] = [{"text_swahili": rx}, {"text_english": rx}]

    coll = _coll()
    total = await coll.count_documents(flt)
    skip = (page - 1) * limit
    items: list[SpeechPromptRow] = []
    async for doc in coll.find(flt).sort("_id", 1).skip(skip).limit(limit):
        has_audio = bool(doc.get("r2_key_target") or doc.get("audio_swahili"))
        items.append(
            SpeechPromptRow(
                id=str(doc["_id"]),
                transcript=doc.get("text_swahili"),
                translation=doc.get("text_english"),
                status=doc.get("status", "pending"),
                has_audio=has_audio,
                created_at=doc.get("created_at"),
            )
        )
    return SpeechPromptListResponse(items=items, total=total, page=page, limit=limit)


@router.delete("")
async def speech_prompts_clear(
    pair_id: str = Query(..., description="language_pairs ObjectId"),
    pending_only: bool = Query(
        True,
        description="When true, only delete pending (un-recorded) prompts; protects submitted recordings.",
    ),
):
    """Delete speech prompts for a pair. Defaults to pending-only to protect recordings."""
    pair_oid, _ = await _require_pair(pair_id)
    flt: dict = {"pair_id": pair_oid}
    if pending_only:
        flt["status"] = "pending"
    res = await _coll().delete_many(flt)
    return {"deleted": res.deleted_count, "pending_only": pending_only}
