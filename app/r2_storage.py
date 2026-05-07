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
