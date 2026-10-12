"""MongoDB and GridFS access for a serverless-friendly runtime.

Design notes:
- The Motor client is created lazily and cached at module scope. On serverless
  platforms (Vercel) each warm invocation reuses the cached client; cold starts
  pay the connection cost once. We never open the client at import time.
- Index creation, default-pair seeding, and legacy backfill are NOT done here.
  Those are one-time provisioning steps; run ``python scripts/init_db.py``.
  Keeping them off the request path makes cold starts fast and avoids repeated
  writes on every function instance.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from app.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    """Return a cached Motor client, creating it on first use."""
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_uri)
    return _client


def get_database():
    return get_client()[settings.database_name]


def get_gridfs() -> AsyncIOMotorGridFSBucket:
    return AsyncIOMotorGridFSBucket(get_database(), bucket_name=settings.gridfs_bucket)


def get_gridfs_synth() -> AsyncIOMotorGridFSBucket:
    return AsyncIOMotorGridFSBucket(
        get_database(), bucket_name=settings.synth_pairs_gridfs_bucket
    )


async def connect_db() -> None:
    """Validate prod storage requirements and warm the client.

    Kept for compatibility with the FastAPI startup event and local/other hosts.
    It no longer creates indexes, seeds, or backfills — see scripts/init_db.py.
    """
    if settings.require_r2:
        from app.r2_storage import r2_enabled

        if not r2_enabled():
            raise RuntimeError(
                "Cloudflare R2 is required in this environment. Set R2_ENDPOINT_URL, "
                "R2_BUCKET_NAME, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY "
                "(or set REQUIRE_R2=false for local GridFS-only dev)."
            )
    # Touch the client so a bad MONGODB_URI fails fast where startup events run.
    get_client()


async def close_db() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
