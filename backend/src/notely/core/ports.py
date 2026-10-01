from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from notely.core.models import Annotation, Document, OutboxEvent


class UnitOfWork(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def get_document(self, document_id: UUID) -> Document | None: ...

    async def get_document_by_sha256(self, sha256: str) -> Document | None: ...

    async def add_document(self, document: Document) -> None: ...

    async def add_annotation(self, annotation: Annotation) -> None: ...

    async def add_outbox_event(self, event: OutboxEvent) -> None: ...

    async def list_annotations(self, document_id: UUID) -> list[Annotation]: ...

    async def commit(self) -> None: ...

