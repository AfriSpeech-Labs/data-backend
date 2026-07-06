"""Contributor-facing leaderboard (multi-contributor language pairs)."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.auth_contributor import ContributorIdentity, optional_contributor
from app.models import LeaderboardResponse
from app.services.contributor_leaderboard import fetch_contributor_leaderboard
from app.services.language_pairs import resolve_pair_id

router = APIRouter(prefix="/api/contributor", tags=["contributor"])


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def contributor_leaderboard(
    task: Literal["text", "audio"] = Query("text"),
    pair_id: str | None = Query(None),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    if contrib:
        pair_oid, _ = await resolve_pair_id(contrib.pair_id)
        current_user_id = contrib.sub
    else:
        if not pair_id:
            raise HTTPException(
                status_code=400,
                detail="pair_id query param or contributor login required",
            )
        pair_oid, _ = await resolve_pair_id(pair_id)
        current_user_id = None

    data = await fetch_contributor_leaderboard(
        pair_oid=pair_oid,
        task=task,
        current_user_id=current_user_id,
    )
    return LeaderboardResponse(**data)
