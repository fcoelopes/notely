from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from notely.core.models import (
    AnnotationSource,
    AnnotationType,
    AuthorType,
    SuggestionStatus,
    ThemeOrigin,
)

MAX_THEME_LENGTH = 200


class DocumentCreate(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    title: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    page_count: int = Field(ge=1)
    storage_uri: str = Field(min_length=1)


class DocumentResponse(DocumentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mime_type: str
    created_at: datetime
    updated_at: datetime


class AnnotationCreate(BaseModel):
    document_id: UUID
    page_number: int = Field(ge=1)
    type: AnnotationType
    quote: str = Field(min_length=1)
    comment: str | None = None
    position: dict[str, Any]
    source: AnnotationSource = AnnotationSource.USER_SELECTION


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    page_number: int
    type: AnnotationType
    quote: str
    comment: str | None
    position: dict[str, Any]
    source: AnnotationSource
    author_type: AuthorType
    created_at: datetime
    updated_at: datetime


class StudySessionCreate(BaseModel):
    theme: str | None = Field(default=None, max_length=MAX_THEME_LENGTH)


class StudySessionThemeUpdate(BaseModel):
    theme: str = Field(min_length=1, max_length=MAX_THEME_LENGTH)


class SessionDocumentCreate(BaseModel):
    document_id: UUID


class StudySessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    theme: str | None
    theme_origin: ThemeOrigin | None
    theme_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StudySessionDocumentResponse(BaseModel):
    id: UUID
    study_session_id: UUID
    document_id: UUID
    position: int
    added_at: datetime
    title: str
    filename: str
    page_count: int


class AISuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    suggestion_type: str
    subject_type: str
    subject_id: UUID
    status: SuggestionStatus
    payload: dict[str, Any]
    provider: str
    model: str
    created_at: datetime
    accepted_at: datetime | None
    rejected_at: datetime | None


class StudySessionDetailResponse(StudySessionResponse):
    documents: list[StudySessionDocumentResponse] = Field(default_factory=list)
    suggestions: list[AISuggestionResponse] = Field(default_factory=list)
