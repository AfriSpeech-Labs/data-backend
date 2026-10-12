"""Admin-only dataset operations: full ZIP downloads + chunked HuggingFace sync."""
from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth_admin import require_admin
from app.services.hf_sync import HfSyncConfigError, sync_batch, sync_text_batch
from app.services.language_pairs import get_pair_by_id
from app.services.speech_parallel_read import build_export_response
from app.services.synth_pairs_export import build_synth_export_zip

router = APIRouter(
    prefix="/api/admin/datasets",
    tags=["admin-datasets"],
    dependencies=[Depends(require_admin)],
)


class HfSyncRequest(BaseModel):
    dataset: Literal["speech", "text"] = Field(
        "speech",
        description="Which collection to sync: 'speech' (audio) or 'text' (text-to-text).",
    )
    repo_id: str | None = Field(
        None,
        description=(
            "Target HF dataset repo 'owner/name'. Falls back to the pair's hf_repo_id "
            "(speech) or hf_text_repo_id (text)."
        ),
    )
    pair_id: str | None = Field(None, description="Scope sync to one language pair (ObjectId).")
    cursor: str | None = Field(None, description="Last _id returned by the previous call; omit to start.")
    limit: int | None = Field(None, description="Records per call (server caps this).")
    resync: bool = Field(False, description="Re-push records already marked hf_synced_at.")
    dry_run: bool = Field(False, description="Report what would be pushed without contacting HF.")


class HfSyncResponse(BaseModel):
    repo_id: str
    pushed: int
    skipped: int
    next_cursor: str | None
    done: bool
    pushed_ids: list[str]
    dry_run: bool


@router.get("/parallel/download")
async def download_parallel_dataset(
    status: Literal["all", "pending", "submitted"] = Query(
        "all",
        description="Filter by recording status; exports all matching rows (no limit).",
    ),
    pair_id: str | None = Query(None, description="Filter by language pair ObjectId"),
):
    return await build_export_response(status=status, uncapped=True, pair_id=pair_id)


@router.get("/synthetic/download")
async def download_synthetic_dataset(
    status: Literal["all", "pending", "approved", "rejected"] = Query(
        "all",
        description="Filter by validation_status; exports all matching rows (no limit).",
    ),
):
    return await build_synth_export_zip(uncapped=True, status=status)


@router.post("/hf-sync", response_model=HfSyncResponse)
async def hf_sync(body: HfSyncRequest):
    """Push submitted speech records to a HuggingFace dataset repo, one batch per call.

    Call repeatedly, passing the returned ``next_cursor`` back as ``cursor``,
    until ``done`` is true. ``repo_id`` may be omitted when the language pair has
    an ``hf_repo_id`` configured.
    """
    pair_oid: ObjectId | None = None
    pair_doc: dict | None = None
    if body.pair_id:
        try:
            pair_oid = ObjectId(body.pair_id.strip())
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pair_id") from None
        pair_doc = await get_pair_by_id(pair_oid)
        if not pair_doc:
            raise HTTPException(status_code=404, detail="Language pair not found")

    is_text = body.dataset == "text"
    pair_repo_field = "hf_text_repo_id" if is_text else "hf_repo_id"
    repo_id = (body.repo_id or "").strip() or (
        (pair_doc or {}).get(pair_repo_field) if pair_doc else None
    )
    if not repo_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"repo_id is required. Provide it in the request body or set "
                f"{pair_repo_field} on the selected language pair."
            ),
        )

    sync_fn = sync_text_batch if is_text else sync_batch
    try:
        result = await sync_fn(
            repo_id=repo_id,
            pair_oid=pair_oid,
            cursor=body.cursor,
            limit=body.limit,
            resync=body.resync,
            dry_run=body.dry_run,
        )
    except HfSyncConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return HfSyncResponse(
        repo_id=result.repo_id,
        pushed=result.pushed,
        skipped=result.skipped,
        next_cursor=result.next_cursor,
        done=result.done,
        pushed_ids=result.pushed_ids,
        dry_run=result.dry_run,
    )
