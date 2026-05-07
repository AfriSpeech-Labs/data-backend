"""Admin-only read/export audio routes (same behavior as public /api/audio read paths)."""
from fastapi import APIRouter, Depends, Query

from app.auth_admin import require_admin
from app.services.speech_parallel_read import (
    build_export_response,
    fetch_audio_stats,
    fetch_audio_stats_durations,
    list_audio_paginated,
    stream_english_response,
    stream_swahili_response,
)

router = APIRouter(
    prefix="/api/admin/audio",
    tags=["admin-audio"],
    dependencies=[Depends(require_admin)],
)


@router.get("/stats/durations")
async def admin_audio_stats_durations(
    pair_id: str | None = Query(None),
):
    return await fetch_audio_stats_durations(pair_id)


@router.get("/stats")
async def admin_audio_stats(pair_id: str | None = Query(None)):
    return await fetch_audio_stats(pair_id)


@router.get("/export")
async def admin_export_dataset(
    limit: int = Query(500, ge=1),
    submitted_only: bool = False,
    pair_id: str | None = Query(None),
):
    return await build_export_response(limit, submitted_only, pair_id=pair_id)


@router.get("")
async def admin_list_audio(
    page: int = 1,
    limit: int = 20,
    pair_id: str | None = Query(None),
):
    return await list_audio_paginated(page, limit, pair_id)


@router.get("/{id}/english")
async def admin_stream_english(id: str):
    return await stream_english_response(id)


@router.get("/{id}/swahili")
async def admin_stream_swahili(id: str):
    return await stream_swahili_response(id)
