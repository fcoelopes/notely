from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import BinaryIO
from urllib.parse import quote

from minio import Minio
from minio.error import S3Error

STREAM_CHUNK_BYTES = 1024 * 1024


class ObjectStorageError(Exception):
    pass


class ObjectNotFoundError(ObjectStorageError):
    pass


class MinioObjectStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool,
    ) -> None:
        self.bucket = bucket
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    async def put_pdf(
        self,
        *,
        object_name: str,
        content: BinaryIO,
        length: int,
        sha256: str,
        original_filename: str,
    ) -> str:
        try:
            await asyncio.to_thread(
                self._put_pdf,
                object_name,
                content,
                length,
                sha256,
                original_filename,
            )
        except (OSError, S3Error, UnicodeError) as exc:
            raise ObjectStorageError("MinIO is unavailable") from exc
        return f"s3://{self.bucket}/{object_name}"

    async def open_pdf(self, *, storage_uri: str) -> AsyncIterator[bytes]:
        object_name = self.object_name(storage_uri)
        try:
            response = await asyncio.to_thread(
                self._client.get_object, self.bucket, object_name
            )
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket"}:
                raise ObjectNotFoundError("the stored PDF is missing") from exc
            raise ObjectStorageError("MinIO is unavailable") from exc
        except (OSError, UnicodeError) as exc:
            raise ObjectStorageError("MinIO is unavailable") from exc

        try:
            while True:
                chunk = await asyncio.to_thread(response.read, STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(_release, response)

    def object_name(self, storage_uri: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not storage_uri.startswith(prefix) or len(storage_uri) == len(prefix):
            raise ObjectNotFoundError(f"unsupported storage uri: {storage_uri}")
        return storage_uri[len(prefix) :]

    def _put_pdf(
        self,
        object_name: str,
        content: BinaryIO,
        length: int,
        sha256: str,
        original_filename: str,
    ) -> None:
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)
        content.seek(0)
        try:
            self._client.put_object(
                self.bucket,
                object_name,
                content,
                length,
                content_type="application/pdf",
                metadata={
                    "sha256": sha256,
                    "original-filename": quote(original_filename, safe=""),
                },
            )
        finally:
            content.seek(0)


def _release(response: object) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()
    release = getattr(response, "release_conn", None)
    if callable(release):
        release()
