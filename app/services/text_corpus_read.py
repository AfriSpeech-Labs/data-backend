"""Read helpers for parallel text corpus (translation tasks)."""
from __future__ import annotations

from bson import ObjectId

from app.config import settings
from app.db import get_database
from app.models import TextTranslationStatsResponse
from app.services.language_pairs import resolve_pair_id


def _text_corpus_coll():
    return get_database()[settings.parallel_text_corpus_collection]


def _translation_filter(pair_oid: ObjectId) -> dict:
    return {"language_pair_id": pair_oid, "task_type": "translation"}


async def _pair_oid_for_stats(pair_id: str | None) -> ObjectId:
    pair_oid, _ = await resolve_pair_id(pair_id)
    return pair_oid


async def fetch_text_translation_stats(
    pair_id: str | None = None,
) -> TextTranslationStatsResponse:
    """Counts for translation tasks scoped to a language pair."""
    pair_oid = await _pair_oid_for_stats(pair_id)
    coll = _text_corpus_coll()
    flt = _translation_filter(pair_oid)
    total = await coll.count_documents(flt)
    submitted = await coll.count_documents({**flt, "status": "submitted"})
    pending = await coll.count_documents({**flt, "status": {"$ne": "submitted"}})
    return TextTranslationStatsResponse(
        total=total,
        pending=pending,
        submitted=submitted,
    )
