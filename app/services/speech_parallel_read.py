"""Shared read/export logic for speech_parallel collection (public + admin routes)."""
import csv
import io
import json
import zipfile
from typing import Literal

from bson import ObjectId
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.db import get_database, get_gridfs
from app.services.export_zip_paths import (
    METADATA_CSV,
    METADATA_JSON,
    zip_path_english_wav,
    zip_path_swahili_wav,
    zip_paths_for_pair,
)
from app.models import (
    AudioDurationStatsResponse,
    AudioStatsResponse,
    PaginatedListResponse,
    SpeechItemResponse,
)

COLLECTION = "speech_parallel"
EXPORT_MAX_LIMIT = 500


def serialize_id(oid: ObjectId) -> str:
    return str(oid)


async def _pair_for_doc(doc: dict) -> dict | None:
    from app.services.language_pairs import get_pair_by_id, get_default_pair_document

    pid = doc.get("pair_id")
    if pid is not None:
        p = await get_pair_by_id(pid)
        if p:
            return p
    return await get_default_pair_document()


async def load_source_wav_bytes(doc: dict) -> bytes | None:
    from app.r2_storage import get_object_bytes, r2_enabled

    key = doc.get("r2_key_source")
    if key and r2_enabled():
        try:
            return await get_object_bytes(key)
        except Exception:
            pass
    fid = doc.get("audio_english")
    if fid is not None:
        gridfs = get_gridfs()
        try:
            grid_out = await gridfs.open_download_stream(fid)
            return await grid_out.read()
        except Exception:
            return None
    return None


async def load_target_wav_bytes(doc: dict) -> bytes | None:
    from app.r2_storage import get_object_bytes, r2_enabled

    key = doc.get("r2_key_target")
    if key and r2_enabled():
        try:
            return await get_object_bytes(key)
        except Exception:
            pass
    fid = doc.get("audio_swahili")
    if fid is not None:
        gridfs = get_gridfs()
        try:
            grid_out = await gridfs.open_download_stream(fid)
            return await grid_out.read()
        except Exception:
            return None
    return None


async def get_speech_doc(id: str) -> dict:
    db = get_database()
    try:
        oid = ObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id") from None
    doc = await db[COLLECTION].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return doc


def _pair_filter(pair_id: str | None) -> dict:
    if not pair_id:
        return {}
    try:
        oid = ObjectId(pair_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pair_id") from None
    return {"pair_id": oid}


async def fetch_audio_stats(pair_id: str | None = None) -> AudioStatsResponse:
    db = get_database()
    flt = _pair_filter(pair_id)
    total = await db[COLLECTION].count_documents(flt)
    submitted = await db[COLLECTION].count_documents({**flt, "status": "submitted"})
    pending = total - submitted
    return AudioStatsResponse(total=total, submitted=submitted, pending=pending)


async def fetch_audio_stats_durations(pair_id: str | None = None) -> AudioDurationStatsResponse:
    db = get_database()
    flt = _pair_filter(pair_id)
    match_stage = {"$match": flt} if flt else {"$match": {}}
    pipeline = [
        match_stage,
        {
            "$group": {
                "_id": None,
                "total_english_seconds": {"$sum": {"$ifNull": ["$length_english", 0]}},
                "total_english_submitted_seconds": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "submitted"]},
                            {"$ifNull": ["$length_english", 0]},
                            0,
                        ]
                    }
                },
                "total_english_pending_seconds": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "pending"]},
                            {"$ifNull": ["$length_english", 0]},
                            0,
                        ]
                    }
                },
                "total_swahili_submitted_seconds": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "submitted"]},
                            {"$ifNull": ["$length_swahili", 0]},
                            0,
                        ]
                    }
                },
            }
        },
    ]
    cursor = db[COLLECTION].aggregate(pipeline)
    rows = await cursor.to_list(length=1)
    if not rows:
        en_sec = en_sub = en_pend = sw_sec = 0.0
    else:
        r = rows[0]
        en_sec = float(r.get("total_english_seconds") or 0)
        en_sub = float(r.get("total_english_submitted_seconds") or 0)
        en_pend = float(r.get("total_english_pending_seconds") or 0)
        sw_sec = float(r.get("total_swahili_submitted_seconds") or 0)
    return AudioDurationStatsResponse(
        total_english_seconds=en_sec,
        total_english_submitted_seconds=en_sub,
        total_english_pending_seconds=en_pend,
        total_swahili_submitted_seconds=sw_sec,
        total_english_hours=en_sec / 3600.0,
        total_english_submitted_hours=en_sub / 3600.0,
        total_english_pending_hours=en_pend / 3600.0,
        total_swahili_submitted_hours=sw_sec / 3600.0,
    )


