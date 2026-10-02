from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from notely.core.models import Document
from notely.core.services import DocumentConflictError, NotelyService
from notely.providers.malware import ScanResult


class InvalidDocumentError(Exception):
    pass


class DocumentTooLargeError(Exception):
    pass


class MalwareScanner(Protocol):
    async def scan(self, content: BinaryIO) -> ScanResult: ...


class ObjectStorage(Protocol):
    async def put_pdf(
        self,
        *,
        object_name: str,
        content: BinaryIO,
        length: int,
        sha256: str,
        original_filename: str,
    ) -> str: ...

    def open_pdf(self, *, storage_uri: str) -> AsyncIterator[bytes]: ...


@dataclass(frozen=True, slots=True)
class InspectedPdf:
    sha256: str
    size: int


class DocumentIngestionService:
    def __init__(
        self,
        *,
        documents: NotelyService,
        scanner: MalwareScanner,
        storage: ObjectStorage,
        max_upload_bytes: int,
    ) -> None:
        self._documents = documents
        self._scanner = scanner
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes

    async def ingest_pdf(
        self,
        *,
        content: BinaryIO,
        filename: str,
        page_count: int,
    ) -> Document:
        inspected = await asyncio.to_thread(
            inspect_pdf, content, self._max_upload_bytes
        )
        await self._scanner.scan(content)

        existing = await self._documents.find_document_by_sha256(inspected.sha256)
        if existing is not None:
            return existing

        object_name = f"documents/{inspected.sha256[:2]}/{inspected.sha256}.pdf"
        storage_uri = await self._storage.put_pdf(
            object_name=object_name,
            content=content,
            length=inspected.size,
            sha256=inspected.sha256,
            original_filename=filename,
        )
        try:
            return await self._documents.register_document(
                sha256=inspected.sha256,
                title=Path(filename).stem,
                filename=filename,
                page_count=page_count,
                storage_uri=storage_uri,
            )
        except DocumentConflictError:
            concurrent = await self._documents.find_document_by_sha256(inspected.sha256)
            if concurrent is None:
                raise
            return concurrent


def inspect_pdf(content: BinaryIO, max_upload_bytes: int) -> InspectedPdf:
    content.seek(0)
    digest = hashlib.sha256()
    total = 0
    header = content.read(5)
    if header != b"%PDF-":
        content.seek(0)
        raise InvalidDocumentError("the uploaded file is not a PDF")
    digest.update(header)
    total += len(header)

    while chunk := content.read(1024 * 1024):
        total += len(chunk)
        if total > max_upload_bytes:
            content.seek(0)
            raise DocumentTooLargeError(
                f"the PDF exceeds the {max_upload_bytes} byte upload limit"
            )
        digest.update(chunk)
    content.seek(0)
    return InspectedPdf(sha256=digest.hexdigest(), size=total)
