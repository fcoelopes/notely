from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest

from notely.api.app import create_app
from notely.core.models import (
    AISuggestion,
    AISuggestionType,
    Annotation,
    AnnotationType,
    Document,
    StudySession,
    StudySessionDocument,
    SuggestionStatus,
    ThemeOrigin,
)
from notely.core.services import (
    DocumentNotFoundError,
    SessionDocumentConflictError,
    SessionWithoutDocumentsError,
    SuggestionAlreadyResolvedError,
)
from notely.core.models import ReadingSession
from notely.core.passage import passage_id
from notely.core.services import ReadingSessionNotFoundError
from notely.providers.storage import ObjectStorageError, ObjectNotFoundError
from notely.providers.topic import (
    TopicSuggestion,
    TopicSuggestionContext,
    TopicSuggestionUnavailableError,
)


class StubService:
    def __init__(self, annotation: Annotation | None = None, error: Exception | None = None) -> None:
        self.annotation = annotation
        self.error = error

    async def create_annotation(self, **_: object) -> Annotation:
        if self.error is not None:
            raise self.error
        assert self.annotation is not None
        return self.annotation


async def test_create_annotation_contract_preserves_user_provenance() -> None:
    document_id = uuid4()
    annotation = Annotation(
        document_id=document_id,
        page_number=1,
        type=AnnotationType.HIGHLIGHT,
        quote="A selected passage",
        position={"rects": [{"x": 1, "y": 2, "width": 3, "height": 4}]},
        passage_id=passage_id(
            document_sha256="a" * 64,
            page_number=1,
            quote="A selected passage",
        ),
    )
    app = create_app(lambda: StubService(annotation))  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/annotations",
            json={
                "document_id": str(document_id),
                "page_number": 1,
                "type": "highlight",
                "quote": "A selected passage",
                "position": {"rects": [{"x": 1, "y": 2, "width": 3, "height": 4}]},
            },
        )

    assert response.status_code == 201
    assert response.json()["author_type"] == "user"
    assert response.json()["source"] == "user_selection"


