from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from notely.core.models import AnnotationSource, AnnotationType, AuthorType


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

