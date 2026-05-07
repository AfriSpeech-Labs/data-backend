#!/usr/bin/env python3
"""
Copy synth-pairs audio from MongoDB GridFS (synth bucket) to Cloudflare R2.

This migrates documents in ``parallel_synth_pairs`` (or the configured collection)
by uploading English/Swahili WAV bytes to R2 and setting:

- r2_key_source
- r2_key_target

Run from ``backend/`` with venv activated:

  set PYTHONPATH=.
  python scripts/migrate_synth_gridfs_to_r2.py

Requires R2_* env vars and a valid Mongo connection.
"""

from __future__ import annotations

import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


async def _read_gridfs(gridfs, file_id) -> bytes | None:
    try:
        out = await gridfs.open_download_stream(file_id)
        return await out.read()
    except Exception as e:
        print(f"gridfs read error: {e}")
        return None


async def main() -> None:
    from bson import ObjectId

    from app.config import settings
    from app.db import close_db, connect_db, get_database, get_gridfs_synth
    from app.r2_storage import put_object_bytes, r2_enabled
    from app.services.language_pairs import get_pair_by_id, resolve_default_pair, synth_audio_keys

    if not r2_enabled():
        print("R2 is not configured; set R2_ENDPOINT_URL, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY.")
        raise SystemExit(1)

    await connect_db()
    try:
        db = get_database()
        gridfs = get_gridfs_synth()
        coll = db[settings.synth_pairs_collection]

        q = {
            "$or": [
                {"r2_key_source": {"$exists": False}},
                {"r2_key_source": None},
                {"r2_key_source": ""},
                {"r2_key_target": {"$exists": False}},
                {"r2_key_target": None},
                {"r2_key_target": ""},
            ]
        }

        n = 0
        async for doc in coll.find(q).sort("_id", 1):
            oid = doc["_id"]
            id_str = str(oid)

            lp = doc.get("language_pair_id")
            pair = None
            if isinstance(lp, ObjectId):
                pair = await get_pair_by_id(lp)
            if not pair:
                pair = await resolve_default_pair()

            updates: dict = {}

            if not doc.get("r2_key_source"):
                fid_en = doc.get("audio_english")
                if fid_en:
                    data = await _read_gridfs(gridfs, fid_en)
                    if data:
                        key, _ = synth_audio_keys(pair, id_str, "source")
                        await put_object_bytes(key, data)
                        updates["r2_key_source"] = key

            if not doc.get("r2_key_target"):
                fid_sw = doc.get("audio_swahili")
                if fid_sw:
                    data = await _read_gridfs(gridfs, fid_sw)
                    if data:
                        key, _ = synth_audio_keys(pair, id_str, "target")
                        await put_object_bytes(key, data)
                        updates["r2_key_target"] = key

            if updates:
                await coll.update_one({"_id": oid}, {"$set": updates})
                n += 1
                print(f"migrated synth {id_str} fields={list(updates.keys())}")

        print(f"done; updated {n} synth documents")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

