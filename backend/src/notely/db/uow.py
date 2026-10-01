from __future__ import annotations

from types import TracebackType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notely.core.models import Annotation, AnnotationSource, AnnotationType, AuthorType, Document, OutboxEvent
from notely.db.models import AnnotationRow, DocumentRow, OutboxEventRow


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    def _active_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("unit of work must be used as an async context manager")
        return self.session

    async def get_document(self, document_id: UUID) -> Document | None:
        row = await self._active_session().get(DocumentRow, document_id)
        return _to_document(row) if row else None

    async def get_document_by_sha256(self, sha256: str) -> Document | None:
        row = await self._active_session().scalar(
            select(DocumentRow).where(DocumentRow.sha256 == sha256)
        )
        return _to_document(row) if row else None

    async def add_document(self, document: Document) -> None:
        self._active_session().add(
            DocumentRow(
                id=document.id,
                sha256=document.sha256,
                title=document.title,
                filename=document.filename,
                mime_type=document.mime_type,
                page_count=document.page_count,
                storage_uri=document.storage_uri,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )

    async def add_annotation(self, annotation: Annotation) -> None:
        self._active_session().add(
            AnnotationRow(
                id=annotation.id,
                document_id=annotation.document_id,
                page_number=annotation.page_number,
                type=annotation.type.value,
                quote=annotation.quote,
                comment=annotation.comment,
                position_json=annotation.position,
                source=annotation.source.value,
                author_type=annotation.author_type.value,
                created_at=annotation.created_at,
                updated_at=annotation.updated_at,
            )
        )

    async def add_outbox_event(self, event: OutboxEvent) -> None:
        self._active_session().add(
            OutboxEventRow(
                id=event.id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=event.payload,
                created_at=event.created_at,
                available_at=event.available_at,
                attempt_count=0,
            )
        )

    async def list_annotations(self, document_id: UUID) -> list[Annotation]:
        rows = (
            await self._active_session().scalars(
                select(AnnotationRow)
                .where(
                    AnnotationRow.document_id == document_id,
                    AnnotationRow.deleted_at.is_(None),
                )
                .order_by(AnnotationRow.page_number, AnnotationRow.created_at)
            )
        ).all()
        return [_to_annotation(row) for row in rows]

    async def commit(self) -> None:
        await self._active_session().commit()


def _to_document(row: DocumentRow) -> Document:
    return Document(
        id=row.id,
        sha256=row.sha256,
        title=row.title,
        filename=row.filename,
        mime_type=row.mime_type,
        page_count=row.page_count,
        storage_uri=row.storage_uri,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_annotation(row: AnnotationRow) -> Annotation:
    return Annotation(
        id=row.id,
        document_id=row.document_id,
        page_number=row.page_number,
        type=AnnotationType(row.type),
        quote=row.quote,
        comment=row.comment,
        position=row.position_json,
        source=AnnotationSource(row.source),
        author_type=AuthorType(row.author_type),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

