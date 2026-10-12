"""One-time (idempotent) database provisioning.

Creates indexes, seeds the default language pair, and backfills ``pair_id`` on
legacy documents. This logic used to run on every app startup inside
``connect_db()``; it was moved here so serverless cold starts stay fast and we
don't issue repeated writes from every function instance.

Run after setting MONGODB_URI (and the usual env), from ``backend/``:

    set PYTHONPATH=.            # Windows PowerShell: $env:PYTHONPATH="."
    python scripts/init_db.py

Safe to re-run: index creation and seeding are idempotent, and backfills only
touch documents missing the field.
"""
import asyncio
import os
import sys

# Allow running as `python scripts/init_db.py` from the backend/ directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


async def init_db() -> None:
    from app.config import settings
    from app.db import close_db, get_database
    from app.services.language_pairs import (
        COLLECTION as LP_COL,
        ensure_default_pair_seeded,
        get_default_pair_document,
    )

    db = get_database()

    print("Creating indexes...")
    await db["admin_users"].create_index("email", unique=True)

    coll = settings.synth_pairs_collection
    await db[coll].create_index([("validation_status", 1), ("created_at", -1)])

    await ensure_default_pair_seeded()
    await db[LP_COL].create_index("slug", unique=True)
    await db["contributor_users"].create_index("phone_e164", unique=True)

    tcoll = settings.parallel_text_corpus_collection
    await db[tcoll].create_index([("language_pair_id", 1), ("_id", 1)])
    await db[tcoll].create_index([("language_pair_id", 1), ("task_type", 1), ("_id", 1)])
    await db[tcoll].create_index(
        [("language_pair_id", 1), ("task_type", 1), ("status", 1)]
    )
    # HF text sync: seek submitted+unsynced slice per pair instead of scanning.
    await db[tcoll].create_index(
        [("language_pair_id", 1), ("task_type", 1), ("status", 1), ("hf_text_synced_at", 1)]
    )

    await db["speech_parallel"].create_index([("pair_id", 1), ("_id", 1)])
    # HF speech sync: seek submitted+unsynced slice per pair instead of scanning.
    await db["speech_parallel"].create_index(
        [("pair_id", 1), ("status", 1), ("hf_synced_at", 1)]
    )
    await db["batch_claims"].create_index(
        [("pair_id", 1), ("batch_index", 1)], unique=True
    )
    await db["batch_completions"].create_index(
        [("pair_id", 1), ("actor_ref", 1), ("batch_index", 1)], unique=True
    )
    await db["text_batch_claims"].create_index(
        [("pair_id", 1), ("batch_index", 1)], unique=True
    )
    await db["text_batch_completions"].create_index(
        [("pair_id", 1), ("actor_ref", 1), ("batch_index", 1)], unique=True
    )

    print("Backfilling pair_id on legacy documents...")
    dp = await get_default_pair_document()
    if not dp:
        raise RuntimeError("No default language pair found after seeding.")
    pid = dp["_id"]

    r1 = await db["speech_parallel"].update_many(
        {"pair_id": {"$exists": False}}, {"$set": {"pair_id": pid}}
    )
    r2 = await db["batch_claims"].update_many(
        {"pair_id": {"$exists": False}}, {"$set": {"pair_id": pid}}
    )
    r3 = await db[coll].update_many(
        {"language_pair_id": {"$exists": False}}, {"$set": {"language_pair_id": pid}}
    )
    print(
        f"Backfilled: speech_parallel={r1.modified_count}, "
        f"batch_claims={r2.modified_count}, {coll}={r3.modified_count}"
    )

    await close_db()
    print("init_db complete.")


if __name__ == "__main__":
    asyncio.run(init_db())
