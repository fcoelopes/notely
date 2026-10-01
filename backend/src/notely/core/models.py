from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class AnnotationType(StrEnum):
    HIGHLIGHT = "highlight"
    NOTE = "note"
    QUESTION = "question"
    IMPORTANT = "important"
    DISAGREEMENT = "disagreement"
    RELATION = "relation"


class AnnotationSource(StrEnum):
    USER_SELECTION = "user_selection"
    NATIVE_PDF = "native_pdf"
    IMPORT = "import"


class AuthorType(StrEnum):
    USER = "user"


@dataclass(frozen=True, slots=True)
class Document:
    sha256: str
    title: str
    filename: str
    page_count: int
    storage_uri: str
    id: UUID = field(default_factory=uuid4)
    mime_type: str = "application/pdf"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if len(self.sha256) != 64:
            raise ValueError("sha256 must contain 64 hexadecimal characters")
        try:
            int(self.sha256, 16)
        except ValueError as exc:
            raise ValueError("sha256 must contain 64 hexadecimal characters") from exc
        if not self.title.strip() or not self.filename.strip():
            raise ValueError("title and filename are required")
        if self.page_count < 1:
            raise ValueError("page_count must be at least 1")


@dataclass(frozen=True, slots=True)
class Annotation:
    document_id: UUID
    page_number: int
    type: AnnotationType
    quote: str
    position: dict[str, Any]
    id: UUID = field(default_factory=uuid4)
    comment: str | None = None
    source: AnnotationSource = AnnotationSource.USER_SELECTION
    author_type: AuthorType = AuthorType.USER
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number must be at least 1")
        if not self.quote.strip():
            raise ValueError("quote is required")
        if not self.position:
            raise ValueError("position is required to restore the annotation")


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any]
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    available_at: datetime = field(default_factory=utc_now)

