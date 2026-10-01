from __future__ import annotations

import asyncio
from typing import BinaryIO
from urllib.parse import quote

from minio import Minio
from minio.error import S3Error


class ObjectStorageError(Exception):
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
