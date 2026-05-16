"""Admin-only API: parallel text corpus (e.g. English–Kom), CSV bulk import, list, stats."""
from __future__ import annotations

import csv
import re
import io
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.auth_admin import require_admin
from app.config import settings
from app.db import get_database
from app.models import (
    BulkCsvErrorItem,
    BulkCsvResponse,
    TextCorpusItemResponse,
    TextCorpusListResponse,
    TextCorpusPairCount,
    TextCorpusStatsResponse,
    TextTranslationStatsResponse,
)
from app.services.language_pairs import get_pair_by_id
from app.services.pair_delete import clear_text_corpus_for_pair_oid
from app.services.text_corpus_read import fetch_text_translation_stats

router = APIRouter(
    prefix="/api/admin/text-corpus",
    tags=["admin-text-corpus"],
    dependencies=[Depends(require_admin)],
)

BULK_DEFAULT_MAX = 50
BULK_HARD_CAP = 200


def _coll():
    return get_database()[settings.parallel_text_corpus_collection]


async def _require_pair_oid(pair_id: str) -> ObjectId:
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
    return oid


@router.post("/bulk-csv", response_model=BulkCsvResponse)
async def text_corpus_bulk_csv(
    file: UploadFile = File(...),
    pair_id: str = Query(..., description="language_pairs ObjectId"),
    max_rows: int = Query(BULK_DEFAULT_MAX, ge=1, le=BULK_HARD_CAP),
    source_column: str = Query("english"),
    target_column: str = Query("kom"),
):
    """Import parallel text lines (no audio). UTF-8 CSV with configurable column names."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files allowed")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid UTF-8: {e}") from e

    pair_oid = await _require_pair_oid(pair_id)
    pair = await get_pair_by_id(pair_oid)
    pair_source_lang = (
        (pair or {}).get("audio_folder_source", "english") or "english"
    ).strip().lower()
    pair_target_lang = (
        (pair or {}).get("audio_folder_target", "kom") or "kom"
    ).strip().lower()

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV has no header row")
    headers_lower = {h.strip().lower(): h for h in reader.fieldnames if h}
    src_key = source_column.strip().lower()
    tgt_key = target_column.strip().lower()
    if src_key not in headers_lower:
        raise HTTPException(
            status_code=400,
            detail=f"Missing column '{source_column}'. Found: {list(reader.fieldnames)}",
        )
    if tgt_key not in headers_lower:
        raise HTTPException(
            status_code=400,
            detail=f"Missing column '{target_column}'. Found: {list(reader.fieldnames)}",
        )
    col_src = headers_lower[src_key]
    col_tgt = headers_lower[tgt_key]

    rows: list[tuple[int, str, str]] = []
    for i, row in enumerate(reader, start=1):
        s = (row.get(col_src) or "").strip()
        t = (row.get(col_tgt) or "").strip()
        if not s and not t:
            continue
        rows.append((i, s, t))

    if len(rows) > max_rows:
        raise HTTPException(
            status_code=400,
            detail=(
                f"CSV has {len(rows)} non-empty data rows; max per request is {max_rows}. "
                f"Split into chunks of ≤{max_rows} rows (repeat header in each chunk) and POST each."
            ),
        )

    coll = _coll()
    created_ids: list[str] = []
    errors: list[BulkCsvErrorItem] = []
    now = datetime.now(timezone.utc)

    for row_index, src, tgt in rows:
        if not src:
            errors.append(BulkCsvErrorItem(row=row_index, detail="Empty source text"))
            continue
        if not tgt:
            errors.append(BulkCsvErrorItem(row=row_index, detail="Empty target text"))
            continue
        doc = {
            "language_pair_id": pair_oid,
            "text_source": src,
            "text_target": tgt,
            "source_language": pair_source_lang,
            "target_language": pair_target_lang,
            "created_at": now,
            "source": "bulk_csv",
            "csv_row": row_index,
        }
        try:
            res = await coll.insert_one(doc)
            created_ids.append(str(res.inserted_id))
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"DB: {e!s}"))

    return BulkCsvResponse(
        created_ids=created_ids,
        errors=errors,
        processed=len(rows),
        failed=len(errors),
    )


@router.post("/source-csv", response_model=BulkCsvResponse)
async def text_corpus_source_csv(
    file: UploadFile = File(...),
    pair_id: str = Query(..., description="language_pairs ObjectId"),
    max_rows: int = Query(BULK_DEFAULT_MAX, ge=1, le=BULK_HARD_CAP),
    source_column: str | None = Query(
        None,
        description="CSV column for pair target language (stored in text_target); defaults from language pair",
    ),
    source_language: str | None = Query(None),
    target_language: str | None = Query(None),
):
    """Import target-language-only rows as pending translation tasks (source language filled by contributors)."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files allowed")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid UTF-8: {e}") from e

    pair_oid = await _require_pair_oid(pair_id)
    pair = await get_pair_by_id(pair_oid)
    pair_source_lang = (
        (pair or {}).get("audio_folder_source", "english") or "english"
    ).strip().lower()
    pair_target_lang = (
        (pair or {}).get("audio_folder_target", "kom") or "kom"
    ).strip().lower()
    resolved_source_lang = (
        source_language.strip().lower() if source_language and source_language.strip() else pair_source_lang
    )
    resolved_target_lang = (
        target_language.strip().lower() if target_language and target_language.strip() else pair_target_lang
    )
    col_header = (
        source_column.strip()
        if source_column and source_column.strip()
        else pair_target_lang
    )

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV has no header row")
    headers_lower = {h.strip().lower(): h for h in reader.fieldnames if h}
    src_key = col_header.lower()
    if src_key not in headers_lower:
        raise HTTPException(
            status_code=400,
            detail=f"Missing column '{col_header}'. Found: {list(reader.fieldnames)}",
        )
    col_src = headers_lower[src_key]

    rows: list[tuple[int, str]] = []
    for i, row in enumerate(reader, start=1):
        s = (row.get(col_src) or "").strip()
        if not s:
            continue
        rows.append((i, s))

    if len(rows) > max_rows:
        raise HTTPException(
            status_code=400,
            detail=(
                f"CSV has {len(rows)} non-empty data rows; max per request is {max_rows}. "
                f"Split into chunks of ≤{max_rows} rows (repeat header in each chunk) and POST each."
            ),
        )

    coll = _coll()
    created_ids: list[str] = []
    errors: list[BulkCsvErrorItem] = []
    now = datetime.now(timezone.utc)

    for row_index, target_text in rows:
        doc = {
            "language_pair_id": pair_oid,
            "task_type": "translation",
            "status": "pending",
            "text_source": None,
            "text_target": target_text,
            "source_language": resolved_source_lang,
            "target_language": resolved_target_lang,
            "created_at": now,
            "submitted_at": None,
            "submitted_by_user_id": None,
            "source": "source_only_csv",
            "csv_row": row_index,
        }
        try:
            res = await coll.insert_one(doc)
            created_ids.append(str(res.inserted_id))
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"DB: {e!s}"))

    return BulkCsvResponse(
        created_ids=created_ids,
        errors=errors,
        processed=len(rows),
        failed=len(errors),
    )


