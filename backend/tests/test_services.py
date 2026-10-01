from __future__ import annotations

from types import TracebackType
from uuid import UUID

import pytest

from notely.core.models import Annotation, AnnotationType, Document, OutboxEvent
from notely.core.services import NotelyService, PageOutsideDocumentError


class FakeUnitOfWork:
    def __init__(self, documents: list[Document], *, fail_outbox: bool = False) -> None:
        self.documents = {document.id: document for document in documents}
        self.annotations: list[Annotation] = []
        self.events: list[OutboxEvent] = []
        self.fail_outbox = fail_outbox
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.annotations.clear()
            self.events.clear()
            self.rolled_back = True

    async def get_document(self, document_id: UUID) -> Document | None:
        return self.documents.get(document_id)

    async def get_document_by_sha256(self, sha256: str) -> Document | None:
        return next((item for item in self.documents.values() if item.sha256 == sha256), None)

    async def add_document(self, document: Document) -> None:
        self.documents[document.id] = document

    async def add_annotation(self, annotation: Annotation) -> None:
        self.annotations.append(annotation)

    async def add_outbox_event(self, event: OutboxEvent) -> None:
        if self.fail_outbox:
            raise RuntimeError("outbox unavailable")
        self.events.append(event)

    async def list_annotations(self, document_id: UUID) -> list[Annotation]:
        return [item for item in self.annotations if item.document_id == document_id]

    async def commit(self) -> None:
        self.committed = True


def make_document() -> Document:
    return Document(
        sha256="a" * 64,
        title="A paper",
        filename="paper.pdf",
        page_count=3,
        storage_uri="documents/paper.pdf",
    )


async def test_annotation_and_outbox_are_committed_together() -> None:
    document = make_document()
    uow = FakeUnitOfWork([document])
    service = NotelyService(lambda: uow)

    annotation = await service.create_annotation(
        document_id=document.id,
        page_number=2,
        annotation_type=AnnotationType.HIGHLIGHT,
        quote="Reader-first systems preserve attention.",
        position={"rects": [{"x": 10, "y": 20, "width": 100, "height": 12}]},
    )

    assert uow.committed is True
    assert uow.annotations == [annotation]
    assert len(uow.events) == 1
    assert uow.events[0].aggregate_id == annotation.id
    assert uow.events[0].event_type == "annotation.created"
    assert uow.events[0].payload["author_type"] == "user"


async def test_outbox_failure_rolls_back_annotation() -> None:
    document = make_document()
    uow = FakeUnitOfWork([document], fail_outbox=True)
    service = NotelyService(lambda: uow)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await service.create_annotation(
            document_id=document.id,
            page_number=1,
            annotation_type=AnnotationType.NOTE,
            quote="Important passage",
            comment="My interpretation",
            position={"start": 10, "end": 27},
        )

    assert uow.rolled_back is True
    assert uow.committed is False
    assert uow.annotations == []


async def test_annotation_cannot_reference_page_outside_document() -> None:
    document = make_document()
    uow = FakeUnitOfWork([document])
    service = NotelyService(lambda: uow)

    with pytest.raises(PageOutsideDocumentError):
        await service.create_annotation(
            document_id=document.id,
            page_number=4,
            annotation_type=AnnotationType.QUESTION,
            quote="What does this mean?",
            position={"start": 1, "end": 5},
        )

    assert uow.annotations == []
    assert uow.events == []

