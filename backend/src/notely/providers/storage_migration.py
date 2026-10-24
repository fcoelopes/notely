"""Verified copy of legacy MinIO PDFs to another S3-compatible service."""

from __future__ import annotations

import hashlib
import re
from tempfile import SpooledTemporaryFile
from typing import Any

from botocore.exceptions import ClientError

_OBJECT_KEY = re.compile(r"documents/([0-9a-f]{2})/([0-9a-f]{64})\.pdf\Z")
_CHUNK = 1024 * 1024


class StorageMigrationError(Exception):
    pass


def object_key(storage_uri: str, bucket: str, sha256: str) -> str:
    prefix = f"s3://{bucket}/"
    if not storage_uri.startswith(prefix):
        raise StorageMigrationError("document URI does not use the destination bucket")
    key = storage_uri[len(prefix):]
    match = _OBJECT_KEY.fullmatch(key)
    if match is None or match.group(1) != sha256[:2] or match.group(2) != sha256:
        raise StorageMigrationError("document URI does not match its SHA-256")
    return key


def _read_verified(
    client: Any, bucket: str, key: str, sha256: str, output: Any = None,
    metadata: dict[str, str] | None = None,
) -> int:
    result = client.get_object(Bucket=bucket, Key=key)
    body = result["Body"]
    if metadata is not None:
        metadata.update(result.get("Metadata", {}))
    digest = hashlib.sha256()
    total = 0
    try:
        while chunk := body.read(_CHUNK):
            digest.update(chunk)
            total += len(chunk)
            if output is not None:
                output.write(chunk)
    finally:
        body.close()
    if digest.hexdigest() != sha256:
        raise StorageMigrationError("object SHA-256 does not match PostgreSQL")
    return total


def verify_object(client: Any, bucket: str, key: str, sha256: str) -> int:
    """Read an object fully and compare it with the authoritative SHA-256."""
    return _read_verified(client, bucket, key, sha256)


def copy_verified(
    *, source: Any, destination: Any, source_bucket: str, destination_bucket: str,
    key: str, sha256: str,
    dry_run: bool = False,
) -> tuple[str, int]:
    """Copy once and read back; existing destination objects must already match."""
    with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as content:
        source_metadata: dict[str, str] = {}
        size = _read_verified(source, source_bucket, key, sha256, content, source_metadata)
        try:
            destination_size = _read_verified(destination, destination_bucket, key, sha256)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"NoSuchKey", "404", "NotFound"}:
                raise
        else:
            if destination_size != size:
                raise StorageMigrationError("destination object size differs from source")
            return "already_verified", size
        if dry_run:
            return "would_copy", size
        content.seek(0)
        destination.put_object(
            Bucket=destination_bucket, Key=key, Body=content, ContentLength=size,
            ContentType="application/pdf", Metadata={**source_metadata, "sha256": sha256},
        )
        destination_size = _read_verified(destination, destination_bucket, key, sha256)
        if destination_size != size:
            raise StorageMigrationError("destination object size differs from source")
        return "copied", size
