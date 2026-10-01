from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from notely.core.models import Annotation, AnnotationSource, AnnotationType, Document, OutboxEvent
from notely.core.ports import UnitOfWork


class DocumentNotFoundError(Exception):
    pass


class DocumentConflictError(Exception):
    pass


class PageOutsideDocumentError(Exception):
    pass


class NotelyService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def find_document_by_sha256(self, sha256: str) -> Document | None:
        async with self._uow_factory() as uow:
            return await uow.get_document_by_sha256(sha256.lower())

    async def register_document(
        self,
        *,
        sha256: str,
        title: str,
        filename: str,
        page_count: int,
        storage_uri: str,
    ) -> Document:
        document = Document(
            sha256=sha256.lower(),
            title=title,
            filename=filename,
            page_count=page_count,
            storage_uri=storage_uri,
        )
        async with self._uow_factory() as uow:
            if await uow.get_document_by_sha256(document.sha256):
                raise DocumentConflictError("a document with this sha256 already exists")
            await uow.add_document(document)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="document",
                    aggregate_id=document.id,
                    event_type="document.created",
                    payload={"document_id": str(document.id)},
                )
            )
            await uow.commit()
        return document

    async def create_annotation(
        self,
        *,
        document_id: UUID,
        page_number: int,
        annotation_type: AnnotationType,
        quote: str,
        position: dict[str, Any],
        comment: str | None = None,
        source: AnnotationSource = AnnotationSource.USER_SELECTION,
    ) -> Annotation:
        async with self._uow_factory() as uow:
            document = await uow.get_document(document_id)
            if document is None:
                raise DocumentNotFoundError(str(document_id))
            if page_number > document.page_count:
                raise PageOutsideDocumentError(
                    f"page {page_number} exceeds document page count {document.page_count}"
                )

            annotation = Annotation(
                document_id=document_id,
                page_number=page_number,
                type=annotation_type,
                quote=quote,
                comment=comment,
                position=position,
                source=source,
            )
            await uow.add_annotation(annotation)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="annotation",
                    aggregate_id=annotation.id,
                    event_type="annotation.created",
                    payload={
                        "annotation_id": str(annotation.id),
                        "document_id": str(document_id),
                        "page_number": page_number,
                        "annotation_type": annotation.type.value,
                        "source": annotation.source.value,
                        "author_type": annotation.author_type.value,
                    },
                )
            )
            await uow.commit()
        return annotation

    async def list_document_annotations(self, document_id: UUID) -> list[Annotation]:
        async with self._uow_factory() as uow:
            if await uow.get_document(document_id) is None:
                raise DocumentNotFoundError(str(document_id))
            return await uow.list_annotations(document_id)
