"""MongoDB and GridFS connection."""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from app.config import settings

client: AsyncIOMotorClient | None = None
gridfs_bucket: AsyncIOMotorGridFSBucket | None = None
gridfs_synth_bucket: AsyncIOMotorGridFSBucket | None = None


def get_database():
    if client is None:
        raise RuntimeError("Database client not initialized")
    return client[settings.database_name]


def get_gridfs():
    if gridfs_bucket is None:
        raise RuntimeError("GridFS bucket not initialized")
    return gridfs_bucket


def get_gridfs_synth():
    if gridfs_synth_bucket is None:
        raise RuntimeError("Synth GridFS bucket not initialized")
    return gridfs_synth_bucket


async def connect_db():
    global client, gridfs_bucket, gridfs_synth_bucket
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.database_name]
    gridfs_bucket = AsyncIOMotorGridFSBucket(db, bucket_name=settings.gridfs_bucket)
    gridfs_synth_bucket = AsyncIOMotorGridFSBucket(
        db, bucket_name=settings.synth_pairs_gridfs_bucket
    )
    await db["admin_users"].create_index("email", unique=True)
    coll = settings.synth_pairs_collection
    await db[coll].create_index([("validation_status", 1), ("created_at", -1)])

    from app.services.language_pairs import (
        COLLECTION as LP_COL,
        ensure_default_pair_seeded,
        get_default_pair_document,
    )

    await ensure_default_pair_seeded()
    await db[LP_COL].create_index("slug", unique=True)
    await db["contributor_users"].create_index("phone_e164", unique=True)
    try:
        await db["speech_parallel"].create_index([("pair_id", 1), ("_id", 1)])
        await db["batch_claims"].create_index(
            [("pair_id", 1), ("batch_index", 1)], unique=True
        )
        await db["batch_completions"].create_index(
            [("pair_id", 1), ("actor_ref", 1), ("batch_index", 1)],
            unique=True,
        )
    except Exception:
        pass

    # Backfill pair_id for legacy docs (single default pair).
    try:
        dp = await get_default_pair_document()
        if not dp:
            raise RuntimeError("no pair")
        pid = dp["_id"]
        await db["speech_parallel"].update_many(
            {"pair_id": {"$exists": False}},
            {"$set": {"pair_id": pid}},
        )
        await db["batch_claims"].update_many(
            {"pair_id": {"$exists": False}},
            {"$set": {"pair_id": pid}},
        )
    except Exception:
        pass


async def close_db():
    global client, gridfs_bucket, gridfs_synth_bucket
    if client:
        client.close()
        client = None
    gridfs_bucket = None
    gridfs_synth_bucket = None
