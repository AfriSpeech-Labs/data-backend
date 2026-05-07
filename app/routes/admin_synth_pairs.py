"""Admin-only API: bidirectional TTS synth pairs (CSV import, list, stream, validation, ZIP export)."""
import asyncio
import csv
import io
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.auth_admin import AdminIdentity, require_admin
from app.config import settings
from app.db import get_database
from app.models import (
    BulkCsvErrorItem,
    BulkCsvResponse,
    SynthPairListResponse,
    SynthPairStatsResponse,
    SynthPairValidationBody,
)
from app.services.language_pairs import get_pair_by_id
from app.services.synth_pairs_export import build_synth_export_zip
from app.services.synth_pairs_insert import insert_synth_pair
from app.services.synth_pairs_read import (
    get_synth_doc,
    get_synth_pair_stats,
    list_synth_pairs,
    stream_english,
    stream_swahili,
)
from app.services.synth_pairs_tts import (
    generate_english_wav_for_synth,
    generate_swahili_wav_for_synth,
)

router = APIRouter(
    prefix="/api/admin/synth-pairs",
    tags=["admin-synth-pairs"],
    dependencies=[Depends(require_admin)],
)

SYNTH_CSV_DEFAULT_MAX = 50
SYNTH_CSV_HARD_CAP = 200


async def _pair_oid_optional(pair_id: str | None) -> ObjectId | None:
    """Resolve optional pair filter; validates ObjectId and pair existence."""
    if not pair_id or not str(pair_id).strip():
        return None
    try:
        oid = ObjectId(pair_id.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pair_id") from None
    pair = await get_pair_by_id(oid)
    if not pair:
        raise HTTPException(status_code=404, detail="Language pair not found")
    return oid


def _coll():
    return get_database()[settings.synth_pairs_collection]


@router.get("/export")
async def synth_pairs_export(
    limit: int = Query(500, ge=1),
    approved_only: bool = False,
    validation_status: str | None = Query(None),
    pair_id: str | None = Query(None, description="Scope export to a language pair"),
):
    if validation_status and validation_status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid validation_status")
    pair_oid = await _pair_oid_optional(pair_id)
    return await build_synth_export_zip(
        limit, approved_only, validation_status, pair_oid=pair_oid
    )


@router.get("", response_model=SynthPairListResponse)
async def synth_pairs_list(
    page: int = 1,
    limit: int = 20,
    validation_status: str | None = Query(None),
    pair_id: str | None = Query(None, description="Scope list to a language pair"),
):
    if validation_status and validation_status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid validation_status")
    pair_oid = await _pair_oid_optional(pair_id)
    return await list_synth_pairs(page, limit, validation_status, pair_oid)


@router.get("/stats", response_model=SynthPairStatsResponse)
async def synth_pairs_stats(
    validation_status: str | None = Query(None),
    pair_id: str | None = Query(None, description="Scope stats to a language pair"),
):
    """Totals for all synth pairs, or filtered by validation_status (same values as list)."""
    if validation_status and validation_status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid validation_status")
    pair_oid = await _pair_oid_optional(pair_id)
    return await get_synth_pair_stats(validation_status, pair_oid)


@router.post("/bulk-csv", response_model=BulkCsvResponse)
async def synth_bulk_csv(
    file: UploadFile = File(...),
    max_rows: int = Query(SYNTH_CSV_DEFAULT_MAX, ge=1, le=SYNTH_CSV_HARD_CAP),
    english_column: str = Query("english"),
    swahili_column: str = Query("swahili"),
    pair_id: str | None = Query(None, description="Tag imported rows with this language pair"),
):
    """Import parallel EN/SW text; TTS both languages per row; max 200 non-empty rows per request."""
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
    pair_oid = await _pair_oid_optional(pair_id)

    for row_index, en, sw in rows:
        if not en:
            errors.append(BulkCsvErrorItem(row=row_index, detail="Empty English text"))
            continue
        if not sw:
            errors.append(
                BulkCsvErrorItem(
                    row=row_index,
                    detail="Empty Swahili text (required for bidirectional TTS)",
                )
            )
            continue
        try:
            wav_en, voice_en = await asyncio.to_thread(generate_english_wav_for_synth, en)
        except ValueError as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=str(e)))
            continue
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"English TTS: {e!s}"))
            continue
        try:
            wav_sw, voice_sw, engine_sw, gender_sw = await asyncio.to_thread(
                generate_swahili_wav_for_synth, sw
            )
        except ValueError as e:
            msg = str(e)
            if "not implemented" in msg.lower():
                raise HTTPException(status_code=501, detail=msg) from e
            errors.append(BulkCsvErrorItem(row=row_index, detail=msg))
            continue
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"Swahili TTS: {e!s}"))
            continue
        try:
            new_id = await insert_synth_pair(
                wav_en,
                wav_sw,
                en,
                sw,
                voice_en,
                voice_sw,
                engine_sw,
                gender_sw,
                csv_row=row_index,
                language_pair_id=pair_oid,
            )
            created_ids.append(new_id)
        except Exception as e:
            errors.append(BulkCsvErrorItem(row=row_index, detail=f"DB/GridFS: {e!s}"))

    return BulkCsvResponse(
        created_ids=created_ids,
        errors=errors,
        processed=len(rows),
        failed=len(errors),
    )


@router.patch("/{id}/validation")
async def synth_validation(
    id: str,
    body: SynthPairValidationBody,
    admin: AdminIdentity = Depends(require_admin),
):
    try:
        oid = ObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    new_status = "approved" if body.decision == "approve" else "rejected"
    res = await _coll().update_one(
        {"_id": oid},
        {
            "$set": {
                "validation_status": new_status,
                "validated_at": datetime.now(timezone.utc),
                "validated_by": admin.email,
                "validation_note": (body.note or "").strip() or None,
            }
        },
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": id, "validation_status": new_status}


@router.get("/{id}/detail")
async def synth_detail(id: str):
    """Single document for admin UI (raw fields)."""
    doc = await get_synth_doc(id)
    return {
        "id": str(doc["_id"]),
        "text_english": doc.get("text_english"),
        "text_swahili": doc.get("text_swahili"),
        "length_english": doc.get("length_english"),
        "length_swahili": doc.get("length_swahili"),
        "validation_status": doc.get("validation_status", "pending"),
        "tts_english_voice": doc.get("tts_english_voice"),
        "tts_swahili_voice": doc.get("tts_swahili_voice"),
        "gender_sw": doc.get("gender_sw"),
        "validated_by": doc.get("validated_by"),
        "validation_note": doc.get("validation_note"),
    }


@router.get("/{id}/english")
async def synth_stream_english(id: str):
    return await stream_english(id)


@router.get("/{id}/swahili")
async def synth_stream_swahili(id: str):
    return await stream_swahili(id)
