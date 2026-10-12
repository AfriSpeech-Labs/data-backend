"""Admin-only API: bidirectional TTS synth pairs (list, stream, validation, ZIP export).

Note: server-side CSV→TTS import is disabled on this deployment (returns 501).
Generate audio offline and upload via the insert endpoints / CLI scripts.
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.auth_admin import AdminIdentity, require_admin
from app.config import settings
from app.db import get_database
from app.models import (
    BulkCsvResponse,
    SynthPairListResponse,
    SynthPairStatsResponse,
    SynthPairValidationBody,
)
from app.services.language_pairs import get_pair_by_id
from app.services.synth_pairs_export import build_synth_export_zip
from app.services.synth_pairs_read import (
    get_synth_doc,
    get_synth_pair_stats,
    list_synth_pairs,
    stream_english,
    stream_swahili,
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


async def _pair_oid_required(pair_id: str | None) -> tuple[ObjectId, dict]:
    if not pair_id or not str(pair_id).strip():
        raise HTTPException(status_code=400, detail="pair_id is required")
    try:
        oid = ObjectId(pair_id.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pair_id") from None
    pair = await get_pair_by_id(oid)
    if not pair:
        raise HTTPException(status_code=404, detail="Language pair not found")
    return oid, pair


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
    pair_id: str = Query(..., description="Language pair that drives TTS engines and voices"),
    source_column: str | None = Query(None, description="CSV source column; default from pair"),
    target_column: str | None = Query(None, description="CSV target column; default from pair"),
    english_column: str | None = Query(None, description="Alias for source_column (legacy)"),
    swahili_column: str | None = Query(None, description="Alias for target_column (legacy)"),
):
    """Server-side synth TTS is offline-only; this endpoint returns 501.

    Per-row TTS needs edge-tts + ffmpeg (and formerly torch for MMS), which are
    not available on the serverless host. Generate the paired WAVs offline and
    import them with ``scripts/import_synth_pairs_csv.py`` against the surviving
    insert endpoints.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Server-side TTS synthesis is disabled on this deployment. Generate "
            "audio offline with scripts/import_synth_pairs_csv.py (installs the "
            "optional TTS toolchain locally) and upload the resulting WAVs."
        ),
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
        "tts_english_engine": doc.get("tts_english_engine", "edge"),
        "gender_en": doc.get("gender_en"),
        "tts_swahili_voice": doc.get("tts_swahili_voice"),
        "tts_swahili_engine": doc.get("tts_swahili_engine", "edge"),
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
