"""Async Cloudflare R2 (S3 API) read/write."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    pass


def r2_enabled() -> bool:
    s = settings
    return bool(
        (s.r2_endpoint_url or "").strip()
        and (s.r2_bucket_name or "").strip()
        and (s.r2_access_key_id or "").strip()
        and (s.r2_secret_access_key or "").strip()
    )


async def put_object_bytes(key: str, body: bytes, content_type: str = "audio/wav") -> None:
    import aioboto3

    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url.strip(),
        aws_access_key_id=settings.r2_access_key_id.strip(),
        aws_secret_access_key=settings.r2_secret_access_key.strip(),
        region_name="auto",
    ) as client:
        await client.put_object(
            Bucket=settings.r2_bucket_name.strip(),
            Key=key,
            Body=body,
            ContentType=content_type,
        )


async def get_object_bytes(key: str) -> bytes:
    import aioboto3

    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url.strip(),
        aws_access_key_id=settings.r2_access_key_id.strip(),
        aws_secret_access_key=settings.r2_secret_access_key.strip(),
        region_name="auto",
    ) as client:
        resp = await client.get_object(
            Bucket=settings.r2_bucket_name.strip(),
            Key=key,
        )
        async with resp["Body"] as stream:
            return await stream.read()


async def delete_objects_by_prefix(prefix: str) -> int:
    """Delete all objects whose keys start with ``prefix``. Returns count deleted."""
    if not r2_enabled():
        return 0
    pfx = (prefix or "").strip()
    if not pfx:
        return 0

    import aioboto3

    bucket = settings.r2_bucket_name.strip()
    deleted = 0
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url.strip(),
        aws_access_key_id=settings.r2_access_key_id.strip(),
        aws_secret_access_key=settings.r2_secret_access_key.strip(),
        region_name="auto",
    ) as client:
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=pfx):
            contents = page.get("Contents") or []
            if not contents:
                continue
            keys = [{"Key": obj["Key"]} for obj in contents if obj.get("Key")]
            for i in range(0, len(keys), 1000):
                batch = keys[i : i + 1000]
                if not batch:
                    continue
                resp = await client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )
                deleted += len(resp.get("Deleted") or batch)
    return deleted
