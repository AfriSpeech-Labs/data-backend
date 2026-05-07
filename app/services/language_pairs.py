"""language_pairs collection + R2 key helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import HTTPException

from app.config import settings
from app.db import get_database

COLLECTION = "language_pairs"


def pair_audio_keys(
    pair: dict,
    item_id_str: str,
    side: str,
) -> tuple[str, str]:
    """Return (r2_key, filename_suffix) for source or target side."""
    slug = pair["slug"]
    if side == "source":
        folder = pair["audio_folder_source"]
        sfx = pair["file_suffix_source"]
    elif side == "target":
        folder = pair["audio_folder_target"]
        sfx = pair["file_suffix_target"]
    else:
        raise ValueError("side must be source or target")
    key = f"{slug}/audio/{folder}/{item_id_str}_{sfx}.wav"
    return key, sfx


def synth_audio_keys(
    pair: dict,
    item_id_str: str,
    side: str,
) -> tuple[str, str]:
    """Return (r2_key, filename_suffix) for synth-pairs audio.

    Uses a stable prefix to avoid collisions with parallel dataset audio.
    """
    slug = pair["slug"]
    if side == "source":
        folder = pair["audio_folder_source"]
        sfx = pair["file_suffix_source"]
    elif side == "target":
        folder = pair["audio_folder_target"]
        sfx = pair["file_suffix_target"]
    else:
        raise ValueError("side must be source or target")
    key = f"{slug}/synth/audio/{folder}/{item_id_str}_{sfx}.wav"
    return key, sfx


async def get_pair_by_id(pair_id: ObjectId | str) -> dict | None:
    db = get_database()
    oid = pair_id if isinstance(pair_id, ObjectId) else ObjectId(pair_id)
    return await db[COLLECTION].find_one({"_id": oid, "active": {"$ne": False}})


async def get_pair_by_slug(slug: str) -> dict | None:
    db = get_database()
    return await db[COLLECTION].find_one(
        {"slug": slug.strip().lower(), "active": {"$ne": False}}
    )


async def resolve_default_pair() -> dict:
    pair = await get_pair_by_slug(settings.default_language_pair_slug)
    if pair:
        return pair
    db = get_database()
    first = await db[COLLECTION].find_one({"active": {"$ne": False}}, sort=[("_id", 1)])
    if first:
        return first
    raise HTTPException(
        status_code=503,
        detail="No language pair configured. Seed language_pairs collection.",
    )


async def resolve_pair_id(pair_id: str | None) -> tuple[ObjectId, dict]:
    if pair_id:
        try:
            oid = ObjectId(pair_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pair_id") from None
        pair = await get_pair_by_id(oid)
        if not pair:
            raise HTTPException(status_code=404, detail="Language pair not found")
        return oid, pair
    pair = await resolve_default_pair()
    return pair["_id"], pair


async def get_default_pair_document() -> dict | None:
    """Return default pair doc or first active pair; no HTTP exceptions."""
    pair = await get_pair_by_slug(settings.default_language_pair_slug)
    if pair:
        return pair
    db = get_database()
    return await db[COLLECTION].find_one({"active": {"$ne": False}}, sort=[("_id", 1)])


async def ensure_default_pair_seeded() -> None:
    """Insert Swahili–English default pair if collection empty."""
    db = get_database()
    count = await db[COLLECTION].count_documents({})
    if count > 0:
        return
    doc: dict[str, Any] = {
        "slug": settings.default_language_pair_slug,
        "display_name": "Swahili–English",
        "audio_folder_source": "english",
        "audio_folder_target": "swahili",
        "file_suffix_source": "en",
        "file_suffix_target": "sw",
        "active": True,
        "created_at": datetime.now(timezone.utc),
    }
    await db[COLLECTION].insert_one(doc)