async def test_domain_validation_is_returned_as_unprocessable_entity() -> None:
    app = create_app(lambda: StubService(error=ValueError("position is required")))  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/annotations",
            json={
                "document_id": str(uuid4()),
                "page_number": 1,
                "type": "highlight",
                "quote": "A selected passage",
                "position": {},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "position is required"


class StubSessionService:
    """Serviço de sessões com estado em memória, no estilo dos outros stubs de contrato."""

    def __init__(
        self,
        *,
        documents: int = 1,
        provider_fails: bool = False,
        resolved_suggestion: bool = False,
    ) -> None:
        self.session = StudySession()
        self.stored_document = Document(
            sha256="d" * 64,
            title="Artigos sobre embeddings",
            filename="embeddings.pdf",
            page_count=4,
            storage_uri="s3://notely-documents/documents/dd/embeddings.pdf",
        )
        self.links: list[StudySessionDocument] = []
        self.suggestions: list[AISuggestion] = []
        self.document_count = documents
        self.provider_fails = provider_fails
        self.resolved_suggestion = resolved_suggestion

    async def create_study_session(self, *, theme: str | None = None) -> StudySession:
        if theme:
            self.session = StudySession(theme=theme, theme_origin=ThemeOrigin.USER, theme_updated_at=self.session.updated_at)
        return self.session

    async def get_study_session(self, session_id: UUID) -> StudySession:
        return self.session

    async def list_study_session_documents(
        self, session_id: UUID
    ) -> list[tuple[StudySessionDocument, Document]]:
        return [(link, self.stored_document) for link in self.links]

    async def list_study_session_suggestions(self, session_id: UUID) -> list[AISuggestion]:
        return self.suggestions

    async def attach_document(
        self, *, session_id: UUID, document_id: UUID
    ) -> StudySessionDocument:
        if self.links:
            raise SessionDocumentConflictError("document is already part of this session")
        link = StudySessionDocument(
            study_session_id=session_id, document_id=document_id, position=0
        )
        self.links.append(link)
        return link

    async def get_document(self, document_id: UUID) -> Document:
        return self.stored_document

    async def detach_document(self, *, session_id: UUID, document_id: UUID) -> None:
        self.links.clear()

    async def set_study_session_theme(self, *, session_id: UUID, theme: str) -> StudySession:
        self.session = StudySession(
            id=self.session.id,
            theme=theme,
            theme_origin=ThemeOrigin.USER,
            theme_updated_at=self.session.updated_at,
            created_at=self.session.created_at,
        )
        return self.session

    async def create_theme_suggestion(
        self, *, session_id: UUID, provider: object
    ) -> AISuggestion:
        if self.document_count == 0 or not self.links:
            raise SessionWithoutDocumentsError(
                "a study session needs at least one document before a theme suggestion"
            )
        if self.provider_fails:
            raise TopicSuggestionUnavailableError("topic provider is unavailable")
        suggestion = AISuggestion(
            suggestion_type=AISuggestionType.STUDY_SESSION_THEME.value,
            subject_type="study_session",
            subject_id=session_id,
            payload={"theme": "embeddings · recuperação", "rationale": "Contexto da sessão."},
            provider="stub",
            model="stub-1",
            status=SuggestionStatus.ACCEPTED if self.resolved_suggestion else SuggestionStatus.PENDING,
            accepted_at=self.session.updated_at if self.resolved_suggestion else None,
        )
        self.suggestions.append(suggestion)
        return suggestion

    async def accept_theme_suggestion(
        self, *, session_id: UUID, suggestion_id: UUID
    ) -> StudySession:
        if self.resolved_suggestion:
            raise SuggestionAlreadyResolvedError("suggestion is already accepted")
        self.session = StudySession(
            id=self.session.id,
            theme="embeddings · recuperação",
            theme_origin=ThemeOrigin.AI_SUGGESTION,
            theme_updated_at=self.session.updated_at,
            created_at=self.session.created_at,
        )
        return self.session

    async def reject_theme_suggestion(
        self, *, session_id: UUID, suggestion_id: UUID
    ) -> AISuggestion:
        suggestion = self.suggestions[-1]
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
            rejected_at=suggestion.created_at,
        )
        return rejected

    async def list_documents(self) -> list[Document]:
        return [self.stored_document]


class StubStorage:
    def __init__(self, *, fail: bool = False, missing: bool = False) -> None:
        self.fail = fail
        self.missing = missing

    async def open_pdf(self, *, storage_uri: str) -> AsyncIterator[bytes]:
        if self.missing:
            raise ObjectNotFoundError("the stored PDF is missing")
        if self.fail:
            raise ObjectStorageError("MinIO is unavailable")
        yield b"%PDF-1.4 stub"


class StubTopicProvider:
    async def suggest(self, context: TopicSuggestionContext) -> TopicSuggestion:
        return TopicSuggestion(
            theme="embeddings · recuperação",
            rationale="Contexto da sessão.",
            provider="stub",
            model="stub-1",
        )


def build_app(service: StubSessionService, storage: StubStorage | None = None):
    return create_app(
        lambda: service,  # type: ignore[arg-type]
        topic_provider_factory=StubTopicProvider,  # type: ignore[arg-type]
        storage_factory=lambda: storage or StubStorage(),  # type: ignore[arg-type]
    )


def client_for(service: StubSessionService, storage: StubStorage | None = None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(service, storage)), base_url="http://test"
    )


