"""Admin: list and release audio/text batch claims."""
from __future__ import annotations

from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth_admin import require_admin
from app.models import AdminBatchClaimItem, AdminBatchClaimsListResponse
from app.services.batch_claims import list_admin_batch_claims, release_batch_claim

router = APIRouter(
    prefix="/api/admin/batch-claims",
    tags=["admin-batch-claims"],
    dependencies=[Depends(require_admin)],
)


def _parse_oid(value: str, label: str) -> ObjectId:
    try:
        return ObjectId(value.strip())
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from None


@router.get("", response_model=AdminBatchClaimsListResponse)
async def list_batch_claims(
    pair_id: str | None = Query(None),
    contributor_id: str | None = Query(None),
    workflow: Literal["audio", "text"] | None = Query(None),
):
    pair_oid = _parse_oid(pair_id, "pair_id") if pair_id and pair_id.strip() else None
    contrib_oid = (
        _parse_oid(contributor_id, "contributor_id")
        if contributor_id and contributor_id.strip()
        else None
    )
    raw = await list_admin_batch_claims(
        pair_id=pair_oid,
        contributor_id=contrib_oid,
        workflow=workflow,
    )
    items = [AdminBatchClaimItem(**row) for row in raw]
    return AdminBatchClaimsListResponse(items=items)


@router.delete("/{workflow}/{pair_id}/{batch_index}")
async def release_batch_claim_endpoint(
    workflow: Literal["audio", "text"],
    pair_id: str,
    batch_index: int,
):
    if batch_index < 1:
        raise HTTPException(status_code=400, detail="batch_index must be >= 1")
    pair_oid = _parse_oid(pair_id, "pair_id")
    released = await release_batch_claim(workflow, pair_oid, batch_index)
    if not released:
        raise HTTPException(status_code=404, detail="No claim found for this batch")
    return {"ok": True, "released": True}
