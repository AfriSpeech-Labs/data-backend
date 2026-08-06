"""Reviewer API: pair-scoped synth TTS QA (list, stream, approve/reject)."""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth_contributor import ContributorIdentity, require_reviewer
from app.config import settings
from app.db import get_database
from app.models import (
    SynthPairListResponse,
    SynthPairStatsResponse,
    SynthPairValidationBody,
)
from app.services.synth_pairs_read import (
    get_synth_doc,
    get_synth_pair_stats,
    list_synth_pairs,
    stream_english,
    stream_swahili,
)

router = APIRouter(
    prefix="/api/reviewer/synth-pairs",
    tags=["reviewer-synth-pairs"],
    dependencies=[Depends(require_reviewer)],
)


def _coll():
    return get_database()[settings.synth_pairs_collection]


def _pair_oid(identity: ContributorIdentity) -> ObjectId:
    try:
        return ObjectId(identity.pair_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pair assignment") from None


def _assert_synth_pair_access(doc: dict, pair_oid: ObjectId) -> None:
    raw = doc.get("language_pair_id")
    if raw is None or str(raw) != str(pair_oid):
        raise HTTPException(
            status_code=403,
            detail="This synth pair belongs to a different language pair",
        )


@router.get("", response_model=SynthPairListResponse)
async def reviewer_synth_list(
    page: int = 1,
    limit: int = 20,
    validation_status: str | None = Query(
        None,
        description="pending | approved | rejected; omit or empty for all",
    ),
    identity: ContributorIdentity = Depends(require_reviewer),
):
    # Default to pending when the client omits the filter (typical reviewer queue).
    raw = validation_status if validation_status is not None else "pending"
    status = raw.strip() or None
    if status and status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid validation_status")
    return await list_synth_pairs(page, limit, status, _pair_oid(identity))


@router.get("/stats", response_model=SynthPairStatsResponse)
async def reviewer_synth_stats(
    validation_status: str | None = Query(None),
    identity: ContributorIdentity = Depends(require_reviewer),
):
    status = (validation_status or "").strip() or None
    if status and status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid validation_status")
    return await get_synth_pair_stats(status, _pair_oid(identity))


@router.patch("/{id}/validation")
async def reviewer_synth_validation(
    id: str,
    body: SynthPairValidationBody,
    identity: ContributorIdentity = Depends(require_reviewer),
):
    pair_oid = _pair_oid(identity)
    doc = await get_synth_doc(id)
    _assert_synth_pair_access(doc, pair_oid)
    try:
        oid = ObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    new_status = "approved" if body.decision == "approve" else "rejected"
    validated_by = f"user:{identity.sub}"
    res = await _coll().update_one(
        {"_id": oid, "language_pair_id": pair_oid},
        {
            "$set": {
                "validation_status": new_status,
                "validated_at": datetime.now(timezone.utc),
                "validated_by": validated_by,
                "validation_note": (body.note or "").strip() or None,
            }
        },
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": id, "validation_status": new_status}


@router.get("/{id}/english")
async def reviewer_stream_english(
    id: str,
    identity: ContributorIdentity = Depends(require_reviewer),
):
    doc = await get_synth_doc(id)
    _assert_synth_pair_access(doc, _pair_oid(identity))
    return await stream_english(id)


@router.get("/{id}/swahili")
async def reviewer_stream_swahili(
    id: str,
    identity: ContributorIdentity = Depends(require_reviewer),
):
    doc = await get_synth_doc(id)
    _assert_synth_pair_access(doc, _pair_oid(identity))
    return await stream_swahili(id)
