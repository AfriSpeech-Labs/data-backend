"""Admin CRUD for language pairs."""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth_admin import AdminIdentity, require_admin
from app.db import get_database
from app.models import LanguagePairDeleteCounts, LanguagePairDeleteResponse
from app.services.language_pairs import COLLECTION, get_pair_by_slug
from app.services.pair_delete import (
    clear_pair_mongo,
    clear_pair_r2_prefix,
    count_contributors_for_pair,
    count_pair_data,
    delete_language_pair_document,
    total_data_count,
)

router = APIRouter(prefix="/api/admin/language-pairs", tags=["admin-language-pairs"])


class PairCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=64)
    display_name: str = Field(..., min_length=1)
    audio_folder_source: str = Field(..., min_length=1, max_length=64)
    audio_folder_target: str = Field(..., min_length=1, max_length=64)
    file_suffix_source: str = Field(..., min_length=1, max_length=16)
    file_suffix_target: str = Field(..., min_length=1, max_length=16)
    active: bool = True


class PairOut(BaseModel):
    id: str
    slug: str
    display_name: str
    audio_folder_source: str
    audio_folder_target: str
    file_suffix_source: str
    file_suffix_target: str
    active: bool


class PairSummaryOut(PairOut):
    total_items: int = 0
    submitted_items: int = 0
    pending_items: int = 0


def _out(doc: dict) -> PairOut:
    return PairOut(
        id=str(doc["_id"]),
        slug=doc["slug"],
        display_name=doc.get("display_name", ""),
        audio_folder_source=doc.get("audio_folder_source", ""),
        audio_folder_target=doc.get("audio_folder_target", ""),
        file_suffix_source=doc.get("file_suffix_source", ""),
        file_suffix_target=doc.get("file_suffix_target", ""),
        active=bool(doc.get("active", True)),
    )


SPEECH_COLLECTION = "speech_parallel"


@router.get("/summary", response_model=list[PairSummaryOut])
async def list_pairs_summary(_: AdminIdentity = Depends(require_admin)):
    """Each language pair with parallel item counts from ``speech_parallel``."""
    db = get_database()
    counts: dict[str, dict[str, int]] = {}
    pipeline = [
        {
            "$group": {
                "_id": "$pair_id",
                "total_items": {"$sum": 1},
                "submitted_items": {
                    "$sum": {"$cond": [{"$eq": ["$status", "submitted"]}, 1, 0]},
                },
                "pending_items": {
                    "$sum": {"$cond": [{"$eq": ["$status", "pending"]}, 1, 0]},
                },
            }
        },
    ]
    async for row in db[SPEECH_COLLECTION].aggregate(pipeline):
        pid = row.get("_id")
        if pid is None:
            continue
        counts[str(pid)] = {
            "total_items": int(row.get("total_items") or 0),
            "submitted_items": int(row.get("submitted_items") or 0),
            "pending_items": int(row.get("pending_items") or 0),
        }
    out: list[PairSummaryOut] = []
    async for doc in db[COLLECTION].find({}).sort("slug", 1):
        base = _out(doc)
        c = counts.get(base.id, {})
        out.append(
            PairSummaryOut(
                **base.model_dump(),
                total_items=c.get("total_items", 0),
                submitted_items=c.get("submitted_items", 0),
                pending_items=c.get("pending_items", 0),
            )
        )
    return out


@router.get("", response_model=list[PairOut])
async def list_pairs(_: AdminIdentity = Depends(require_admin)):
    db = get_database()
    out: list[PairOut] = []
    async for doc in db[COLLECTION].find({}).sort("slug", 1):
        out.append(_out(doc))
    return out


@router.post("", response_model=PairOut)
async def create_pair(body: PairCreate, _: AdminIdentity = Depends(require_admin)):
    slug = body.slug.strip().lower().replace(" ", "-")
    if await get_pair_by_slug(slug):
        raise HTTPException(status_code=409, detail="Slug already exists")
    db = get_database()
    doc = {
        "slug": slug,
        "display_name": body.display_name.strip(),
        "audio_folder_source": body.audio_folder_source.strip(),
        "audio_folder_target": body.audio_folder_target.strip(),
        "file_suffix_source": body.file_suffix_source.strip(),
        "file_suffix_target": body.file_suffix_target.strip(),
        "active": body.active,
        "created_at": datetime.now(timezone.utc),
    }
    res = await db[COLLECTION].insert_one(doc)
    doc["_id"] = res.inserted_id
    return _out(doc)


