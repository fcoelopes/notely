from __future__ import annotations

from io import BytesIO

import pytest

from notely.core.ingestion import (
    DocumentIngestionService,
    DocumentTooLargeError,
    InvalidDocumentError,
    inspect_pdf,
)
from notely.core.models import Document
from notely.providers.malware import (
    MalwareDetectedError,
    MalwareScanError,
    ScanResult,
    parse_clamav_response,
)


class FakeDocuments:
    def __init__(self) -> None:
        self.document: Document | None = None

    async def find_document_by_sha256(self, sha256: str) -> Document | None:
        if self.document and self.document.sha256 == sha256:
            return self.document
        return None

    async def register_document(self, **values: object) -> Document:
        self.document = Document(**values)  # type: ignore[arg-type]
        return self.document


class FakeScanner:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def scan(self, content: BytesIO) -> ScanResult:
        self.calls += 1
        if self.error:
            raise self.error
        assert content.tell() == 0
        return ScanResult(engine="fake", message="clean")


class FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []

    async def put_pdf(self, **values: object) -> str:
        self.uploads.append(values)
        return f"s3://notely-documents/{values['object_name']}"


async def test_clean_pdf_is_scanned_before_storage_and_registration() -> None:
    documents = FakeDocuments()
    scanner = FakeScanner()
    storage = FakeStorage()
    service = DocumentIngestionService(
        documents=documents,  # type: ignore[arg-type]
        scanner=scanner,
        storage=storage,
        max_upload_bytes=1024,
    )

    document = await service.ingest_pdf(
        content=BytesIO(b"%PDF-1.7\nminimal test document"),
        filename="paper.pdf",
        page_count=2,
    )

    assert scanner.calls == 1
    assert len(storage.uploads) == 1
    assert storage.uploads[0]["sha256"] == document.sha256
    assert document.storage_uri.startswith("s3://notely-documents/documents/")


async def test_infected_pdf_never_reaches_storage() -> None:
    storage = FakeStorage()
    service = DocumentIngestionService(
        documents=FakeDocuments(),  # type: ignore[arg-type]
        scanner=FakeScanner(MalwareDetectedError("Eicar-Test-Signature")),
        storage=storage,
        max_upload_bytes=1024,
    )

    with pytest.raises(MalwareDetectedError):
        await service.ingest_pdf(
            content=BytesIO(b"%PDF-1.7\nunsafe"),
            filename="unsafe.pdf",
            page_count=1,
        )

    assert storage.uploads == []


def test_pdf_inspection_rejects_invalid_or_oversized_content() -> None:
    with pytest.raises(InvalidDocumentError):
        inspect_pdf(BytesIO(b"not a pdf"), 100)

    with pytest.raises(DocumentTooLargeError):
        inspect_pdf(BytesIO(b"%PDF-" + b"x" * 20), 10)


def test_clamav_responses_fail_closed() -> None:
    assert parse_clamav_response("stream: OK").engine == "clamav"
    with pytest.raises(MalwareDetectedError, match="Eicar-Test-Signature"):
        parse_clamav_response("stream: Eicar-Test-Signature FOUND")
    with pytest.raises(MalwareScanError, match="unexpected"):
        parse_clamav_response("stream: ERROR")
