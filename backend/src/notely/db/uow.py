from __future__ import annotations

from types import TracebackType
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notely.core.models import (
    AISuggestion,
    Annotation,
    ReadingSession,
    AnnotationSource,
    AnnotationType,
    AuthorType,
    Document,
    OutboxEvent,
    StudySession,
    StudySessionDocument,
    SuggestionStatus,
    ThemeOrigin,
)
from notely.db.models import (
    AISuggestionRow,
    AnnotationRow,
    DocumentRow,
    OutboxEventRow,
    ReadingSessionRow,
    StudySessionDocumentRow,
    StudySessionRow,
)


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

    async def list_documents(self) -> list[Document]:
        rows = (
            await self._active_session().scalars(
                select(DocumentRow).order_by(DocumentRow.created_at.desc(), DocumentRow.id)
            )
        ).all()
        return [_to_document(row) for row in rows]

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
                passage_id=annotation.passage_id,
                passage_id_version=annotation.passage_id_version,
                source=annotation.source.value,
                author_type=annotation.author_type.value,
                reading_session_id=annotation.reading_session_id,
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
                event_key=event.event_key,
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

    async def add_reading_session(self, session: ReadingSession) -> None:
        self._active_session().add(_reading_session_row(session))

    async def update_reading_session(self, session: ReadingSession) -> None:
        row = await self._active_session().get(ReadingSessionRow, session.id)
        if row is None:
            raise LookupError(f"reading session {session.id} does not exist")
        row.ended_at = session.ended_at
        row.start_page = session.start_page
        row.end_page = session.end_page
        row.last_activity_at = session.last_activity_at
        row.updated_at = session.updated_at

    async def get_reading_session(self, session_id: UUID) -> ReadingSession | None:
        row = await self._active_session().get(ReadingSessionRow, session_id)
        return _to_reading_session(row) if row else None

    async def add_study_session(self, session: StudySession) -> None:
        self._active_session().add(_study_session_row(session))

    async def update_study_session(self, session: StudySession) -> None:
        row = await self._active_session().get(StudySessionRow, session.id)
        if row is None:
            raise LookupError(f"study session {session.id} does not exist")
        row.theme = session.theme
        row.theme_origin = session.theme_origin.value if session.theme_origin else None
        row.theme_updated_at = session.theme_updated_at
        row.updated_at = session.updated_at

    async def get_study_session(self, session_id: UUID) -> StudySession | None:
        row = await self._active_session().get(StudySessionRow, session_id)
        return _to_study_session(row) if row else None

    async def list_study_sessions(self) -> list[StudySession]:
        rows = (
            await self._active_session().scalars(
                select(StudySessionRow).order_by(
                    StudySessionRow.updated_at.desc(),
                    StudySessionRow.created_at.desc(),
                    StudySessionRow.id,
                )
            )
        ).all()
        return [_to_study_session(row) for row in rows]

    async def add_study_session_document(self, link: StudySessionDocument) -> None:
        self._active_session().add(
            StudySessionDocumentRow(
                id=link.id,
                study_session_id=link.study_session_id,
                document_id=link.document_id,
                position=link.position,
                added_at=link.added_at,
            )
        )

    async def get_study_session_document(
        self, session_id: UUID, document_id: UUID
    ) -> StudySessionDocument | None:
        row = await self._active_session().scalar(
            select(StudySessionDocumentRow).where(
                StudySessionDocumentRow.study_session_id == session_id,
                StudySessionDocumentRow.document_id == document_id,
            )
        )
        return _to_study_session_document(row) if row else None

    async def list_study_session_documents(
        self, session_id: UUID
    ) -> list[StudySessionDocument]:
        rows = (
            await self._active_session().scalars(
                select(StudySessionDocumentRow)
                .where(StudySessionDocumentRow.study_session_id == session_id)
                .order_by(
                    StudySessionDocumentRow.position,
                    StudySessionDocumentRow.added_at,
                    StudySessionDocumentRow.id,
                )
            )
        ).all()
        return [_to_study_session_document(row) for row in rows]

    async def remove_study_session_document(self, session_id: UUID, document_id: UUID) -> None:
        await self._active_session().execute(
            delete(StudySessionDocumentRow).where(
                StudySessionDocumentRow.study_session_id == session_id,
                StudySessionDocumentRow.document_id == document_id,
            )
        )

    async def next_study_session_position(self, session_id: UUID) -> int:
        highest = await self._active_session().scalar(
            select(func.max(StudySessionDocumentRow.position)).where(
                StudySessionDocumentRow.study_session_id == session_id
            )
        )
        return 0 if highest is None else int(highest) + 1

    async def add_ai_suggestion(self, suggestion: AISuggestion) -> None:
        self._active_session().add(
            AISuggestionRow(
                id=suggestion.id,
                suggestion_type=suggestion.suggestion_type,
                subject_type=suggestion.subject_type,
                subject_id=suggestion.subject_id,
                payload=suggestion.payload,
                provider=suggestion.provider,
                model=suggestion.model,
                status=suggestion.status.value,
                created_at=suggestion.created_at,
                accepted_at=suggestion.accepted_at,
                rejected_at=suggestion.rejected_at,
            )
        )

    async def update_ai_suggestion(self, suggestion: AISuggestion) -> None:
        row = await self._active_session().get(AISuggestionRow, suggestion.id)
        if row is None:
            raise LookupError(f"suggestion {suggestion.id} does not exist")
        row.status = suggestion.status.value
        row.accepted_at = suggestion.accepted_at
        row.rejected_at = suggestion.rejected_at

    async def get_ai_suggestion(self, suggestion_id: UUID) -> AISuggestion | None:
        row = await self._active_session().get(AISuggestionRow, suggestion_id)
        return _to_ai_suggestion(row) if row else None

    async def list_ai_suggestions(
        self, subject_type: str, subject_id: UUID
    ) -> list[AISuggestion]:
        rows = (
            await self._active_session().scalars(
                select(AISuggestionRow)
                .where(
                    AISuggestionRow.subject_type == subject_type,
                    AISuggestionRow.subject_id == subject_id,
                )
                .order_by(AISuggestionRow.created_at.desc(), AISuggestionRow.id)
            )
        ).all()
        return [_to_ai_suggestion(row) for row in rows]

    async def commit(self) -> None:
        await self._active_session().commit()