@router.patch("/{pair_id}", response_model=PairOut)
async def update_pair(
    pair_id: str,
    body: PairCreate,
    _: AdminIdentity = Depends(require_admin),
):
    try:
        oid = ObjectId(pair_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pair id") from None
    db = get_database()
    existing = await db[COLLECTION].find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Not found")
    slug = body.slug.strip().lower().replace(" ", "-")
    other = await get_pair_by_slug(slug)
    if other and other["_id"] != oid:
        raise HTTPException(status_code=409, detail="Slug already exists")
    await db[COLLECTION].update_one(
        {"_id": oid},
        {
            "$set": {
                "slug": slug,
                "display_name": body.display_name.strip(),
                "audio_folder_source": body.audio_folder_source.strip(),
                "audio_folder_target": body.audio_folder_target.strip(),
                "file_suffix_source": body.file_suffix_source.strip(),
                "file_suffix_target": body.file_suffix_target.strip(),
                "active": body.active,
            }
        },
    )
    doc = await db[COLLECTION].find_one({"_id": oid})
    assert doc
    return _out(doc)


def _parse_pair_oid(pair_id: str) -> ObjectId:
    try:
        return ObjectId(pair_id.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pair id") from None


@router.delete("/{pair_id}", response_model=LanguagePairDeleteResponse)
async def delete_language_pair(
    pair_id: str,
    clear_data: bool = Query(
        False,
        description="If true, delete all MongoDB and optional R2 data for this pair before removing the pair row",
    ),
    include_r2: bool = Query(
        True,
        description="When clear_data is true, delete R2 objects under the pair slug prefix",
    ),
    _: AdminIdentity = Depends(require_admin),
):
    oid = _parse_pair_oid(pair_id)
    db = get_database()
    pair = await db[COLLECTION].find_one({"_id": oid})
    if not pair:
        raise HTTPException(status_code=404, detail="Language pair not found")

    slug = str(pair.get("slug", ""))
    contrib_n = await count_contributors_for_pair(oid)
    if contrib_n > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Cannot delete: contributors are assigned to this language pair. "
                    "Reassign them to another pair first."
                ),
                "counts": {"contributors": contrib_n},
            },
        )

    counts = await count_pair_data(oid)
    if not clear_data and total_data_count(counts) > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Pair has associated data. Pass clear_data=true to delete all data "
                    "and the pair."
                ),
                "counts": counts,
            },
        )

    deleted_raw: dict[str, int] = {}
    if clear_data:
        deleted_raw = await clear_pair_mongo(oid)
        if include_r2:
            deleted_raw["r2_objects"] = await clear_pair_r2_prefix(slug)

    pair_deleted = await delete_language_pair_document(oid)
    if pair_deleted == 0:
        raise HTTPException(status_code=404, detail="Language pair not found")

    deleted = LanguagePairDeleteCounts(
        speech_parallel=deleted_raw.get("speech_parallel", 0),
        batch_claims=deleted_raw.get("batch_claims", 0),
        batch_completions=deleted_raw.get("batch_completions", 0),
        parallel_text_corpus=deleted_raw.get("parallel_text_corpus", 0),
        text_batch_claims=deleted_raw.get("text_batch_claims", 0),
        text_batch_completions=deleted_raw.get("text_batch_completions", 0),
        parallel_synth_pairs=deleted_raw.get("parallel_synth_pairs", 0),
        gridfs_files=deleted_raw.get("gridfs_files", 0),
        r2_objects=deleted_raw.get("r2_objects", 0),
    )
    return LanguagePairDeleteResponse(
        pair_id=str(oid),
        slug=slug,
        clear_data=clear_data,
        deleted=deleted,
        language_pair_deleted=pair_deleted,
    )
