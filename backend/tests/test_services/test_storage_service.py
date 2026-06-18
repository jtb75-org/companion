"""Unit tests for services/storage_service.py — S3/MinIO blob storage."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.config import settings
from app.services import storage_service

# ---------------------------------------------------------------------------
# parse_uri
# ---------------------------------------------------------------------------


def test_parse_uri_s3_scheme():
    bucket, key = storage_service.parse_uri("s3://my-bucket/path/to/obj.jpg")
    assert bucket == "my-bucket"
    assert key == "path/to/obj.jpg"


def test_parse_uri_legacy_gcs_scheme():
    """Legacy gs:// references (pre-migration rows) must still parse."""
    bucket, key = storage_service.parse_uri("gs://old-bucket/scans/a/b.png")
    assert bucket == "old-bucket"
    assert key == "scans/a/b.png"


def test_parse_uri_bare_key_uses_default_bucket():
    bucket, key = storage_service.parse_uri("scans/user/doc/page.jpg")
    assert bucket == settings.s3_bucket_documents
    assert key == "scans/user/doc/page.jpg"


# ---------------------------------------------------------------------------
# upload / download / delete (boto3 client mocked)
# ---------------------------------------------------------------------------


async def test_upload_returns_s3_uri():
    fake = MagicMock()
    with patch.object(storage_service, "_client", return_value=fake):
        uri = await storage_service.upload(
            "scans/u/d/page_000.jpg", b"bytes", "image/jpeg"
        )
    assert uri == f"s3://{settings.s3_bucket_documents}/scans/u/d/page_000.jpg"
    fake.put_object.assert_called_once_with(
        Bucket=settings.s3_bucket_documents,
        Key="scans/u/d/page_000.jpg",
        Body=b"bytes",
        ContentType="image/jpeg",
    )


async def test_download_reads_body():
    fake = MagicMock()
    fake.get_object.return_value = {"Body": MagicMock(read=lambda: b"data")}
    with patch.object(storage_service, "_client", return_value=fake):
        data = await storage_service.download("s3://b/k.bin")
    assert data == b"data"
    fake.get_object.assert_called_once_with(Bucket="b", Key="k.bin")


def test_delete_objects_counts_and_parses_mixed_refs():
    fake = MagicMock()
    fake.delete_object.side_effect = [None, RuntimeError("nope"), None]
    with patch.object(storage_service, "_client", return_value=fake):
        deleted, failed = storage_service.delete_objects(
            ["s3://b/one", "s3://b/two", "gs://b/three"]
        )
    assert (deleted, failed) == (2, 1)


def test_delete_objects_empty_is_noop():
    assert storage_service.delete_objects([]) == (0, 0)