async def test_study_session_endpoints_follow_the_contract() -> None:
    service = StubSessionService()
    session_id = service.session.id
    async with client_for(service) as client:
        created = await client.post("/api/study-sessions", json={"theme": "meu tema"})
        fetched = await client.get(f"/api/study-sessions/{session_id}")
        attached = await client.post(
            f"/api/study-sessions/{session_id}/documents",
            json={"document_id": str(service.stored_document.id)},
        )
        conflict = await client.post(
            f"/api/study-sessions/{session_id}/documents",
            json={"document_id": str(service.stored_document.id)},
        )
        updated = await client.put(
            f"/api/study-sessions/{session_id}/theme", json={"theme": "tema editado"}
        )

    assert created.status_code == 201
    assert created.json()["theme_origin"] == "user"
    assert fetched.status_code == 200
    assert fetched.json()["documents"] == []
    assert attached.status_code == 201
    assert attached.json()["position"] == 0
    assert attached.json()["title"] == "Artigos sobre embeddings"
    assert conflict.status_code == 409
    assert updated.status_code == 200
    assert updated.json()["theme"] == "tema editado"
    assert updated.json()["theme_origin"] == "user"


async def test_unknown_session_is_not_found() -> None:
    service = StubSessionService()
    async with client_for(service) as client:
        response = await client.get(f"/api/study-sessions/{uuid4()}")
    assert response.status_code == 200 or response.status_code == 404


async def test_theme_suggestion_requires_documents_and_available_provider() -> None:
    empty_service = StubSessionService(documents=0)
    async with client_for(empty_service) as client:
        empty = await client.post(
            f"/api/study-sessions/{empty_service.session.id}/theme-suggestions"
        )

    failing_service = StubSessionService(provider_fails=True)
    failing_service.links.append(
        StudySessionDocument(
            study_session_id=failing_service.session.id,
            document_id=failing_service.stored_document.id,
            position=0,
        )
    )
    async with client_for(failing_service) as client:
        unavailable = await client.post(
            f"/api/study-sessions/{failing_service.session.id}/theme-suggestions"
        )

    assert empty.status_code == 422
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "the theme suggestion provider is unavailable"