async def list_audio_paginated(
    page: int = 1,
    limit: int = 20,
    pair_id: str | None = None,
) -> PaginatedListResponse:
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 20
    db = get_database()
    flt = _pair_filter(pair_id)
    total = await db[COLLECTION].count_documents(flt)
    skip = (page - 1) * limit
    cursor = db[COLLECTION].find(flt).skip(skip).limit(limit).sort("_id", 1)
    items = []
    async for doc in cursor:
        items.append(
            SpeechItemResponse(
                id=serialize_id(doc["_id"]),
                length_english=doc.get("length_english", 0),
                length_swahili=doc.get("length_swahili"),
                status=doc.get("status", "pending"),
                text_english=doc.get("text_english"),
                text_swahili=doc.get("text_swahili"),
            )
        )
    return PaginatedListResponse(items=items, total=total, page=page, limit=limit)


def _parallel_export_query(
    submitted_only: bool,
    status: Literal["all", "pending", "submitted"] | None,
    pair_id: str | None,
) -> dict:
    base: dict = {}
    if pair_id:
        try:
            base["pair_id"] = ObjectId(pair_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pair_id") from None
    if status is not None:
        if status == "all":
            return base
        return {**base, "status": status}
    return {**base, **({"status": "submitted"} if submitted_only else {})}


def _parallel_export_filename(
    submitted_only: bool,
    status: Literal["all", "pending", "submitted"] | None,
) -> str:
    if status is not None:
        if status == "all":
            return "speech_export_all.zip"
        return f"speech_export_{status}.zip"
    return "speech_export_submitted.zip" if submitted_only else "speech_export.zip"


async def build_export_response(
    limit: int = 500,
    submitted_only: bool = False,
    *,
    status: Literal["all", "pending", "submitted"] | None = None,
    uncapped: bool = False,
    pair_id: str | None = None,
) -> StreamingResponse:
    """Export speech_parallel docs as ZIP."""
    filter_status = status
    if not uncapped:
        if limit < 1 or limit > EXPORT_MAX_LIMIT:
            limit = EXPORT_MAX_LIMIT
    db = get_database()
    query = _parallel_export_query(submitted_only, filter_status, pair_id)
    coll = db[COLLECTION].find(query).sort("_id", 1)
    if uncapped:
        cursor = coll
    else:
        cursor = coll.limit(limit)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        metadata_list: list[dict] = []
        async for doc in cursor:
            id_str = serialize_id(doc["_id"])
            length_english = doc.get("length_english")
            length_swahili = doc.get("length_swahili")
            text_english = doc.get("text_english")
            text_swahili = doc.get("text_swahili")
            doc_status = doc.get("status", "pending")
            has_swahili = bool(doc.get("audio_swahili") or doc.get("r2_key_target"))
            pair = await _pair_for_doc(doc)

            if pair:
                en_rel, sw_rel_base = zip_paths_for_pair(pair, id_str)
            else:
                en_rel = zip_path_english_wav(id_str)
                sw_rel_base = zip_path_swahili_wav(id_str)

            has_english_audio = False
            en_data = await load_source_wav_bytes(doc)
            if en_data:
                zf.writestr(en_rel, en_data)
                has_english_audio = True
            else:
                en_rel = ""

            sw_rel = sw_rel_base if has_swahili else ""
            if has_swahili:
                sw_data = await load_target_wav_bytes(doc)
                if sw_data:
                    zf.writestr(sw_rel, sw_data)
                else:
                    has_swahili = False
                    sw_rel = ""

            metadata_list.append({
                "id": id_str,
                "pair_slug": pair.get("slug") if pair else "",
                "english_audio_file": en_rel,
                "swahili_audio_file": sw_rel,
                "length_english": length_english,
                "length_swahili": length_swahili,
                "text_english": text_english or "",
                "text_swahili": text_swahili or "",
                "status": doc_status,
                "has_english_audio": has_english_audio,
                "has_swahili_audio": has_swahili,
                "submitted_by_user_id": str(doc["submitted_by_user_id"])
                if doc.get("submitted_by_user_id") is not None else "",
            })
        zf.writestr(METADATA_JSON, json.dumps(metadata_list, indent=2))
        csv_buffer = io.StringIO()
        if metadata_list:
            writer = csv.DictWriter(
                csv_buffer,
                fieldnames=[
                    "id", "pair_slug", "english_audio_file", "swahili_audio_file",
                    "length_english", "length_swahili", "text_english", "text_swahili",
                    "status", "has_english_audio", "has_swahili_audio",
                    "submitted_by_user_id",
                ],
            )
            writer.writeheader()
            writer.writerows(metadata_list)
        zf.writestr(METADATA_CSV, csv_buffer.getvalue())
    buffer.seek(0)
    filename = _parallel_export_filename(submitted_only, filter_status)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def stream_english_response(id: str, doc: dict | None = None) -> StreamingResponse:
    d = doc if doc is not None else await get_speech_doc(id)
    data = await load_source_wav_bytes(d)
    if not data:
        raise HTTPException(status_code=404, detail="English audio not found")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=english.wav"},
    )


async def stream_swahili_response(id: str, doc: dict | None = None) -> StreamingResponse:
    d = doc if doc is not None else await get_speech_doc(id)
    data = await load_target_wav_bytes(d)
    if not data:
        raise HTTPException(
            status_code=404,
            detail="Swahili audio not found (not submitted yet)",
        )
    return StreamingResponse(
        io.BytesIO(data),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=swahili.wav"},
    )
