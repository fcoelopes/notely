from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from notely.core.models import (
    AISuggestion,
    AISuggestionType,
    Annotation,
    AnnotationSource,
    AnnotationType,
    Document,
    OutboxEvent,
    StudySession,
    StudySessionDocument,
    SuggestionStatus,
    ThemeOrigin,
    utc_now,
)
from notely.core.ports import UnitOfWork
from notely.providers.topic import TopicSuggestionContext, TopicSuggestionProvider

STUDY_SESSION_SUBJECT = "study_session"


class DocumentNotFoundError(Exception):
    pass


class DocumentConflictError(Exception):
    pass


class PageOutsideDocumentError(Exception):
    pass


class StudySessionNotFoundError(Exception):
    pass


class SessionDocumentConflictError(Exception):
    pass


class SessionDocumentNotFoundError(Exception):
    pass


class SuggestionNotFoundError(Exception):
    pass


class SuggestionAlreadyResolvedError(Exception):
    pass


class SessionWithoutDocumentsError(Exception):
    pass


class NotelyService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def find_document_by_sha256(self, sha256: str) -> Document | None:
        async with self._uow_factory() as uow:
            return await uow.get_document_by_sha256(sha256.lower())

    async def list_documents(self) -> list[Document]:
        async with self._uow_factory() as uow:
            return await uow.list_documents()

    async def get_document(self, document_id: UUID) -> Document:
        async with self._uow_factory() as uow:
            document = await uow.get_document(document_id)
            if document is None:
                raise DocumentNotFoundError(str(document_id))
            return document

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

    async def create_study_session(self, *, theme: str | None = None) -> StudySession:
        clean_theme = theme.strip() if theme else None
        session = StudySession(
            theme=clean_theme or None,
            theme_origin=ThemeOrigin.USER if clean_theme else None,
            theme_updated_at=utc_now() if clean_theme else None,
        )
        async with self._uow_factory() as uow:
            await uow.add_study_session(session)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session",
                    aggregate_id=session.id,
                    event_type="study_session.created",
                    payload={
                        "study_session_id": str(session.id),
                        "theme_origin": session.theme_origin.value if session.theme_origin else None,
                    },
                )
            )
            await uow.commit()
        return session

    async def list_study_sessions(self) -> list[StudySession]:
        async with self._uow_factory() as uow:
            return await uow.list_study_sessions()

    async def get_study_session(self, session_id: UUID) -> StudySession:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            return session

    async def list_study_session_documents(
        self, session_id: UUID
    ) -> list[tuple[StudySessionDocument, Document]]:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            links = await uow.list_study_session_documents(session_id)
            entries: list[tuple[StudySessionDocument, Document]] = []
            for link in links:
                document = await uow.get_document(link.document_id)
                if document is not None:
                    entries.append((link, document))
            return entries

    async def attach_document(
        self, *, session_id: UUID, document_id: UUID
    ) -> StudySessionDocument:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            if await uow.get_document(document_id) is None:
                raise DocumentNotFoundError(str(document_id))
            if await uow.get_study_session_document(session_id, document_id) is not None:
                raise SessionDocumentConflictError("document is already part of this session")

            position = await uow.next_study_session_position(session_id)
            link = StudySessionDocument(
                study_session_id=session_id,
                document_id=document_id,
                position=position,
            )
            await uow.add_study_session_document(link)
            await uow.update_study_session(_touched(session))
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session_document",
                    aggregate_id=link.id,
                    event_type="study_session.document_attached",
                    payload={
                        "study_session_id": str(session_id),
                        "document_id": str(document_id),
                        "position": position,
                    },
                )
            )
            await uow.commit()
        return link

    async def detach_document(self, *, session_id: UUID, document_id: UUID) -> None:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            link = await uow.get_study_session_document(session_id, document_id)
            if link is None:
                raise SessionDocumentNotFoundError(
                    f"document {document_id} is not part of session {session_id}"
                )
            await uow.remove_study_session_document(session_id, document_id)
            await uow.update_study_session(_touched(session))
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session_document",
                    aggregate_id=link.id,
                    event_type="study_session.document_removed",
                    payload={
                        "study_session_id": str(session_id),
                        "document_id": str(document_id),
                    },
                )
            )
            await uow.commit()

    async def set_study_session_theme(self, *, session_id: UUID, theme: str) -> StudySession:
        clean_theme = theme.strip()
        if not clean_theme:
            raise ValueError("theme cannot be blank")
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            updated = _with_theme(session, clean_theme, ThemeOrigin.USER)
            await uow.update_study_session(updated)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session",
                    aggregate_id=session_id,
                    event_type="study_session.theme_set",
                    event_key=(
                        f"study_session:{session_id}:theme_set:"
                        f"{updated.theme_updated_at.isoformat()}"
                    ),
                    payload={
                        "study_session_id": str(session_id),
                        "theme_origin": ThemeOrigin.USER.value,
                    },
                )
            )
            await uow.commit()
        return updated

    async def list_study_session_suggestions(self, session_id: UUID) -> list[AISuggestion]:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            return await uow.list_ai_suggestions(STUDY_SESSION_SUBJECT, session_id)

    async def create_theme_suggestion(
        self, *, session_id: UUID, provider: TopicSuggestionProvider
    ) -> AISuggestion:
        context = await self._topic_context(session_id)
        # A chamada ao modelo acontece fora de qualquer unidade de trabalho: a IA nunca
        # participa da transação que grava o domínio.
        suggestion = await provider.suggest(context)

        record = AISuggestion(
            suggestion_type=AISuggestionType.STUDY_SESSION_THEME.value,
            subject_type=STUDY_SESSION_SUBJECT,
            subject_id=session_id,
            payload={"theme": suggestion.theme, "rationale": suggestion.rationale},
            provider=suggestion.provider,
            model=suggestion.model,
        )
        async with self._uow_factory() as uow:
            await uow.add_ai_suggestion(record)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="ai_suggestion",
                    aggregate_id=record.id,
                    event_type="ai_suggestion.created",
                    payload={
                        "suggestion_id": str(record.id),
                        "subject_type": STUDY_SESSION_SUBJECT,
                        "subject_id": str(session_id),
                        "suggestion_type": record.suggestion_type,
                        "provider": record.provider,
                        "model": record.model,
                    },
                )
            )
            await uow.commit()
        return record

    async def accept_theme_suggestion(
        self, *, session_id: UUID, suggestion_id: UUID
    ) -> StudySession:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            suggestion = await _pending_suggestion(uow, session_id, suggestion_id)
            theme = str(suggestion.payload.get("theme", "")).strip()
            if not theme:
                raise ValueError("suggestion payload has no usable theme")

            accepted = AISuggestion(
                id=suggestion.id,
                suggestion_type=suggestion.suggestion_type,
                subject_type=suggestion.subject_type,
                subject_id=suggestion.subject_id,
                payload=suggestion.payload,
                provider=suggestion.provider,
                model=suggestion.model,
                status=SuggestionStatus.ACCEPTED,
                created_at=suggestion.created_at,
                accepted_at=utc_now(),
            )
            updated = _with_theme(session, theme, ThemeOrigin.AI_SUGGESTION)
            await uow.update_ai_suggestion(accepted)
            await uow.update_study_session(updated)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="ai_suggestion",
                    aggregate_id=accepted.id,
                    event_type="ai_suggestion.accepted",
                    payload={
                        "suggestion_id": str(accepted.id),
                        "subject_id": str(session_id),
                        "provider": accepted.provider,
                        "model": accepted.model,
                    },
                )
            )
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session",
                    aggregate_id=session_id,
                    event_type="study_session.theme_set",
                    event_key=(
                        f"study_session:{session_id}:theme_set:"
                        f"{updated.theme_updated_at.isoformat()}"
                    ),
                    payload={
                        "study_session_id": str(session_id),
                        "theme_origin": ThemeOrigin.AI_SUGGESTION.value,
                        "suggestion_id": str(accepted.id),
                    },
                )
            )
            await uow.commit()
        return updated

    async def reject_theme_suggestion(
        self, *, session_id: UUID, suggestion_id: UUID
    ) -> AISuggestion:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            suggestion = await _pending_suggestion(uow, session_id, suggestion_id)
            rejected = AISuggestion(
                id=suggestion.id,
                suggestion_type=suggestion.suggestion_type,
                subject_type=suggestion.subject_type,
                subject_id=suggestion.subject_id,
                payload=suggestion.payload,
                provider=suggestion.provider,
                model=suggestion.model,
                status=SuggestionStatus.REJECTED,
                created_at=suggestion.created_at,
                rejected_at=utc_now(),
            )
            await uow.update_ai_suggestion(rejected)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="ai_suggestion",
                    aggregate_id=rejected.id,
                    event_type="ai_suggestion.rejected",
                    payload={"suggestion_id": str(rejected.id), "subject_id": str(session_id)},
                )
            )
            await uow.commit()
        return rejected

    async def _topic_context(self, session_id: UUID) -> TopicSuggestionContext:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            links = await uow.list_study_session_documents(session_id)
            if not links:
                raise SessionWithoutDocumentsError(
                    "a study session needs at least one document before a theme suggestion"
                )
            titles: list[str] = []
            quotes: list[str] = []
            comments: list[str] = []
            for link in links:
                document = await uow.get_document(link.document_id)
                if document is None:
                    continue
                titles.append(document.title)
                for annotation in await uow.list_annotations(link.document_id):
                    quotes.append(annotation.quote)
                    if annotation.comment:
                        comments.append(annotation.comment)
        return TopicSuggestionContext(
            document_titles=tuple(titles),
            quotes=tuple(quotes),
            comments=tuple(comments),
        )


def _touched(session: StudySession) -> StudySession:
    return StudySession(
        id=session.id,
        theme=session.theme,
        theme_origin=session.theme_origin,
        theme_updated_at=session.theme_updated_at,
        created_at=session.created_at,
        updated_at=utc_now(),
    )


def _with_theme(session: StudySession, theme: str, origin: ThemeOrigin) -> StudySession:
    now = utc_now()
    return StudySession(
        id=session.id,
        theme=theme,
        theme_origin=origin,
        theme_updated_at=now,
        created_at=session.created_at,
        updated_at=now,
    )


async def _pending_suggestion(
    uow: UnitOfWork, session_id: UUID, suggestion_id: UUID
) -> AISuggestion:
    suggestion = await uow.get_ai_suggestion(suggestion_id)
    if suggestion is None or suggestion.subject_id != session_id:
        raise SuggestionNotFoundError(str(suggestion_id))
    if suggestion.status is not SuggestionStatus.PENDING:
        raise SuggestionAlreadyResolvedError(
            f"suggestion {suggestion_id} is already {suggestion.status.value}"
        )
    return suggestion