@router.get("/translation-stats", response_model=TextTranslationStatsResponse)
async def text_translation_stats(
    pair_id: str = Query(..., description="Filter by language_pairs ObjectId"),
):
    await _require_pair_oid(pair_id)
    return await fetch_text_translation_stats(pair_id)


@router.get("", response_model=TextCorpusListResponse)
async def text_corpus_list(
    pair_id: str = Query(..., description="Filter by language_pairs ObjectId"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="Optional case-insensitive substring on source or target"),
    task_type: str | None = Query(None, description="Optional task type filter, e.g. translation"),
    corpus_only: bool = Query(
        False, description="If true, exclude translation tasks (pre-completed pairs only)"
    ),
    status: str | None = Query(None, description="Optional status filter, e.g. pending or submitted"),
):
    pair_oid = await _require_pair_oid(pair_id)
    coll = _coll()
    flt: dict = {"language_pair_id": pair_oid}
    if corpus_only:
        flt["task_type"] = {"$ne": "translation"}
    elif task_type and task_type.strip():
        flt["task_type"] = task_type.strip()
    if status and status.strip():
        flt["status"] = status.strip()
    if q and q.strip():
        pat = {"$regex": re.escape(q.strip()), "$options": "i"}
        flt["$or"] = [{"text_source": pat}, {"text_target": pat}]

    total = await coll.count_documents(flt)
    skip = (page - 1) * limit
    cursor = coll.find(flt).sort("_id", -1).skip(skip).limit(limit)
    items: list[TextCorpusItemResponse] = []
    async for doc in cursor:
        items.append(
            TextCorpusItemResponse(
                id=str(doc["_id"]),
                language_pair_id=str(doc["language_pair_id"]),
                text_source=str(doc.get("text_source") or ""),
                text_target=doc.get("text_target"),
                created_at=doc.get("created_at") or datetime.now(timezone.utc),
                source=doc.get("source"),
                task_type=doc.get("task_type"),
                status=doc.get("status"),
                source_language=doc.get("source_language"),
                target_language=doc.get("target_language"),
                submitted_at=doc.get("submitted_at"),
            )
        )
    return TextCorpusListResponse(items=items, total=total, page=page, limit=limit)


@router.get("/stats", response_model=TextCorpusStatsResponse)
async def text_corpus_stats(
    pair_id: str | None = Query(None, description="If set, only total for this pair"),
):
    coll = _coll()
    if pair_id and pair_id.strip():
        oid = await _require_pair_oid(pair_id)
        total = await coll.count_documents({"language_pair_id": oid})
        return TextCorpusStatsResponse(total=total, by_pair=[])

    pipeline = [
        {"$group": {"_id": "$language_pair_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    by_pair: list[TextCorpusPairCount] = []
    total = 0
    async for row in coll.aggregate(pipeline):
        pid = row.get("_id")
        n = int(row.get("n") or 0)
        total += n
        if pid is not None:
            by_pair.append(TextCorpusPairCount(pair_id=str(pid), count=n))
    return TextCorpusStatsResponse(total=total, by_pair=by_pair)


@router.delete("")
async def clear_text_corpus_for_pair(
    pair_id: str = Query(..., description="language_pairs ObjectId"),
):
    """Delete all parallel text corpus rows and text batch claims/completions for a pair."""
    pair_oid = await _require_pair_oid(pair_id)
    part = await clear_text_corpus_for_pair_oid(pair_oid)
    return {
        "ok": True,
        "corpus_deleted": part["parallel_text_corpus"],
        "claims_deleted": part["text_batch_claims"],
        "completions_deleted": part["text_batch_completions"],
    }
