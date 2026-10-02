from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from notely.api.schemas import (
    AISuggestionResponse,
    AnnotationCreate,
    AnnotationResponse,
    DocumentCreate,
    DocumentResponse,
    SessionDocumentCreate,
    StudySessionCreate,
    StudySessionDetailResponse,
    StudySessionDocumentResponse,
    StudySessionResponse,
    StudySessionThemeUpdate,
)
from notely.core.ingestion import (
    DocumentIngestionService,
    DocumentTooLargeError,
    InvalidDocumentError,
)
from notely.core.models import AISuggestion, Document, StudySession, StudySessionDocument
from notely.core.services import (
    DocumentConflictError,
    DocumentNotFoundError,
    NotelyService,
    PageOutsideDocumentError,
    SessionDocumentConflictError,
    SessionDocumentNotFoundError,
    SessionWithoutDocumentsError,
    StudySessionNotFoundError,
    SuggestionAlreadyResolvedError,
    SuggestionNotFoundError,
)
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.providers.malware import ClamAVScanner, MalwareDetectedError, MalwareScanError
from notely.providers.storage import (
    MinioObjectStorage,
    ObjectNotFoundError,
    ObjectStorageError,
)
from notely.providers.topic import (
    HeuristicTopicSuggestionProvider,
    OpenAICompatibleTopicSuggestionProvider,
    TopicSuggestionProvider,
    TopicSuggestionUnavailableError,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTELY_", env_file=".env")

    database_url: str = "postgresql+asyncpg://notely:notely@localhost:5432/notely"
    clamav_host: str = "localhost"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 30
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "notely"
    minio_secret_key: str = "notely-development-only"
    minio_bucket: str = "notely-documents"
    minio_secure: bool = False
    max_upload_bytes: int = 100 * 1024 * 1024
    topic_provider: str = "heuristic"
    topic_endpoint: str = ""
    topic_model: str = ""
    topic_api_key: str = ""
    topic_timeout_seconds: float = 30.0


def build_topic_provider(settings: Settings) -> TopicSuggestionProvider:
    if settings.topic_provider == "heuristic":
        return HeuristicTopicSuggestionProvider()
    if settings.topic_provider == "openai_compatible":
        return OpenAICompatibleTopicSuggestionProvider(
            endpoint=settings.topic_endpoint,
            model=settings.topic_model,
            api_key=settings.topic_api_key,
            timeout_seconds=settings.topic_timeout_seconds,
        )
    raise ValueError(f"unsupported topic provider: {settings.topic_provider}")


def create_app(
    service_factory: Callable[[], NotelyService] | None = None,
    ingestion_factory: Callable[[], DocumentIngestionService] | None = None,
    topic_provider_factory: Callable[[], TopicSuggestionProvider] | None = None,
    storage_factory: Callable[[], MinioObjectStorage] | None = None,
) -> FastAPI:
    settings = Settings()
    engine = None

    if service_factory is None:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        service_factory = lambda: NotelyService(lambda: SqlAlchemyUnitOfWork(sessions))

    if storage_factory is None:
        storage_factory = lambda: MinioObjectStorage(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )

    if ingestion_factory is None:
        scanner = ClamAVScanner(
            settings.clamav_host,
            settings.clamav_port,
            settings.clamav_timeout_seconds,
        )

        def default_ingestion_factory() -> DocumentIngestionService:
            assert service_factory is not None
            return DocumentIngestionService(
                documents=service_factory(),
                scanner=scanner,
                storage=storage_factory(),
                max_upload_bytes=settings.max_upload_bytes,
            )

        ingestion_factory = default_ingestion_factory

    if topic_provider_factory is None:
        topic_provider_factory = lambda: build_topic_provider(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="Notely API", version="0.3.0", lifespan=lifespan)

    def get_service() -> NotelyService:
        assert service_factory is not None
        return service_factory()

    def get_ingestion() -> DocumentIngestionService:
        assert ingestion_factory is not None
        return ingestion_factory()

    def get_storage() -> MinioObjectStorage:
        assert storage_factory is not None
        return storage_factory()

    def get_topic_provider() -> TopicSuggestionProvider:
        assert topic_provider_factory is not None
        return topic_provider_factory()

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/api/documents", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED
    )
    async def create_document(
        body: DocumentCreate, service: NotelyService = Depends(get_service)
    ) -> DocumentResponse:
        try:
            document = await service.register_document(**body.model_dump())
        except DocumentConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return DocumentResponse.model_validate(document)

    @app.get("/api/documents", response_model=list[DocumentResponse])
    async def list_documents(service: NotelyService = Depends(get_service)) -> list[DocumentResponse]:
        documents = await service.list_documents()
        return [DocumentResponse.model_validate(document) for document in documents]

    @app.get("/api/documents/{document_id}/content")
    async def download_document(
        document_id: UUID,
        service: NotelyService = Depends(get_service),
        storage: MinioObjectStorage = Depends(get_storage),
    ) -> StreamingResponse:
        try:
            document = await service.get_document(document_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found") from exc
        try:
            stream = storage.open_pdf(storage_uri=document.storage_uri)
            first_chunk = await anext(stream)

            async def body() -> AsyncIterator[bytes]:
                yield first_chunk
                async for chunk in stream:
                    yield chunk

        except ObjectNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="the stored PDF is missing",
            ) from exc
        except ObjectStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="object storage is unavailable",
            ) from exc
        return StreamingResponse(
            body(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
        )

    @app.post(
        "/api/documents/upload",
        response_model=DocumentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_document(
        file: UploadFile = File(...),
        page_count: int = Form(...),
        ingestion: DocumentIngestionService = Depends(get_ingestion),
    ) -> DocumentResponse:
        if page_count < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="page_count must be at least 1",
            )
        filename = file.filename or "document.pdf"
        try:
            document = await ingestion.ingest_pdf(
                content=file.file,
                filename=filename,
                page_count=page_count,
            )
        except InvalidDocumentError as exc:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
        except DocumentTooLargeError as exc:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
        except MalwareDetectedError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"the PDF was rejected by malware scanning ({exc.signature})",
            ) from exc
        except MalwareScanError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="malware scanning is unavailable; upload rejected",
            ) from exc
        except ObjectStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="object storage is unavailable",
            ) from exc
        finally:
            await file.close()
        return DocumentResponse.model_validate(document)

    @app.post(
        "/api/annotations",
        response_model=AnnotationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_annotation(
        body: AnnotationCreate, service: NotelyService = Depends(get_service)
    ) -> AnnotationResponse:
        try:
            annotation = await service.create_annotation(
                document_id=body.document_id,
                page_number=body.page_number,
                annotation_type=body.type,
                quote=body.quote,
                comment=body.comment,
                position=body.position,
                source=body.source,
            )
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found") from exc
        except (PageOutsideDocumentError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return AnnotationResponse.model_validate(annotation)

    @app.get(
        "/api/documents/{document_id}/annotations",
        response_model=list[AnnotationResponse],
    )
    async def list_annotations(
        document_id: UUID, service: NotelyService = Depends(get_service)
    ) -> list[AnnotationResponse]:
        try:
            annotations = await service.list_document_annotations(document_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found") from exc
        return [AnnotationResponse.model_validate(item) for item in annotations]

    @app.post(
        "/api/study-sessions",
        response_model=StudySessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_study_session(
        body: StudySessionCreate, service: NotelyService = Depends(get_service)
    ) -> StudySessionResponse:
        try:
            session = await service.create_study_session(theme=body.theme)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return StudySessionResponse.model_validate(session)

    @app.get("/api/study-sessions", response_model=list[StudySessionResponse])
    async def list_study_sessions(service: NotelyService = Depends(get_service)) -> list[StudySessionResponse]:
        sessions = await service.list_study_sessions()
        return [StudySessionResponse.model_validate(session) for session in sessions]

    @app.get(
        "/api/study-sessions/{session_id}",
        response_model=StudySessionDetailResponse,
    )
    async def get_study_session(
        session_id: UUID, service: NotelyService = Depends(get_service)
    ) -> StudySessionDetailResponse:
        try:
            session = await service.get_study_session(session_id)
            entries = await service.list_study_session_documents(session_id)
            suggestions = await service.list_study_session_suggestions(session_id)
        except StudySessionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="study session not found"
            ) from exc
        return _session_detail(session, entries, suggestions)

    @app.post(
        "/api/study-sessions/{session_id}/documents",
        response_model=StudySessionDocumentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def attach_document(
        session_id: UUID, body: SessionDocumentCreate, service: NotelyService = Depends(get_service)
    ) -> StudySessionDocumentResponse:
        try:
            link = await service.attach_document(
                session_id=session_id, document_id=body.document_id
            )
            document = await service.get_document(body.document_id)
        except StudySessionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="study session not found"
            ) from exc
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found") from exc
        except SessionDocumentConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return _session_document(link, document)

    @app.delete(
        "/api/study-sessions/{session_id}/documents/{document_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def detach_document(
        session_id: UUID, document_id: UUID, service: NotelyService = Depends(get_service)
    ) -> None:
        try:
            await service.detach_document(session_id=session_id, document_id=document_id)
        except StudySessionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="study session not found"
            ) from exc
        except SessionDocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="document is not in this session"
            ) from exc

    @app.put(
        "/api/study-sessions/{session_id}/theme",
        response_model=StudySessionResponse,
    )
    async def update_theme(
        session_id: UUID, body: StudySessionThemeUpdate, service: NotelyService = Depends(get_service)
    ) -> StudySessionResponse:
        try:
            session = await service.set_study_session_theme(
                session_id=session_id, theme=body.theme
            )
        except StudySessionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="study session not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return StudySessionResponse.model_validate(session)

    @app.post(
        "/api/study-sessions/{session_id}/theme-suggestions",
        response_model=AISuggestionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_theme_suggestion(
        session_id: UUID,
        service: NotelyService = Depends(get_service),
        provider: TopicSuggestionProvider = Depends(get_topic_provider),
    ) -> AISuggestionResponse:
        try:
            suggestion = await service.create_theme_suggestion(
                session_id=session_id, provider=provider
            )
        except StudySessionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="study session not found"
            ) from exc
        except SessionWithoutDocumentsError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        except TopicSuggestionUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="the theme suggestion provider is unavailable",
            ) from exc
        return AISuggestionResponse.model_validate(suggestion)

    @app.post(
        "/api/study-sessions/{session_id}/theme-suggestions/{suggestion_id}/accept",
        response_model=StudySessionResponse,
    )
    async def accept_theme_suggestion(
        session_id: UUID, suggestion_id: UUID, service: NotelyService = Depends(get_service)
    ) -> StudySessionResponse:
        try:
            session = await service.accept_theme_suggestion(
                session_id=session_id, suggestion_id=suggestion_id
            )
        except StudySessionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="study session not found"
            ) from exc
        except SuggestionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found") from exc
        except SuggestionAlreadyResolvedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return StudySessionResponse.model_validate(session)

    @app.post(
        "/api/study-sessions/{session_id}/theme-suggestions/{suggestion_id}/reject",
        response_model=AISuggestionResponse,
    )
    async def reject_theme_suggestion(
        session_id: UUID, suggestion_id: UUID, service: NotelyService = Depends(get_service)
    ) -> AISuggestionResponse:
        try:
            suggestion = await service.reject_theme_suggestion(
                session_id=session_id, suggestion_id=suggestion_id
            )
        except StudySessionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="study session not found"
            ) from exc
        except SuggestionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found") from exc
        except SuggestionAlreadyResolvedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return AISuggestionResponse.model_validate(suggestion)

    return app


def _session_document(
    link: StudySessionDocument, document: Document
) -> StudySessionDocumentResponse:
    return StudySessionDocumentResponse(
        id=link.id,
        study_session_id=link.study_session_id,
        document_id=link.document_id,
        position=link.position,
        added_at=link.added_at,
        title=document.title,
        filename=document.filename,
        page_count=document.page_count,
    )


def _session_detail(
    session: StudySession,
    entries: list[tuple[StudySessionDocument, Document]],
    suggestions: list[AISuggestion],
) -> StudySessionDetailResponse:
    return StudySessionDetailResponse(
        id=session.id,
        theme=session.theme,
        theme_origin=session.theme_origin,
        theme_updated_at=session.theme_updated_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
        documents=[_session_document(link, document) for link, document in entries],
        suggestions=[AISuggestionResponse.model_validate(item) for item in suggestions],
    )


app = create_app()
