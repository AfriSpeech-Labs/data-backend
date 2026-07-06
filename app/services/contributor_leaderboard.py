"""Contributor leaderboard for a language pair."""
from __future__ import annotations

import re
from typing import Literal

from bson import ObjectId

from app.config import settings
from app.db import get_database
from app.services.language_pairs import get_pair_by_id

TaskKind = Literal["text", "audio"]
CONTRIBUTOR_COLLECTION = "contributor_users"


def _mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 4:
        return f"···{digits[-4:]}"
    return "Contributor"


def _contributor_pair_filter(pair_oid: ObjectId) -> dict:
    return {
        "$or": [
            {"language_pair_id": pair_oid},
            {"language_pair_id": str(pair_oid)},
        ]
    }


async def _submission_counts_by_user(
    pair_oid: ObjectId,
    task: TaskKind,
) -> dict[str, int]:
    db = get_database()
    if task == "text":
        coll = db[settings.parallel_text_corpus_collection]
        match = {
            "language_pair_id": pair_oid,
            "task_type": "translation",
            "status": "submitted",
            "submitted_by_user_id": {"$exists": True, "$ne": None},
        }
    else:
        coll = db["speech_parallel"]
        match = {
            "pair_id": pair_oid,
            "status": "submitted",
            "submitted_by_user_id": {"$exists": True, "$ne": None},
        }

    counts: dict[str, int] = {}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$submitted_by_user_id", "submitted_count": {"$sum": 1}}},
    ]
    async for row in coll.aggregate(pipeline):
        uid = row.get("_id")
        if uid is None:
            continue
        counts[str(uid)] = int(row.get("submitted_count") or 0)
    return counts


async def fetch_contributor_leaderboard(
    *,
    pair_oid: ObjectId,
    task: TaskKind,
    current_user_id: str | None,
) -> dict:
    pair_doc = await get_pair_by_id(pair_oid)
    label = (
        str(pair_doc.get("display_name") or pair_doc.get("slug") or "Language pair")
        if pair_doc
        else "Language pair"
    )

    db = get_database()
    pair_filter = _contributor_pair_filter(pair_oid)
    contributor_count = await db[CONTRIBUTOR_COLLECTION].count_documents(pair_filter)
    if contributor_count < 1:
        return {"enabled": False, "language_pair_label": label, "entries": []}

    submission_counts = await _submission_counts_by_user(pair_oid, task)

    contributors: list[dict] = []
    async for doc in db[CONTRIBUTOR_COLLECTION].find(pair_filter):
        uid = str(doc["_id"])
        phone = str(doc.get("phone_e164", ""))
        contributors.append(
            {
                "user_id": uid,
                "phone": phone,
                "submitted_count": submission_counts.get(uid, 0),
            }
        )

    contributors.sort(key=lambda c: c["submitted_count"], reverse=True)
    entries = []
    for rank, c in enumerate(contributors, start=1):
        is_you = current_user_id is not None and c["user_id"] == current_user_id
        entries.append(
            {
                "rank": rank,
                "display_name": "You" if is_you else _mask_phone(c["phone"]),
                "submitted_count": c["submitted_count"],
                "is_current_user": is_you,
            }
        )

    return {"enabled": True, "language_pair_label": label, "entries": entries}
