"""S3-compatible object storage (MinIO) — replaces Google Cloud Storage.

All blob references are stored as ``s3://bucket/key`` URIs. Helpers here
parse that scheme (and tolerate legacy ``gs://`` URIs or bare keys) so call
sites never construct buckets/clients themselves.
"""

import asyncio
import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)

_SCHEMES = ("s3://", "gs://")


@lru_cache
def _client():
    """Return a cached, thread-safe boto3 S3 client pointed at MinIO."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        region_name=settings.s3_region,
        # MinIO needs path-style addressing (no per-bucket DNS) + SigV4.
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def parse_uri(uri: str) -> tuple[str, str]:
    """Split a storage reference into ``(bucket, key)``.

    Accepts ``s3://bucket/key``, legacy ``gs://bucket/key``, or a bare key
    (in which case the configured documents bucket is assumed).
    """
    for scheme in _SCHEMES:
        if uri.startswith(scheme):
            bucket, _, key = uri[len(scheme) :].partition("/")
            return bucket, key
    return settings.s3_bucket_documents, uri


def _upload_sync(key: str, data: bytes, content_type: str) -> str:
    bucket = settings.s3_bucket_documents
    _client().put_object(
        Bucket=bucket, Key=key, Body=data, ContentType=content_type
    )
    return f"s3://{bucket}/{key}"


def _download_sync(uri: str) -> bytes:
    bucket, key = parse_uri(uri)
    resp = _client().get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


async def upload(key: str, data: bytes, content_type: str) -> str:
    """Upload bytes and return the ``s3://bucket/key`` URI."""
    return await asyncio.to_thread(_upload_sync, key, data, content_type)


async def download(uri: str) -> bytes:
    """Download an object by its storage URI (or bare key)."""
    return await asyncio.to_thread(_download_sync, uri)


def delete_objects(uris: list[str]) -> tuple[int, int]:
    """Delete objects best-effort. Returns ``(deleted, failed)``.

    Each entry may be a full ``s3://``/``gs://`` URI or a bare key; the
    bucket and key are parsed per-entry so mixed/legacy references work.
    """
    if not uris:
        return 0, 0
    try:
        client = _client()
    except Exception:
        logger.exception("Failed to initialize S3 client")
        return 0, len(uris)

    deleted, failed = 0, 0
    for uri in uris:
        bucket, key = parse_uri(uri)
        try:
            client.delete_object(Bucket=bucket, Key=key)
            deleted += 1
        except Exception:
            logger.warning("Failed to delete object: %s", uri)
            failed += 1
    return deleted, failed
