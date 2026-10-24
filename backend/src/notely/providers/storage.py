"""Private S3-compatible PDF storage; domain metadata remains in PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import BinaryIO
from urllib.parse import quote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

STREAM_CHUNK_BYTES = 1024 * 1024


class ObjectStorageError(Exception):
    pass


class ObjectNotFoundError(ObjectStorageError):
    pass


def validate_s3_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    try:
        parsed = urlsplit(endpoint)
        parsed.port
    except ValueError as exc:
        raise ObjectStorageError("S3 endpoint is invalid") from exc
    local_http = parsed.scheme == "http" and parsed.hostname in {
        "localhost", "127.0.0.1", "::1",
    }
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or (parsed.scheme == "http" and not local_http)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ObjectStorageError("S3 endpoint must use HTTPS or HTTP loopback")
    return endpoint


class S3ObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        region: str = "us-east-1",
        client: object | None = None,
    ) -> None:
        self.bucket = bucket.strip()
        self._client = client
        self._configuration = (
            endpoint_url.strip(), access_key_id.strip(), secret_access_key.strip(), region.strip()
        )

    def _s3(self) -> object:
        if self._client is None:
            endpoint, access_key, secret_key, region = self._configuration
            if not all((endpoint, access_key, secret_key, self.bucket, region)):
                raise ObjectStorageError("S3 storage is not configured")
            endpoint = validate_s3_endpoint(endpoint)
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._client

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
            content.seek(0)
            await asyncio.to_thread(
                self._s3().put_object,
                Bucket=self.bucket,
                Key=object_name,
                Body=content,
                ContentLength=length,
                ContentType="application/pdf",
                Metadata={
                    "sha256": sha256,
                    "original-filename": quote(original_filename, safe=""),
                },
            )
        except (BotoCoreError, ClientError, OSError, UnicodeError) as exc:
            raise ObjectStorageError("S3 storage is unavailable") from exc
        finally:
            content.seek(0)
        return f"s3://{self.bucket}/{object_name}"

    async def open_pdf(self, *, storage_uri: str) -> AsyncIterator[bytes]:
        object_name = self.object_name(storage_uri)
        try:
            result = await asyncio.to_thread(
                self._s3().get_object, Bucket=self.bucket, Key=object_name
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
                raise ObjectNotFoundError("the stored PDF is missing") from exc
            raise ObjectStorageError("S3 storage is unavailable") from exc
        except (BotoCoreError, OSError, UnicodeError) as exc:
            raise ObjectStorageError("S3 storage is unavailable") from exc

        body = result["Body"]
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(body.read, STREAM_CHUNK_BYTES)
                except (BotoCoreError, OSError) as exc:
                    raise ObjectStorageError("S3 storage is unavailable") from exc
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    def object_name(self, storage_uri: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not self.bucket or not storage_uri.startswith(prefix) or len(storage_uri) == len(prefix):
            raise ObjectNotFoundError(f"unsupported storage uri: {storage_uri}")
        return storage_uri[len(prefix) :]
