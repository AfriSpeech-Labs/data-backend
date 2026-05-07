"""ZIP export for parallel_synth_pairs."""
import csv
import io
import json
import zipfile
from typing import Literal

from bson import ObjectId
from fastapi.responses import StreamingResponse

from app.config import settings
from app.db import get_database, get_gridfs_synth
from app.services.export_zip_paths import (
    METADATA_CSV,
    METADATA_JSON,
    zip_path_english_wav,
    zip_path_swahili_wav,
)
from app.services.synth_pairs_read import merge_synth_pair_scope, serialize_id

EXPORT_MAX_LIMIT = 500


def _synth_coll():
    return get_database()[settings.synth_pairs_collection]


def _synth_export_query(
    approved_only: bool,
    validation_status: str | None,
    status: Literal["all", "pending", "approved", "rejected"] | None,
) -> dict:
    if status is not None:
        if status == "all":
            return {}
        return {"validation_status": status}
    q: dict = {}
    if approved_only:
        q["validation_status"] = "approved"
    elif validation_status in ("pending", "approved", "rejected"):
        q["validation_status"] = validation_status
    return q


def _synth_export_filename(
    approved_only: bool,
    validation_status: str | None,
    status: Literal["all", "pending", "approved", "rejected"] | None,
) -> str:
    if status is not None:
        return f"synth_pairs_export_{status}.zip"
    if approved_only:
        return "synth_pairs_export_approved.zip"
    if validation_status:
        return f"synth_pairs_export_{validation_status}.zip"
    return "synth_pairs_export.zip"


async def build_synth_export_zip(
    limit: int = 500,
    approved_only: bool = False,
    validation_status: str | None = None,
    *,
    uncapped: bool = False,
    status: Literal["all", "pending", "approved", "rejected"] | None = None,
    pair_oid: ObjectId | None = None,
) -> StreamingResponse:
    if not uncapped:
        if limit < 1 or limit > EXPORT_MAX_LIMIT:
            limit = EXPORT_MAX_LIMIT
    gridfs = get_gridfs_synth()
    q = _synth_export_query(approved_only, validation_status, status)
    q = merge_synth_pair_scope(q, pair_oid)
    coll = _synth_coll().find(q).sort("_id", 1)
    if uncapped:
        cursor = coll
    else:
        cursor = coll.limit(limit)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        metadata_list: list[dict] = []
        async for doc in cursor:
            id_str = serialize_id(doc["_id"])
            en_rel = zip_path_english_wav(id_str)
            sw_rel = zip_path_swahili_wav(id_str)
            try:
                grid_out = await gridfs.open_download_stream(doc["audio_english"])
                en_data = await grid_out.read()
                zf.writestr(en_rel, en_data)
            except Exception:
                en_rel = ""
            try:
                grid_out = await gridfs.open_download_stream(doc["audio_swahili"])
                sw_data = await grid_out.read()
                zf.writestr(sw_rel, sw_data)
            except Exception:
                sw_rel = ""
            metadata_list.append({
                "id": id_str,
                "english_audio_file": en_rel,
                "swahili_audio_file": sw_rel,
                "length_english": doc.get("length_english"),
                "length_swahili": doc.get("length_swahili"),
                "text_english": doc.get("text_english") or "",
                "text_swahili": doc.get("text_swahili") or "",
                "validation_status": doc.get("validation_status", "pending"),
                "tts_english_voice": doc.get("tts_english_voice"),
                "tts_swahili_voice": doc.get("tts_swahili_voice"),
                "tts_swahili_engine": doc.get("tts_swahili_engine", "edge"),
                "gender_sw": doc.get("gender_sw"),
            })
        zf.writestr(METADATA_JSON, json.dumps(metadata_list, indent=2))
        csv_buffer = io.StringIO()
        if metadata_list:
            writer = csv.DictWriter(
                csv_buffer,
                fieldnames=list(metadata_list[0].keys()),
            )
            writer.writeheader()
            writer.writerows(metadata_list)
        zf.writestr(METADATA_CSV, csv_buffer.getvalue())
    buffer.seek(0)
    name = _synth_export_filename(approved_only, validation_status, status)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
