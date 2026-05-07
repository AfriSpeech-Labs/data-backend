"""Read / stream parallel_synth_pairs (synth GridFS bucket)."""
import io

from bson import ObjectId
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config import settings
from app.db import get_database, get_gridfs_synth
from app.models import SynthPairItemResponse, SynthPairListResponse, SynthPairStatsResponse


def _coll():
    return get_database()[settings.synth_pairs_collection]


def serialize_id(oid: ObjectId) -> str:
    return str(oid)


def merge_synth_pair_scope(
    base_match: dict, pair_oid: ObjectId | None
) -> dict:
    """When a pair is selected, include rows tagged to that pair and legacy rows
    without ``language_pair_id`` (shared until imports are tagged per pair).
    """
    if pair_oid is None:
        return dict(base_match)
    scope = {
        "$or": [
            {"language_pair_id": pair_oid},
            {"language_pair_id": {"$exists": False}},
        ]
    }
    if not base_match:
        return scope
    return {"$and": [base_match, scope]}


async def get_synth_doc(id: str) -> dict:
    try:
        oid = ObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    doc = await _coll().find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return doc


async def list_synth_pairs(
    page: int = 1,
    limit: int = 20,
    validation_status: str | None = None,
    pair_oid: ObjectId | None = None,
) -> SynthPairListResponse:
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 20
    q: dict = {}
    if validation_status in ("pending", "approved", "rejected"):
        q["validation_status"] = validation_status
    q = merge_synth_pair_scope(q, pair_oid)
    skip = (page - 1) * limit
    total = await _coll().count_documents(q)
    cursor = _coll().find(q).skip(skip).limit(limit).sort("_id", 1)
    items = []
    async for doc in cursor:
        items.append(
            SynthPairItemResponse(
                id=serialize_id(doc["_id"]),
                length_english=float(doc.get("length_english") or 0),
                length_swahili=float(doc.get("length_swahili") or 0),
                text_english=doc.get("text_english"),
                text_swahili=doc.get("text_swahili"),
                validation_status=doc.get("validation_status", "pending"),
                tts_english_voice=doc.get("tts_english_voice"),
                tts_swahili_voice=doc.get("tts_swahili_voice"),
                tts_swahili_engine=doc.get("tts_swahili_engine", "edge"),
                gender_sw=doc.get("gender_sw"),
            )
        )
    return SynthPairListResponse(items=items, total=total, page=page, limit=limit)


async def get_synth_pair_stats(
    validation_status: str | None = None,
    pair_oid: ObjectId | None = None,
) -> SynthPairStatsResponse:
    """Count + sum of stored clip lengths (seconds), optionally filtered like list_synth_pairs."""
    pipeline: list[dict] = []
    match_q: dict = {}
    if validation_status in ("pending", "approved", "rejected"):
        match_q["validation_status"] = validation_status
    match_q = merge_synth_pair_scope(match_q, pair_oid)
    pipeline.append({"$match": match_q})
    pipeline.append(
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "sum_en": {"$sum": {"$ifNull": ["$length_english", 0]}},
                "sum_sw": {"$sum": {"$ifNull": ["$length_swahili", 0]}},
            }
        }
    )
    cursor = _coll().aggregate(pipeline)
    async for doc in cursor:
        return SynthPairStatsResponse(
            total=int(doc.get("total") or 0),
            total_english_seconds=float(doc.get("sum_en") or 0),
            total_swahili_seconds=float(doc.get("sum_sw") or 0),
        )
    return SynthPairStatsResponse(
        total=0,
        total_english_seconds=0.0,
        total_swahili_seconds=0.0,
    )


async def stream_english(id: str) -> StreamingResponse:
    doc = await get_synth_doc(id)
    gridfs = get_gridfs_synth()
    file_id = doc.get("audio_english")
    if not file_id:
        raise HTTPException(status_code=404, detail="English audio not found")
    try:
        grid_out = await gridfs.open_download_stream(file_id)
        data = await grid_out.read()
    except Exception:
        raise HTTPException(status_code=404, detail="Audio file not found")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=english.wav"},
    )


async def stream_swahili(id: str) -> StreamingResponse:
    doc = await get_synth_doc(id)
    gridfs = get_gridfs_synth()
    file_id = doc.get("audio_swahili")
    if not file_id:
        raise HTTPException(status_code=404, detail="Swahili audio not found")
    try:
        grid_out = await gridfs.open_download_stream(file_id)
        data = await grid_out.read()
    except Exception:
        raise HTTPException(status_code=404, detail="Audio file not found")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=swahili.wav"},
    )