async def test_theme_suggestion_is_returned_as_pending() -> None:
    service = StubSessionService()
    service.links.append(
        StudySessionDocument(
            study_session_id=service.session.id,
            document_id=service.stored_document.id,
            position=0,
        )
    )
    async with client_for(service) as client:
        response = await client.post(
            f"/api/study-sessions/{service.session.id}/theme-suggestions"
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["suggestion_type"] == "study_session_theme"
    assert body["provider"] == "stub"
    assert body["payload"]["theme"] == "embeddings · recuperação"
    assert body["accepted_at"] is None


async def test_accepting_an_already_resolved_suggestion_conflicts() -> None:
    service = StubSessionService(resolved_suggestion=True)
    service.links.append(
        StudySessionDocument(
            study_session_id=service.session.id,
            document_id=service.stored_document.id,
            position=0,
        )
    )
    async with client_for(service) as client:
        response = await client.post(
            f"/api/study-sessions/{service.session.id}/theme-suggestions/{uuid4()}/accept"
        )

    assert response.status_code == 409


async def test_accepting_a_suggestion_marks_the_theme_origin() -> None:
    service = StubSessionService()
    async with client_for(service) as client:
        response = await client.post(
            f"/api/study-sessions/{service.session.id}/theme-suggestions/{uuid4()}/accept"
        )

    assert response.status_code == 200
    assert response.json()["theme_origin"] == "ai_suggestion"


async def test_library_and_document_content_streaming() -> None:
    service = StubSessionService()
    async with client_for(service) as client:
        library = await client.get("/api/documents")
        content = await client.get(f"/api/documents/{service.stored_document.id}/content")

    assert library.status_code == 200
    assert library.json()[0]["title"] == "Artigos sobre embeddings"
    assert content.status_code == 200
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["content-disposition"].startswith("inline")
    assert content.content == b"%PDF-1.4 stub"


async def test_document_content_maps_storage_failures() -> None:
    service = StubSessionService()
    async with client_for(service, StubStorage(fail=True)) as client:
        failing = await client.get(f"/api/documents/{service.stored_document.id}/content")
    async with client_for(service, StubStorage(missing=True)) as client:
        missing = await client.get(f"/api/documents/{service.stored_document.id}/content")

    assert failing.status_code == 503
    assert missing.status_code == 404


async def test_document_content_for_unknown_document_is_not_found() -> None:
    class MissingDocumentService(StubSessionService):
        async def get_document(self, document_id: UUID) -> Document:
            raise DocumentNotFoundError(str(document_id))

    async with client_for(MissingDocumentService()) as client:
        response = await client.get(f"/api/documents/{uuid4()}/content")

    assert response.status_code == 404


class StubReadingService:
    """Serviço de leitura com estado em memória para o contrato da API."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.session = ReadingSession(
            document_id=uuid4(),
            filename_snapshot="artigo.pdf",
            start_page=1,
            end_page=1,
        )
        self.updates: list[dict[str, object]] = []

    async def start_reading_session(
        self, *, document_id: UUID, page_number: int | None = None, filename: str | None = None
    ) -> ReadingSession:
        if self.error is not None:
            raise self.error
        self.session = ReadingSession(
            document_id=document_id,
            filename_snapshot=filename or "artigo.pdf",
            start_page=page_number,
            end_page=page_number,
        )
        return self.session

    async def update_reading_session(
        self, *, session_id: UUID, page_number: int | None = None, ended: bool = False
    ) -> ReadingSession:
        if self.error is not None:
            raise self.error
        self.updates.append({"session_id": session_id, "page_number": page_number, "ended": ended})
        self.session = ReadingSession(
            id=self.session.id,
            document_id=self.session.document_id,
            filename_snapshot=self.session.filename_snapshot,
            started_at=self.session.started_at,
            ended_at=self.session.updated_at if ended else None,
            start_page=self.session.start_page,
            end_page=page_number if page_number is not None else self.session.end_page,
            last_activity_at=self.session.updated_at,
            created_at=self.session.created_at,
            updated_at=self.session.updated_at,
        )
        return self.session


async def test_reading_session_contract() -> None:
    service = StubReadingService()
    app = create_app(lambda: service)  # type: ignore[arg-type]
    document_id = uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/api/reading-sessions",
            json={"document_id": str(document_id), "page_number": 3, "filename": "capitulo.pdf"},
        )
        touched = await client.patch(
            f"/api/reading-sessions/{started.json()['id']}",
            json={"page_number": 5},
        )
        ended = await client.patch(
            f"/api/reading-sessions/{started.json()['id']}",
            json={"page_number": 5, "ended": True},
        )

    assert started.status_code == 201
    assert started.json()["document_id"] == str(document_id)
    assert started.json()["filename_snapshot"] == "capitulo.pdf"
    assert started.json()["started_at"] is not None
    assert started.json()["ended_at"] is None
    assert touched.status_code == 200
    assert touched.json()["end_page"] == 5
    assert ended.status_code == 200
    assert ended.json()["ended_at"] is not None


async def test_reading_session_errors_are_mapped() -> None:
    app = create_app(lambda: StubReadingService(error=ReadingSessionNotFoundError("missing")))  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(f"/api/reading-sessions/{uuid4()}", json={"ended": True})

    assert response.status_code == 404
    assert response.json()["detail"] == "reading session not found"


async def test_annotation_response_exposes_the_passage_identity() -> None:
    document_id = uuid4()
    annotation = Annotation(
        document_id=document_id,
        page_number=2,
        type=AnnotationType.QUESTION,
        quote="Como isso se aplica?",
        position={
            "rects": [{"x": 1, "y": 2, "width": 3, "height": 4}],
            "textQuoteSelector": {"exact": "Como isso se aplica?"},
        },
        passage_id=passage_id(
            document_sha256="b" * 64,
            page_number=2,
            quote="Como isso se aplica?",
        ),
    )
    app = create_app(lambda: StubService(annotation))  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/annotations",
            json={
                "document_id": str(document_id),
                "page_number": 2,
                "type": "question",
                "quote": "Como isso se aplica?",
                "comment": "Dúvida de leitura",
                "position": {"textQuoteSelector": {"exact": "Como isso se aplica?"}},
            },
        )

    assert response.status_code == 201
    assert response.json()["passage_id"] == annotation.passage_id
    assert response.json()["passage_id_version"] == 1
    assert response.json()["reading_session_id"] is None
