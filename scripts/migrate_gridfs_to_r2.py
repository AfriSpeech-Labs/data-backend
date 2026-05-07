"""
Copy speech_parallel audio from MongoDB GridFS to Cloudflare R2.

Run from ``backend/`` with venv activated:
  set PYTHONPATH=.
  python scripts/migrate_gridfs_to_r2.py

Requires ``R2_*`` env vars and existing ``language_pairs`` / ``pair_id`` on documents.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys

# Ensure backend package is importable when run as script
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


async def main() -> None:
    from bson import ObjectId

    from app.config import settings
    from app.db import connect_db, close_db, get_database, get_gridfs
    from app.r2_storage import put_object_bytes, r2_enabled
    from app.services.language_pairs import get_pair_by_id, pair_audio_keys

    if not r2_enabled():
        print("R2 is not configured; set R2_ENDPOINT_URL, R2_BUCKET_NAME, keys.")
        sys.exit(1)

    await connect_db()
    try:
        db = get_database()
        gridfs = get_gridfs()
        coll = db["speech_parallel"]
        cursor = coll.find(
            {
                "$or": [
                    {"audio_english": {"$exists": True, "$ne": None}},
                    {"audio_swahili": {"$exists": True, "$ne": None}},
                ]
            }
        )
        n = 0
        async for doc in cursor:
            oid = doc["_id"]
            id_str = str(oid)
            pair_id = doc.get("pair_id")
            if not pair_id:
                print(f"skip {id_str}: no pair_id")
                continue
            pair = await get_pair_by_id(pair_id)
            if not pair:
                print(f"skip {id_str}: pair not found")
                continue
            updates: dict = {}
            if doc.get("audio_english") and not doc.get("r2_key_source"):
                data = await read_gridfs(gridfs, doc["audio_english"])
                if data:
                    key, _ = pair_audio_keys(pair, id_str, "source")
                    await put_object_bytes(key, data)
                    updates["r2_key_source"] = key
            if doc.get("audio_swahili") and not doc.get("r2_key_target"):
                data = await read_gridfs(gridfs, doc["audio_swahili"])
                if data:
                    key, _ = pair_audio_keys(pair, id_str, "target")
                    await put_object_bytes(key, data)
                    updates["r2_key_target"] = key
            if updates:
                await coll.update_one({"_id": oid}, {"$set": updates})
                n += 1
                print(f"migrated {id_str} fields={list(updates.keys())}")
        print(f"done; updated {n} documents")
    finally:
        await close_db()


async def read_gridfs(gridfs, file_id) -> bytes | None:
    try:
        out = await gridfs.open_download_stream(file_id)
        return await out.read()
    except Exception as e:
        print(f"gridfs read error: {e}")
        return None


if __name__ == "__main__":
    asyncio.run(main())
