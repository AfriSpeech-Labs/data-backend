"""Shared helpers for audio/text batch claims (admin list + release)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from bson import ObjectId

from app.config import settings
from app.db import get_database

BATCH_SIZE = 25
AUDIO_CLAIMS = "batch_claims"
TEXT_CLAIMS = "text_batch_claims"
SPEECH_COLLECTION = "speech_parallel"
CONTRIBUTOR_COLLECTION = "contributor_users"

Workflow = Literal["audio", "text"]


def claims_collection(workflow: Workflow) -> str:
    return AUDIO_CLAIMS if workflow == "audio" else TEXT_CLAIMS


async def audio_submitted_count_for_batch(pair_oid: ObjectId, batch_index: int) -> int:
    db = get_database()
    skip = (batch_index - 1) * BATCH_SIZE
    cursor = db[SPEECH_COLLECTION].aggregate(
        [
            {"$match": {"pair_id": pair_oid}},
            {"$sort": {"_id": 1}},
            {"$skip": skip},
            {"$limit": BATCH_SIZE},
            {"$match": {"status": "submitted"}},
            {"$count": "n"},
        ]
    )
    async for doc in cursor:
        return int(doc.get("n") or 0)
    return 0


async def text_submitted_count_for_batch(pair_oid: ObjectId, batch_index: int) -> int:
    coll = get_database()[settings.parallel_text_corpus_collection]
    skip = (batch_index - 1) * BATCH_SIZE
    flt = {"language_pair_id": pair_oid, "task_type": "translation"}
    cursor = coll.aggregate(
        [
            {"$match": flt},
            {"$sort": {"_id": 1}},
            {"$skip": skip},
            {"$limit": BATCH_SIZE},
            {"$match": {"status": "submitted"}},
            {"$count": "n"},
        ]
    )
    async for doc in cursor:
        return int(doc.get("n") or 0)
    return 0


async def submitted_count_for_batch(
    workflow: Workflow, pair_oid: ObjectId, batch_index: int
) -> int:
    if workflow == "audio":
        return await audio_submitted_count_for_batch(pair_oid, batch_index)
    return await text_submitted_count_for_batch(pair_oid, batch_index)


def batch_status(submitted_count: int, total: int = BATCH_SIZE) -> str:
    return "completed" if submitted_count >= total else "submission_ongoing"


async def _phone_by_user_id(user_ids: set[ObjectId]) -> dict[str, str]:
    if not user_ids:
        return {}
    db = get_database()
    out: dict[str, str] = {}
    async for doc in db[CONTRIBUTOR_COLLECTION].find({"_id": {"$in": list(user_ids)}}):
        out[str(doc["_id"])] = str(doc.get("phone_e164", ""))
    return out


async def _claim_doc_to_item(
    workflow: Workflow,
    doc: dict,
    phones: dict[str, str],
) -> dict:
    pair_oid = doc["pair_id"]
    batch_index = int(doc["batch_index"])
    uid = doc.get("user_id")
    contributor_user_id = str(uid) if uid else None
    contributor_phone = phones.get(contributor_user_id) if contributor_user_id else None
    session_id = doc.get("session_id")
    submitted = await submitted_count_for_batch(workflow, pair_oid, batch_index)
    claimed_at = doc.get("claimed_at") or datetime.now(timezone.utc)
    return {
        "workflow": workflow,
        "pair_id": str(pair_oid),
        "batch_index": batch_index,
        "claimed_at": claimed_at,
        "contributor_user_id": contributor_user_id,
        "contributor_phone": contributor_phone,
        "session_id": session_id,
        "submitted_count": submitted,
        "total": BATCH_SIZE,
        "status": batch_status(submitted),
    }


async def list_admin_batch_claims(
    *,
    pair_id: ObjectId | None = None,
    contributor_id: ObjectId | None = None,
    workflow: Workflow | None = None,
) -> list[dict]:
    db = get_database()
    workflows: list[Workflow] = (
        [workflow] if workflow else ["audio", "text"]
    )

    claim_docs: list[tuple[Workflow, dict]] = []
    for wf in workflows:
        coll_name = claims_collection(wf)
        flt: dict = {}
        if pair_id is not None:
            flt["pair_id"] = pair_id
        if contributor_id is not None:
            flt["user_id"] = contributor_id
        cursor = db[coll_name].find(flt).sort("claimed_at", -1)
        async for doc in cursor:
            claim_docs.append((wf, doc))

    user_ids: set[ObjectId] = set()
    for _, doc in claim_docs:
        uid = doc.get("user_id")
        if uid is not None:
            user_ids.add(uid if isinstance(uid, ObjectId) else ObjectId(uid))
    phones = await _phone_by_user_id(user_ids)

    items: list[dict] = []
    for wf, doc in claim_docs:
        items.append(await _claim_doc_to_item(wf, doc, phones))

    items.sort(key=lambda x: x["claimed_at"], reverse=True)
    return items


async def release_batch_claim(
    workflow: Workflow,
    pair_oid: ObjectId,
    batch_index: int,
) -> bool:
    db = get_database()
    coll_name = claims_collection(workflow)
    result = await db[coll_name].delete_one(
        {"pair_id": pair_oid, "batch_index": batch_index}
    )
    return result.deleted_count > 0
