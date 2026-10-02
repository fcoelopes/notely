from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from notely.core.passage import PASSAGE_ID_VERSION


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


class ThemeOrigin(StrEnum):
    USER = "user"
    AI_SUGGESTION = "ai_suggestion"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AISuggestionType(StrEnum):
    STUDY_SESSION_THEME = "study_session_theme"


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
    passage_id: str
    id: UUID = field(default_factory=uuid4)
    comment: str | None = None
    source: AnnotationSource = AnnotationSource.USER_SELECTION
    author_type: AuthorType = AuthorType.USER
    reading_session_id: UUID | None = None
    passage_id_version: int = PASSAGE_ID_VERSION
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number must be at least 1")
        if not self.quote.strip():
            raise ValueError("quote is required")
        if not self.position:
            raise ValueError("position is required to restore the annotation")
        if len(self.passage_id) != 64:
            raise ValueError("passage_id must be the hex digest of the passage anchor")
        try:
            int(self.passage_id, 16)
        except ValueError as exc:
            raise ValueError("passage_id must be the hex digest of the passage anchor") from exc
        if self.passage_id_version < 1:
            raise ValueError("passage_id_version must be at least 1")


@dataclass(frozen=True, slots=True)
class ReadingSession:
    """Uma tentativa de leitura sobre um documento.

    PostgreSQL guarda o estado atual (``started_at``, ``last_activity_at``, ``ended_at``);
    a trilha temporal em TimescaleDB guarda quando cada evento aconteceu.
    """

    document_id: UUID
    filename_snapshot: str
    started_at: datetime = field(default_factory=utc_now)
    last_activity_at: datetime = field(default_factory=utc_now)
    id: UUID = field(default_factory=uuid4)
    ended_at: datetime | None = None
    start_page: int | None = None
    end_page: int | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.filename_snapshot.strip():
            raise ValueError("filename_snapshot is required")
        if self.last_activity_at < self.started_at:
            raise ValueError("last_activity_at cannot precede started_at")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        for label, page in (("start_page", self.start_page), ("end_page", self.end_page)):
            if page is not None and page < 1:
                raise ValueError(f"{label} must be at least 1")

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True, slots=True)
class StudySession:
    """Sessão de estudo: documentos lidos em conjunto sobre um mesmo tema.

    O tema é dado autoral. Quando existe, ``theme_origin`` registra se o usuário o
    escreveu ou se ele veio de uma sugestão aceita explicitamente.
    """

    theme: str | None = None
    theme_origin: ThemeOrigin | None = None
    theme_updated_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.theme is not None and not self.theme.strip():
            raise ValueError("theme cannot be blank when provided")
        theme_set = self.theme is not None
        if theme_set != (self.theme_origin is not None) or theme_set != (
            self.theme_updated_at is not None
        ):
            raise ValueError("theme, theme_origin and theme_updated_at must be set together")


@dataclass(frozen=True, slots=True)
class StudySessionDocument:
    study_session_id: UUID
    document_id: UUID
    position: int
    id: UUID = field(default_factory=uuid4)
    added_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ValueError("position cannot be negative")


@dataclass(frozen=True, slots=True)
class AISuggestion:
    """Sugestão de modelo, sempre separada da autoria do usuário até aceite explícito."""

    suggestion_type: str
    subject_type: str
    subject_id: UUID
    payload: dict[str, Any]
    provider: str
    model: str
    status: SuggestionStatus = SuggestionStatus.PENDING
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    accepted_at: datetime | None = None
    rejected_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.payload:
            raise ValueError("payload is required for a suggestion")
        for label, value in (
            ("suggestion_type", self.suggestion_type),
            ("subject_type", self.subject_type),
            ("provider", self.provider),
            ("model", self.model),
        ):
            if not value.strip():
                raise ValueError(f"{label} is required for a suggestion")
        if (self.status is SuggestionStatus.ACCEPTED) != (self.accepted_at is not None):
            raise ValueError("accepted_at must be set only for accepted suggestions")
        if (self.status is SuggestionStatus.REJECTED) != (self.rejected_at is not None):
            raise ValueError("rejected_at must be set only for rejected suggestions")


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any]
    event_key: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    available_at: datetime = field(default_factory=utc_now)

