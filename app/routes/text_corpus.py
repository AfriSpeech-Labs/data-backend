"""Contributor-facing text corpus translation submission API."""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.auth_contributor import ContributorIdentity, optional_contributor
from app.config import settings
from app.db import get_database
from app.models import TextTranslationItemResponse, TextTranslationSubmitBody

router = APIRouter(prefix="/api/text-corpus", tags=["text-corpus"])


def _coll():
    return get_database()[settings.parallel_text_corpus_collection]


def _serialize_task(doc: dict) -> TextTranslationItemResponse:
    return TextTranslationItemResponse(
        id=str(doc["_id"]),
        language_pair_id=str(doc["language_pair_id"]),
        status=doc.get("status", "pending"),
        text_source=doc.get("text_source", ""),
        text_target=doc.get("text_target"),
        source_language=doc.get("source_language", "kom"),
        target_language=doc.get("target_language", "english"),
        created_at=doc.get("created_at") or datetime.now(timezone.utc),
        submitted_at=doc.get("submitted_at"),
    )


async def _get_task_or_404(id: str) -> dict:
    try:
        oid = ObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid text corpus id") from None
    doc = await _coll().find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Text corpus item not found")
    if doc.get("task_type") != "translation":
        raise HTTPException(status_code=400, detail="Item is not a translation task")
    return doc


def _assert_contributor_pair(doc: dict, contrib: ContributorIdentity | None) -> None:
    if contrib is None:
        return
    raw = doc.get("language_pair_id")
    if raw is None or str(raw) != contrib.pair_id:
        raise HTTPException(
            status_code=403,
            detail="This text item belongs to a different language pair than your assignment",
        )


async def _save_translation(
    id: str,
    body: TextTranslationSubmitBody,
    contrib: ContributorIdentity | None,
    *,
    allow_replace: bool,
) -> TextTranslationItemResponse:
    doc = await _get_task_or_404(id)
    _assert_contributor_pair(doc, contrib)
    if doc.get("status") == "submitted" and not allow_replace:
        raise HTTPException(
            status_code=400,
            detail="Already submitted. Use PUT /api/text-corpus/{id}/translation to replace.",
        )

    text_target = body.text_target.strip()
    if not text_target:
        raise HTTPException(status_code=400, detail="text_target is required")

    now = datetime.now(timezone.utc)
    payload: dict = {
        "text_target": text_target,
        "status": "submitted",
        "submitted_at": now,
    }
    if contrib:
        try:
            payload["submitted_by_user_id"] = ObjectId(contrib.sub)
        except Exception:
            pass

    await _coll().update_one({"_id": doc["_id"]}, {"$set": payload})
    updated = {**doc, **payload}
    return _serialize_task(updated)


@router.post("/{id}/translation", response_model=TextTranslationItemResponse)
async def submit_text_translation(
    id: str,
    body: TextTranslationSubmitBody,
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    return await _save_translation(id, body, contrib, allow_replace=False)


@router.put("/{id}/translation", response_model=TextTranslationItemResponse)
async def replace_text_translation(
    id: str,
    body: TextTranslationSubmitBody,
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    return await _save_translation(id, body, contrib, allow_replace=True)