def _reading_session_row(session: ReadingSession) -> ReadingSessionRow:
    return ReadingSessionRow(
        id=session.id,
        document_id=session.document_id,
        filename_snapshot=session.filename_snapshot,
        started_at=session.started_at,
        ended_at=session.ended_at,
        start_page=session.start_page,
        end_page=session.end_page,
        last_activity_at=session.last_activity_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _to_reading_session(row: ReadingSessionRow) -> ReadingSession:
    return ReadingSession(
        id=row.id,
        document_id=row.document_id,
        filename_snapshot=row.filename_snapshot,
        started_at=row.started_at,
        ended_at=row.ended_at,
        start_page=row.start_page,
        end_page=row.end_page,
        last_activity_at=row.last_activity_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _study_session_row(session: StudySession) -> StudySessionRow:
    return StudySessionRow(
        id=session.id,
        theme=session.theme,
        theme_origin=session.theme_origin.value if session.theme_origin else None,
        theme_updated_at=session.theme_updated_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


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
        passage_id=row.passage_id,
        passage_id_version=row.passage_id_version,
        source=AnnotationSource(row.source),
        author_type=AuthorType(row.author_type),
        reading_session_id=row.reading_session_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_study_session(row: StudySessionRow) -> StudySession:
    return StudySession(
        id=row.id,
        theme=row.theme,
        theme_origin=ThemeOrigin(row.theme_origin) if row.theme_origin else None,
        theme_updated_at=row.theme_updated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_study_session_document(row: StudySessionDocumentRow) -> StudySessionDocument:
    return StudySessionDocument(
        id=row.id,
        study_session_id=row.study_session_id,
        document_id=row.document_id,
        position=row.position,
        added_at=row.added_at,
    )


def _to_ai_suggestion(row: AISuggestionRow) -> AISuggestion:
    return AISuggestion(
        id=row.id,
        suggestion_type=row.suggestion_type,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        payload=row.payload,
        provider=row.provider,
        model=row.model,
        status=SuggestionStatus(row.status),
        created_at=row.created_at,
        accepted_at=row.accepted_at,
        rejected_at=row.rejected_at,
    )
