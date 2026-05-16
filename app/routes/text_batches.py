"""Text translation batch API: pair-scoped; claims by X-Session-ID or contributor JWT."""
import asyncio
import math
import random
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.auth_contributor import ContributorIdentity, optional_contributor
from app.config import settings
from app.db import get_database
from app.models import (
    CompletedBatchInfo,
    MyBatchesOverviewResponse,
    MyCurrentBatchResponse,
    TextTranslationItemResponse,
)
from app.services.language_pairs import resolve_pair_id

router = APIRouter(prefix="/api/text-batches", tags=["text-batches"])
CLAIMS_COLLECTION = "text_batch_claims"
COMPLETIONS_COLLECTION = "text_batch_completions"
BATCH_SIZE = 25
UNCLAIMED_RANDOM_PREVIEW = 3
_UNCLAIMED_PREVIEW_CHUNK = 15


def _coll():
    return get_database()[settings.parallel_text_corpus_collection]


def _serialize_id(oid) -> str:
    return str(oid)


def _actor_ref(session_id: str | None, user_id: str | None) -> str | None:
    if user_id:
        return f"u:{user_id}"
    if session_id:
        return f"s:{session_id}"
    return None


def _task_filter(pair_oid: ObjectId) -> dict:
    return {"language_pair_id": pair_oid, "task_type": "translation"}


async def _pair_scope(
    pair_id_q: str | None,
    contrib: ContributorIdentity | None,
) -> tuple[ObjectId, dict]:
    if contrib:
        return await resolve_pair_id(contrib.pair_id)
    return await resolve_pair_id(pair_id_q)


async def _count_pair(pair_oid: ObjectId) -> int:
    return await _coll().count_documents(_task_filter(pair_oid))


async def _submitted_count_for_batch(pair_oid: ObjectId, batch_index: int) -> int:
    skip = (batch_index - 1) * BATCH_SIZE
    cursor = _coll().aggregate(
        [
            {"$match": _task_filter(pair_oid)},
            {"$sort": {"_id": 1}},
            {"$skip": skip},
            {"$limit": BATCH_SIZE},
            {"$match": {"status": "submitted"}},
            {"$count": "n"},
        ]
    )
    async for doc in cursor:
        return doc.get("n", 0)
    return 0


async def _claimant_session_for_batch(
    pair_oid: ObjectId, batch_index: int
) -> str | None:
    doc = await get_database()[CLAIMS_COLLECTION].find_one(
        {"pair_id": pair_oid, "batch_index": batch_index}
    )
    if not doc:
        return None
    return doc.get("session_id")


async def _claimant_user_for_batch(
    pair_oid: ObjectId, batch_index: int
) -> str | None:
    doc = await get_database()[CLAIMS_COLLECTION].find_one(
        {"pair_id": pair_oid, "batch_index": batch_index}
    )
    if not doc:
        return None
    uid = doc.get("user_id")
    return str(uid) if uid else None


async def _session_claim_doc(pair_oid: ObjectId, session_id: str) -> dict | None:
    return await get_database()[CLAIMS_COLLECTION].find_one(
        {"pair_id": pair_oid, "session_id": session_id}
    )


async def _user_claim_doc(pair_oid: ObjectId, user_id: str) -> dict | None:
    try:
        oid = ObjectId(user_id)
    except Exception:
        return None
    return await get_database()[CLAIMS_COLLECTION].find_one(
        {"pair_id": pair_oid, "user_id": oid}
    )


async def _release_actor_claims(
    pair_oid: ObjectId, session_id: str | None, user_id: str | None
) -> None:
    db = get_database()
    if user_id:
        try:
            uid = ObjectId(user_id)
        except Exception:
            return
        await db[CLAIMS_COLLECTION].delete_many({"pair_id": pair_oid, "user_id": uid})
    elif session_id:
        await db[CLAIMS_COLLECTION].delete_many({"pair_id": pair_oid, "session_id": session_id})


