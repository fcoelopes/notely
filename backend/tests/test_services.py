from __future__ import annotations

from types import TracebackType
from uuid import UUID

import pytest

from notely.core.models import (
    AISuggestion,
    Annotation,
    AnnotationType,
    Document,
    OutboxEvent,
    StudySession,
    StudySessionDocument,
    SuggestionStatus,
    ThemeOrigin,
)
from notely.core.services import (
    NotelyService,
    PageOutsideDocumentError,
    SessionDocumentConflictError,
    SessionWithoutDocumentsError,
    SuggestionAlreadyResolvedError,
    SuggestionNotFoundError,
)
from notely.providers.topic import (
    HeuristicTopicSuggestionProvider,
    TopicSuggestion,
    TopicSuggestionContext,
    TopicSuggestionUnavailableError,
)


class FakeUnitOfWork:
    def __init__(self, documents: list[Document], *, fail_outbox: bool = False) -> None:
        self.documents = {document.id: document for document in documents}
        self.annotations: list[Annotation] = []
        self.events: list[OutboxEvent] = []
        self.sessions: dict[UUID, StudySession] = {}
        self.session_documents: list[StudySessionDocument] = []
        self.suggestions: dict[UUID, AISuggestion] = {}
        self.fail_outbox = fail_outbox
        self.committed = False
        self.commits = 0
        self.rolled_back = False
        self.depth = 0

    async def __aenter__(self) -> FakeUnitOfWork:
        self.depth += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.depth -= 1
        if exc_type is not None:
            self.annotations.clear()
            self.events.clear()
            self.rolled_back = True

    async def get_document(self, document_id: UUID) -> Document | None:
        return self.documents.get(document_id)

    async def get_document_by_sha256(self, sha256: str) -> Document | None:
        return next((item for item in self.documents.values() if item.sha256 == sha256), None)

    async def list_documents(self) -> list[Document]:
        return list(self.documents.values())

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

    async def add_study_session(self, session: StudySession) -> None:
        self.sessions[session.id] = session

    async def update_study_session(self, session: StudySession) -> None:
        self.sessions[session.id] = session

    async def get_study_session(self, session_id: UUID) -> StudySession | None:
        return self.sessions.get(session_id)

    async def list_study_sessions(self) -> list[StudySession]:
        return list(self.sessions.values())

    async def add_study_session_document(self, link: StudySessionDocument) -> None:
        self.session_documents.append(link)

    async def get_study_session_document(
        self, session_id: UUID, document_id: UUID
    ) -> StudySessionDocument | None:
        return next(
            (
                link
                for link in self.session_documents
                if link.study_session_id == session_id and link.document_id == document_id
            ),
            None,
        )

    async def list_study_session_documents(
        self, session_id: UUID
    ) -> list[StudySessionDocument]:
        links = [link for link in self.session_documents if link.study_session_id == session_id]
        return sorted(links, key=lambda link: (link.position, link.added_at))

    async def remove_study_session_document(self, session_id: UUID, document_id: UUID) -> None:
        self.session_documents = [
            link
            for link in self.session_documents
            if not (link.study_session_id == session_id and link.document_id == document_id)
        ]

    async def next_study_session_position(self, session_id: UUID) -> int:
        positions = [
            link.position
            for link in self.session_documents
            if link.study_session_id == session_id
        ]
        return 0 if not positions else max(positions) + 1

    async def add_ai_suggestion(self, suggestion: AISuggestion) -> None:
        self.suggestions[suggestion.id] = suggestion

    async def update_ai_suggestion(self, suggestion: AISuggestion) -> None:
        self.suggestions[suggestion.id] = suggestion

    async def get_ai_suggestion(self, suggestion_id: UUID) -> AISuggestion | None:
        return self.suggestions.get(suggestion_id)

    async def list_ai_suggestions(self, subject_type: str, subject_id: UUID) -> list[AISuggestion]:
        return [
            suggestion
            for suggestion in self.suggestions.values()
            if suggestion.subject_type == subject_type and suggestion.subject_id == subject_id
        ]

    async def commit(self) -> None:
        self.committed = True
        self.commits += 1


class StubTopicProvider:
    def __init__(self, theme: str = "embeddings · recuperação", depth: list[int] | None = None):
        self.theme = theme
        self.contexts: list[TopicSuggestionContext] = []
        self.depth = depth if depth is not None else []

    async def suggest(self, context: TopicSuggestionContext) -> TopicSuggestion:
        self.contexts.append(context)
        return TopicSuggestion(
            theme=self.theme,
            rationale="Sugestão de teste.",
            provider="stub",
            model="stub-1",
        )


