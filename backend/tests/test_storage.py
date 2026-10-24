from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from botocore.exceptions import ClientError

from notely.providers.storage import (
    ObjectNotFoundError, ObjectStorageError, S3ObjectStorage, validate_s3_endpoint,
)
from notely.providers.storage_migration import (
    StorageMigrationError, copy_verified, object_key,
)
from scripts.migrate_minio_to_s3 import DocumentRef, plan_objects


class MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}

    def put_object(
        self, *, Bucket: str, Key: str, Body: BytesIO,
        Metadata: dict[str, str] | None = None, **_: object,
    ) -> None:
        self.objects[(Bucket, Key)] = Body.read()
        self.metadata[(Bucket, Key)] = Metadata or {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject"
            ) from exc
        return {"Body": BytesIO(data), "Metadata": self.metadata.get((Bucket, Key), {})}


@pytest.mark.asyncio
async def test_s3_provider_round_trip_and_missing_object() -> None:
    client = MemoryS3()
    storage = S3ObjectStorage(
        endpoint_url="https://example.invalid", access_key_id="key",
        secret_access_key="secret", bucket="notely-documents", client=client,
    )
    content = BytesIO(b"%PDF-1.7\nimportant")
    sha256 = hashlib.sha256(content.getvalue()).hexdigest()
    key = f"documents/{sha256[:2]}/{sha256}.pdf"
    uri = await storage.put_pdf(
        object_name=key, content=content, length=len(content.getvalue()),
        sha256=sha256, original_filename="paper.pdf",
    )
    assert uri == f"s3://notely-documents/{key}"
    assert content.tell() == 0
    assert b"".join([part async for part in storage.open_pdf(storage_uri=uri)]) == content.getvalue()
    with pytest.raises(ObjectNotFoundError):
        _ = [part async for part in storage.open_pdf(storage_uri="s3://notely-documents/missing")]
    with pytest.raises(ObjectNotFoundError):
        _ = [part async for part in storage.open_pdf(storage_uri="s3://other-bucket/file")]


@pytest.mark.asyncio
async def test_s3_provider_rejects_missing_or_insecure_configuration() -> None:
    for endpoint in (
        "", "http://example.invalid", "https://user:pass@example.invalid",
        "https://example.invalid/other", "https://example.invalid/?token=x",
        "https://example.invalid:bad", "https://[invalid",
    ):
        storage = S3ObjectStorage(
            endpoint_url=endpoint, access_key_id="key", secret_access_key="secret",
            bucket="notely-documents",
        )
        with pytest.raises(ObjectStorageError):
            await storage.put_pdf(
                object_name="documents/test.pdf", content=BytesIO(b"%PDF-"),
                length=5, sha256="0" * 64, original_filename="test.pdf",
            )


def test_s3_endpoint_validation_allows_local_http_and_remote_https() -> None:
    assert validate_s3_endpoint("http://127.0.0.1:19000") == "http://127.0.0.1:19000"
    assert validate_s3_endpoint("https://s3.example.invalid") == "https://s3.example.invalid"


def test_migration_verifies_source_and_destination_and_can_resume() -> None:
    source, destination = MemoryS3(), MemoryS3()
    content = b"%PDF-1.7\nimportant"
    sha256 = hashlib.sha256(content).hexdigest()
    key = f"documents/{sha256[:2]}/{sha256}.pdf"
    source.objects[("notely-documents", key)] = content
    source.metadata[("notely-documents", key)] = {"original-filename": "paper.pdf"}
    assert object_key(f"s3://notely-documents/{key}", "notely-documents", sha256) == key
    assert copy_verified(
        source=source, destination=destination, source_bucket="notely-documents", destination_bucket="notely",
        key=key, sha256=sha256, dry_run=True,
    ) == ("would_copy", len(content))
    assert destination.objects == {}
    assert copy_verified(
        source=source, destination=destination, source_bucket="notely-documents", destination_bucket="notely",
        key=key, sha256=sha256,
    ) == ("copied", len(content))
    assert destination.metadata[("notely", key)] == {
        "original-filename": "paper.pdf", "sha256": sha256,
    }
    assert copy_verified(
        source=source, destination=destination, source_bucket="notely-documents", destination_bucket="notely",
        key=key, sha256=sha256,
    ) == ("already_verified", len(content))
    destination.objects[("notely", key)] = b"corrupted"
    with pytest.raises(StorageMigrationError, match="SHA-256"):
        copy_verified(
            source=source, destination=destination, source_bucket="notely-documents", destination_bucket="notely",
            key=key, sha256=sha256,
        )
    assert destination.objects[("notely", key)] == b"corrupted"


def test_migration_refuses_incorrect_source_or_document_reference() -> None:
    source, destination = MemoryS3(), MemoryS3()
    sha256 = hashlib.sha256(b"expected").hexdigest()
    key = f"documents/{sha256[:2]}/{sha256}.pdf"
    source.objects[("notely-documents", key)] = b"wrong"
    with pytest.raises(StorageMigrationError, match="SHA-256"):
        copy_verified(
            source=source, destination=destination, source_bucket="notely-documents", destination_bucket="notely",
            key=key, sha256=sha256,
        )
    assert destination.objects == {}
    with pytest.raises(StorageMigrationError, match="URI"):
        object_key("s3://another-bucket/file.pdf", "notely-documents", sha256)


def test_migration_plan_accepts_legacy_and_activated_references() -> None:
    from uuid import uuid4

    sha256 = hashlib.sha256(b"pdf").hexdigest()
    key = f"documents/{sha256[:2]}/{sha256}.pdf"
    old = DocumentRef(uuid4(), sha256, f"s3://notely-documents/{key}")
    active = DocumentRef(uuid4(), sha256, f"s3://notely/{key}")
    planned = plan_objects([old, active], "notely-documents", "notely")
    assert [item.needs_copy for item in planned] == [True, False]
    assert [item.key for item in planned] == [key, key]
    with pytest.raises(StorageMigrationError, match="unexpected bucket"):
        plan_objects([DocumentRef(uuid4(), sha256, f"s3://other/{key}")],
                     "notely-documents", "notely")
