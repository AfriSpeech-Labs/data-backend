"""Admin-only full dataset ZIP downloads (parallel + synthetic) with status filters; no row cap."""
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.auth_admin import require_admin
from app.services.speech_parallel_read import build_export_response
from app.services.synth_pairs_export import build_synth_export_zip

router = APIRouter(
    prefix="/api/admin/datasets",
    tags=["admin-datasets"],
    dependencies=[Depends(require_admin)],
)


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