class RecordingDepthProvider(StubTopicProvider):
    def __init__(self, uow: FakeUnitOfWork) -> None:
        super().__init__()
        self._uow = uow
        self.depth_at_call: list[int] = []

    async def suggest(self, context: TopicSuggestionContext) -> TopicSuggestion:
        self.depth_at_call.append(self._uow.depth)
        return await super().suggest(context)


def make_document(title: str = "A paper", *, digest: str = "a") -> Document:
    return Document(
        sha256=digest * 64,
        title=title,
        filename=f"{title}.pdf",
        page_count=3,
        storage_uri=f"documents/{title}.pdf",
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


async def test_study_session_records_theme_origin() -> None:
    uow = FakeUnitOfWork([])
    service = NotelyService(lambda: uow)

    with_theme = await service.create_study_session(theme="  recuperação de informação  ")
    without_theme = await service.create_study_session()

    assert with_theme.theme == "recuperação de informação"
    assert with_theme.theme_origin is ThemeOrigin.USER
    assert with_theme.theme_updated_at is not None
    assert without_theme.theme is None
    assert without_theme.theme_origin is None
    assert uow.committed is True
    assert [event.event_type for event in uow.events] == [
        "study_session.created",
        "study_session.created",
    ]
    assert uow.events[0].payload["theme_origin"] == "user"
    assert uow.events[1].payload["theme_origin"] is None


async def test_attaching_documents_keeps_order_and_emits_one_event_per_link() -> None:
    first = make_document("Primeiro", digest="b")
    second = make_document("Segundo", digest="c")
    uow = FakeUnitOfWork([first, second])
    service = NotelyService(lambda: uow)
    session = await service.create_study_session(theme="tema")

    first_link = await service.attach_document(
        session_id=session.id, document_id=first.id
    )
    second_link = await service.attach_document(
        session_id=session.id, document_id=second.id
    )

    assert (first_link.position, second_link.position) == (0, 1)
    entries = await service.list_study_session_documents(session.id)
    assert [document.title for _, document in entries] == ["Primeiro", "Segundo"]
    attach_events = [
        event
        for event in uow.events
        if event.event_type == "study_session.document_attached"
    ]
    assert [event.aggregate_id for event in attach_events] == [first_link.id, second_link.id]
    assert attach_events[1].payload["position"] == 1


async def test_attaching_the_same_document_twice_is_rejected() -> None:
    document = make_document()
    uow = FakeUnitOfWork([document])
    service = NotelyService(lambda: uow)
    session = await service.create_study_session()

    await service.attach_document(session_id=session.id, document_id=document.id)
    commits_before = uow.commits

    with pytest.raises(SessionDocumentConflictError):
        await service.attach_document(session_id=session.id, document_id=document.id)

    assert uow.commits == commits_before
    assert uow.rolled_back is True


async def test_theme_suggestion_is_pending_and_run_outside_the_transaction() -> None:
    document = make_document("Retrieval augmented generation")
    uow = FakeUnitOfWork([document])
    service = NotelyService(lambda: uow)
    session = await service.create_study_session()
    await service.attach_document(session_id=session.id, document_id=document.id)
    await service.create_annotation(
        document_id=document.id,
        page_number=1,
        annotation_type=AnnotationType.NOTE,
        quote="Chunking strategy matters",
        comment="Preciso comparar estratégias de chunking",
        position={"rects": [{"x": 1, "y": 2, "width": 3, "height": 4}]},
    )

    provider = RecordingDepthProvider(uow)
    suggestion = await service.create_theme_suggestion(
        session_id=session.id, provider=provider
    )

    assert suggestion.status is SuggestionStatus.PENDING
    assert suggestion.provider == "stub"
    assert suggestion.subject_id == session.id
    assert provider.depth_at_call == [0]
    assert provider.contexts[0].document_titles == ("Retrieval augmented generation",)
    assert provider.contexts[0].quotes == ("Chunking strategy matters",)
    assert provider.contexts[0].comments == ("Preciso comparar estratégias de chunking",)

    stored = await service.get_study_session(session.id)
    assert stored.theme is None
    assert uow.events[-1].event_type == "ai_suggestion.created"


async def test_theme_suggestion_requires_documents() -> None:
    uow = FakeUnitOfWork([])
    service = NotelyService(lambda: uow)
    session = await service.create_study_session()
    provider = StubTopicProvider()

    with pytest.raises(SessionWithoutDocumentsError):
        await service.create_theme_suggestion(session_id=session.id, provider=provider)

    assert provider.contexts == []


async def test_accepting_a_suggestion_is_the_only_way_it_becomes_the_theme() -> None:
    document = make_document()
    uow = FakeUnitOfWork([document])
    service = NotelyService(lambda: uow)
    session = await service.create_study_session()
    await service.attach_document(session_id=session.id, document_id=document.id)
    suggestion = await service.create_theme_suggestion(
        session_id=session.id, provider=StubTopicProvider(theme="embeddings")
    )

    accepted = await service.accept_theme_suggestion(
        session_id=session.id, suggestion_id=suggestion.id
    )

    assert accepted.theme == "embeddings"
    assert accepted.theme_origin is ThemeOrigin.AI_SUGGESTION
    assert uow.suggestions[suggestion.id].status is SuggestionStatus.ACCEPTED
    assert uow.suggestions[suggestion.id].accepted_at is not None
    assert [event.event_type for event in uow.events[-2:]] == [
        "ai_suggestion.accepted",
        "study_session.theme_set",
    ]

    with pytest.raises(SuggestionAlreadyResolvedError):
        await service.accept_theme_suggestion(
            session_id=session.id, suggestion_id=suggestion.id
        )


async def test_suggestion_from_another_session_is_not_found() -> None:
    document = make_document()
    uow = FakeUnitOfWork([document])
    service = NotelyService(lambda: uow)
    first = await service.create_study_session()
    second = await service.create_study_session()
    await service.attach_document(session_id=first.id, document_id=document.id)
    suggestion = await service.create_theme_suggestion(
        session_id=first.id, provider=StubTopicProvider()
    )

    with pytest.raises(SuggestionNotFoundError):
        await service.accept_theme_suggestion(
            session_id=second.id, suggestion_id=suggestion.id
        )


async def test_rejecting_a_suggestion_leaves_the_theme_untouched() -> None:
    document = make_document()
    uow = FakeUnitOfWork([document])
    service = NotelyService(lambda: uow)
    session = await service.create_study_session(theme="tema do usuário")
    await service.attach_document(session_id=session.id, document_id=document.id)
    suggestion = await service.create_theme_suggestion(
        session_id=session.id, provider=StubTopicProvider(theme="tema sugerido")
    )

    rejected = await service.reject_theme_suggestion(
        session_id=session.id, suggestion_id=suggestion.id
    )

    assert rejected.status is SuggestionStatus.REJECTED
    assert rejected.rejected_at is not None
    stored = await service.get_study_session(session.id)
    assert stored.theme == "tema do usuário"
    assert stored.theme_origin is ThemeOrigin.USER
    assert uow.events[-1].event_type == "ai_suggestion.rejected"


async def test_user_edits_get_distinct_event_keys() -> None:
    uow = FakeUnitOfWork([])
    service = NotelyService(lambda: uow)
    session = await service.create_study_session()

    first = await service.set_study_session_theme(session_id=session.id, theme="primeiro tema")
    second = await service.set_study_session_theme(session_id=session.id, theme="segundo tema")

    assert first.theme_origin is ThemeOrigin.USER
    assert second.theme == "segundo tema"
    theme_events = [
        event for event in uow.events if event.event_type == "study_session.theme_set"
    ]
    assert len(theme_events) == 2
    assert theme_events[0].event_key != theme_events[1].event_key


async def test_heuristic_provider_is_deterministic_and_needs_signals() -> None:
    provider = HeuristicTopicSuggestionProvider()
    context = TopicSuggestionContext(
        document_titles=("Recuperação de informação", "Recuperação semântica"),
        quotes=("Busca semântica usa embeddings",),
        comments=("Comparar estratégias de busca",),
    )

    first = await provider.suggest(context)
    second = await provider.suggest(context)

    assert first == second
    assert first.provider == "heuristic"
    assert "recuperação" in first.theme.lower()
    assert len(first.theme) <= 80

    with pytest.raises(TopicSuggestionUnavailableError):
        await provider.suggest(
            TopicSuggestionContext(document_titles=(), quotes=(), comments=())
        )
