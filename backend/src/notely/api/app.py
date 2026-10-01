from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from notely.api.schemas import AnnotationCreate, AnnotationResponse, DocumentCreate, DocumentResponse
from notely.core.ingestion import (
    DocumentIngestionService,
    DocumentTooLargeError,
    InvalidDocumentError,
)
from notely.core.services import (
    DocumentConflictError,
    DocumentNotFoundError,
    NotelyService,
    PageOutsideDocumentError,
)
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.providers.malware import ClamAVScanner, MalwareDetectedError, MalwareScanError
from notely.providers.storage import MinioObjectStorage, ObjectStorageError


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


def create_app(
    service_factory: Callable[[], NotelyService] | None = None,
    ingestion_factory: Callable[[], DocumentIngestionService] | None = None,
) -> FastAPI:
    settings = Settings()
    engine = None

    if service_factory is None:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        service_factory = lambda: NotelyService(lambda: SqlAlchemyUnitOfWork(sessions))

    if ingestion_factory is None:
        scanner = ClamAVScanner(
            settings.clamav_host,
            settings.clamav_port,
            settings.clamav_timeout_seconds,
        )
        storage = MinioObjectStorage(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )

        def default_ingestion_factory() -> DocumentIngestionService:
            assert service_factory is not None
            return DocumentIngestionService(
                documents=service_factory(),
                scanner=scanner,
                storage=storage,
                max_upload_bytes=settings.max_upload_bytes,
            )

        ingestion_factory = default_ingestion_factory

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="Notely API", version="0.2.0", lifespan=lifespan)

    def get_service() -> NotelyService:
        assert service_factory is not None
        return service_factory()

    def get_ingestion() -> DocumentIngestionService:
        assert ingestion_factory is not None
        return ingestion_factory()

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

    return app


app = create_app()
