"""Copy and verify PDFs between legacy MinIO and the shared S3 bucket.

Run with the application stopped. A copy never changes PostgreSQL references. After all
objects exist and pass SHA-256 verification, --activate changes URIs in one transaction.
Use --to-legacy to perform the same safe sequence in the reverse direction.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
import boto3
from botocore.config import Config
from pydantic_settings import BaseSettings, SettingsConfigDict

from notely.providers.storage import ObjectStorageError, validate_s3_endpoint
from notely.providers.storage_migration import (
    StorageMigrationError, copy_verified, object_key, verify_object,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTELY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://notely:notely@localhost:5432/notely"
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket: str = "notely"
    s3_region: str = "us-east-1"
    legacy_minio_endpoint_url: str = "http://127.0.0.1:19001"
    legacy_minio_access_key_id: str = "notely"
    legacy_minio_secret_access_key: str = "notely-development-only"
    legacy_minio_bucket: str = "notely-documents"


@dataclass(frozen=True)
class DocumentRef:
    id: UUID
    sha256: str
    storage_uri: str


@dataclass(frozen=True)
class PlannedObject:
    document: DocumentRef
    key: str
    needs_copy: bool


def plan_objects(
    documents: Sequence[DocumentRef], source_bucket: str, destination_bucket: str,
) -> list[PlannedObject]:
    if not source_bucket or not destination_bucket or source_bucket == destination_bucket:
        raise StorageMigrationError("source and destination buckets must differ")
    planned: list[PlannedObject] = []
    for document in documents:
        if document.storage_uri.startswith(f"s3://{source_bucket}/"):
            planned.append(PlannedObject(
                document, object_key(document.storage_uri, source_bucket, document.sha256), True,
            ))
        elif document.storage_uri.startswith(f"s3://{destination_bucket}/"):
            planned.append(PlannedObject(
                document, object_key(document.storage_uri, destination_bucket, document.sha256), False,
            ))
        else:
            raise StorageMigrationError("document URI uses an unexpected bucket")
    return planned


def client(endpoint: str, access_key: str, secret_key: str, region: str):
    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=access_key,
        aws_secret_access_key=secret_key, region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def database_dsn(settings: Settings) -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def documents(settings: Settings) -> list[DocumentRef]:
    connection = await asyncpg.connect(database_dsn(settings))
    try:
        rows = await connection.fetch("SELECT id, sha256, storage_uri FROM documents ORDER BY id")
        return [DocumentRef(row["id"], row["sha256"], row["storage_uri"]) for row in rows]
    finally:
        await connection.close()


async def activate_references(
    settings: Settings, expected: Sequence[DocumentRef],
    source_bucket: str, destination_bucket: str,
) -> int:
    """Fail if any document changed since verification; switch all URIs atomically."""
    connection = await asyncpg.connect(database_dsn(settings))
    try:
        async with connection.transaction():
            rows = await connection.fetch(
                "SELECT id, sha256, storage_uri FROM documents ORDER BY id FOR UPDATE"
            )
            current = [DocumentRef(row["id"], row["sha256"], row["storage_uri"]) for row in rows]
            if current != list(expected):
                raise StorageMigrationError("documents changed during verification; retry")
            planned = plan_objects(current, source_bucket, destination_bucket)
            changed = 0
            for item in planned:
                if not item.needs_copy:
                    continue
                new_uri = f"s3://{destination_bucket}/{item.key}"
                result = await connection.execute(
                    "UPDATE documents SET storage_uri = $1 WHERE id = $2 AND storage_uri = $3",
                    new_uri, item.document.id, item.document.storage_uri,
                )
                if result != "UPDATE 1":
                    raise StorageMigrationError("document changed during activation; retry")
                changed += 1
            return changed
    finally:
        await connection.close()


def endpoint_identity(endpoint: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return parsed.scheme, host, parsed.port


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and migrate PDFs between S3 buckets")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="verify without writing objects or URIs")
    action.add_argument("--activate", action="store_true", help="verify every destination object, then update all PostgreSQL URIs atomically")
    parser.add_argument("--to-legacy", action="store_true", help="reverse the copy and activation direction for rollback")
    args = parser.parse_args()
    settings = Settings()
    if not all((settings.s3_endpoint_url, settings.s3_access_key_id,
                settings.s3_secret_access_key, settings.s3_bucket)):
        parser.error("configure NOTELY_S3_ENDPOINT_URL, ACCESS_KEY_ID, SECRET_ACCESS_KEY and BUCKET")
    try:
        shared_endpoint = validate_s3_endpoint(settings.s3_endpoint_url)
        legacy_endpoint = validate_s3_endpoint(settings.legacy_minio_endpoint_url)
    except ObjectStorageError as exc:
        parser.error(str(exc))
    if endpoint_identity(shared_endpoint) == endpoint_identity(legacy_endpoint):
        parser.error("shared S3 and legacy MinIO endpoints must differ")

    shared = client(shared_endpoint, settings.s3_access_key_id,
                    settings.s3_secret_access_key, settings.s3_region)
    legacy = client(legacy_endpoint, settings.legacy_minio_access_key_id,
                    settings.legacy_minio_secret_access_key, "us-east-1")
    source, destination = (shared, legacy) if args.to_legacy else (legacy, shared)
    source_bucket, destination_bucket = (
        (settings.s3_bucket, settings.legacy_minio_bucket) if args.to_legacy
        else (settings.legacy_minio_bucket, settings.s3_bucket)
    )
    records = asyncio.run(documents(settings))
    planned = plan_objects(records, source_bucket, destination_bucket)
    counts = {"copied": 0, "already_verified": 0, "would_copy": 0}
    total_bytes = 0
    for item in planned:
        if args.activate or not item.needs_copy:
            size = verify_object(destination, destination_bucket, item.key, item.document.sha256)
            result = "already_verified"
        else:
            result, size = copy_verified(
                source=source, destination=destination,
                source_bucket=source_bucket, destination_bucket=destination_bucket,
                key=item.key, sha256=item.document.sha256, dry_run=args.dry_run,
            )
        counts[result] += 1
        total_bytes += size

    changed = 0
    if args.activate:
        changed = asyncio.run(activate_references(
            settings, records, source_bucket, destination_bucket,
        ))
    print(f"documents={len(planned)} bytes={total_bytes} {counts} activated={changed}")


if __name__ == "__main__":
    main()