async def _ensure_completion_logged(
    pair_oid: ObjectId,
    batch_index: int,
    session_id: str | None,
    user_id: str | None,
) -> None:
    if batch_index < 1:
        return
    ref = _actor_ref(session_id, user_id)
    if not ref:
        return
    await get_database()[COMPLETIONS_COLLECTION].update_one(
        {
            "pair_id": pair_oid,
            "batch_index": batch_index,
            "actor_ref": ref,
        },
        {"$setOnInsert": {"completed_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


@router.get("")
async def list_text_batches(
    pair_id: str | None = Query(None),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    pair_oid, _ = await _pair_scope(pair_id, contrib)
    total = await _count_pair(pair_oid)
    num_batches = max(0, math.ceil(total / BATCH_SIZE))
    if num_batches == 0:
        return {"batches": [], "total_items": total}

    db = get_database()
    claim_by_batch: dict[int, str] = {}
    async for doc in db[CLAIMS_COLLECTION].find({"pair_id": pair_oid}):
        idx = doc.get("batch_index")
        sid = doc.get("session_id")
        uid = doc.get("user_id")
        if idx is not None:
            claim_by_batch[int(idx)] = str(uid) if uid else (sid or "")

    unclaimed: list[int] = []
    for k in range(1, num_batches + 1):
        if claim_by_batch.get(k) is not None:
            continue
        unclaimed.append(k)

    if not unclaimed:
        return {"batches": [], "total_items": total}

    random.shuffle(unclaimed)
    picked: list[tuple[int, int]] = []
    offset = 0
    while len(picked) < UNCLAIMED_RANDOM_PREVIEW and offset < len(unclaimed):
        chunk = unclaimed[offset : offset + _UNCLAIMED_PREVIEW_CHUNK]
        offset += len(chunk)
        if not chunk:
            break
        subs = await asyncio.gather(
            *[_submitted_count_for_batch(pair_oid, k) for k in chunk],
        )
        for k, sub in zip(chunk, subs, strict=True):
            if len(picked) >= UNCLAIMED_RANDOM_PREVIEW:
                break
            if sub < BATCH_SIZE:
                picked.append((k, sub))
        if len(picked) >= UNCLAIMED_RANDOM_PREVIEW:
            break

    random.shuffle(picked)
    return {
        "batches": [
            {
                "batch_index": k,
                "status": "pending",
                "submitted_count": submitted,
                "total": BATCH_SIZE,
                "claimed_by_you": False,
            }
            for k, submitted in picked
        ],
        "total_items": total,
    }


@router.get("/current")
async def get_current_text_batch(
    pair_id: str | None = Query(None),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    pair_oid, _ = await _pair_scope(pair_id, contrib)
    if contrib:
        claim = await _user_claim_doc(pair_oid, contrib.sub)
    else:
        if not x_session_id:
            raise HTTPException(status_code=400, detail="X-Session-ID header required")
        claim = await _session_claim_doc(pair_oid, x_session_id)
    if not claim:
        raise HTTPException(status_code=404, detail="No batch claimed")
    batch_index = int(claim["batch_index"])
    submitted = await _submitted_count_for_batch(pair_oid, batch_index)
    status = "completed" if submitted >= BATCH_SIZE else "submission_ongoing"
    if submitted >= BATCH_SIZE:
        await _ensure_completion_logged(
            pair_oid,
            batch_index,
            None if contrib else x_session_id,
            contrib.sub if contrib else None,
        )
    return {
        "batch_index": batch_index,
        "status": status,
        "submitted_count": submitted,
        "total": BATCH_SIZE,
    }


@router.get("/my-overview", response_model=MyBatchesOverviewResponse)
async def my_text_batches_overview(
    pair_id: str | None = Query(None),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    pair_oid, _ = await _pair_scope(pair_id, contrib)
    if contrib:
        session_for_legacy = None
        uid = contrib.sub
        claim = await _user_claim_doc(pair_oid, uid)
    else:
        if not x_session_id:
            raise HTTPException(status_code=400, detail="X-Session-ID header required")
        session_for_legacy = x_session_id
        uid = None
        claim = await _session_claim_doc(pair_oid, x_session_id)

    current: MyCurrentBatchResponse | None = None
    if claim:
        batch_index = int(claim["batch_index"])
        submitted = await _submitted_count_for_batch(pair_oid, batch_index)
        if submitted >= BATCH_SIZE:
            await _ensure_completion_logged(pair_oid, batch_index, session_for_legacy, uid)
        else:
            current = MyCurrentBatchResponse(
                batch_index=batch_index,
                status="submission_ongoing",
                submitted_count=submitted,
                total=BATCH_SIZE,
            )

    completed: list[CompletedBatchInfo] = []
    ref = _actor_ref(session_for_legacy, uid)
    if ref:
        async for doc in get_database()[COMPLETIONS_COLLECTION].find(
            {"pair_id": pair_oid, "actor_ref": ref}
        ).sort("completed_at", -1):
            completed.append(
                CompletedBatchInfo(
                    batch_index=int(doc["batch_index"]),
                    completed_at=doc["completed_at"],
                )
            )
    return MyBatchesOverviewResponse(current=current, completed_batches=completed)


@router.post("/{batch_index}/claim")
async def claim_text_batch(
    batch_index: int,
    pair_id: str | None = Query(None),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    pair_oid, _ = await _pair_scope(pair_id, contrib)
    if contrib:
        sid = None
        uid = contrib.sub
    else:
        if not x_session_id:
            raise HTTPException(status_code=400, detail="X-Session-ID header required")
        sid = x_session_id
        uid = None

    if batch_index < 1:
        raise HTTPException(status_code=400, detail="batch_index must be >= 1")

    total = await _count_pair(pair_oid)
    num_batches = math.ceil(total / BATCH_SIZE) if total else 0
    if batch_index > num_batches:
        raise HTTPException(status_code=404, detail="Batch does not exist")

    if contrib:
        existing = await _user_claim_doc(pair_oid, uid)
    else:
        existing = await _session_claim_doc(pair_oid, sid)

    if existing:
        other_index = int(existing["batch_index"])
        if other_index != batch_index:
            other_submitted = await _submitted_count_for_batch(pair_oid, other_index)
            if other_submitted < BATCH_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail="Finish your current batch before selecting another.",
                )
            await _ensure_completion_logged(pair_oid, other_index, sid, uid)

    claimed_by_session = await _claimant_session_for_batch(pair_oid, batch_index)
    claimed_by_user = await _claimant_user_for_batch(pair_oid, batch_index)
    if uid:
        if claimed_by_user and claimed_by_user != uid:
            raise HTTPException(status_code=409, detail="Batch already in progress")
        if claimed_by_session:
            raise HTTPException(status_code=409, detail="Batch already in progress")
    else:
        if claimed_by_session and claimed_by_session != sid:
            raise HTTPException(status_code=409, detail="Batch already in progress")
        if claimed_by_user:
            raise HTTPException(status_code=409, detail="Batch already in progress")

    await _release_actor_claims(pair_oid, sid, uid)
    doc_claim = {
        "pair_id": pair_oid,
        "batch_index": batch_index,
        "claimed_at": datetime.now(timezone.utc),
    }
    if uid:
        doc_claim["user_id"] = ObjectId(uid)
    else:
        doc_claim["session_id"] = sid
    await get_database()[CLAIMS_COLLECTION].insert_one(doc_claim)

    submitted = await _submitted_count_for_batch(pair_oid, batch_index)
    return {
        "batch_index": batch_index,
        "status": "completed" if submitted >= BATCH_SIZE else "submission_ongoing",
        "submitted_count": submitted,
        "total": BATCH_SIZE,
    }


@router.get("/{batch_index}/items", response_model=list[TextTranslationItemResponse])
async def get_text_batch_items(
    batch_index: int,
    pair_id: str | None = Query(None),
    contrib: ContributorIdentity | None = Depends(optional_contributor),
):
    pair_oid, _ = await _pair_scope(pair_id, contrib)
    if batch_index < 1:
        raise HTTPException(status_code=400, detail="batch_index must be >= 1")
    total = await _count_pair(pair_oid)
    num_batches = math.ceil(total / BATCH_SIZE) if total else 0
    if batch_index > num_batches:
        raise HTTPException(status_code=404, detail="Batch does not exist")

    skip = (batch_index - 1) * BATCH_SIZE
    cursor = (
        _coll()
        .find(_task_filter(pair_oid))
        .sort("_id", 1)
        .skip(skip)
        .limit(BATCH_SIZE)
    )
    items = []
    async for doc in cursor:
        items.append(
            TextTranslationItemResponse(
                id=_serialize_id(doc["_id"]),
                language_pair_id=str(doc["language_pair_id"]),
                status=doc.get("status", "pending"),
                text_source=str(doc.get("text_source") or ""),
                text_target=doc.get("text_target"),
                source_language=doc.get("source_language", "english"),
                target_language=doc.get("target_language", "kom"),
                created_at=doc.get("created_at") or datetime.now(timezone.utc),
                submitted_at=doc.get("submitted_at"),
            )
        )
    return items
