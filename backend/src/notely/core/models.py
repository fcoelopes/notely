from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
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


class CurationStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    NO_SOURCE = "no_source"
    FAILED = "failed"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AISuggestionType(StrEnum):
    STUDY_SESSION_THEME = "study_session_theme"


class ConceptRelation(StrEnum):
    """Como uma anotação se liga a um conceito."""

    REFERS_TO = "refers_to"
    QUESTIONS = "questions"


class RelationProvenance(StrEnum):
    """De onde a relação veio. Inferência nunca se apresenta como autoral.

    ``USER_CONFIRMED`` é relação **inferida** que o usuário aceitou: quem a produziu foi o
    modelo, e o aceite só registra que ela passou a valer para ele. ``USER_AUTHORED`` é
    relação **produzida pelo próprio usuário** — não houve modelo no caminho. As duas
    chegam juntas ao grafo justamente para que essa diferença não se perca.
    """

    AI_INFERENCE = "ai_inference"
    USER_CONFIRMED = "user_confirmed"
    USER_AUTHORED = "user_authored"


class RelationStatus(StrEnum):
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ClaimRelationKind(StrEnum):
    """Arestas semânticas do grafo, nomeadas em caixa baixa como no banco.

    As quatro usam rótulos distintos no AGE (`SUPPORTS`, `CONTRADICTS`, `ANSWERED_BY`,
    `RELATED_TO`) e pontas diferentes: as duas primeiras ligam anotação → afirmação, a
    terceira liga dúvida → anotação e a última liga conceito ↔ conceito.
    """

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    ANSWERED_BY = "answered_by"
    RELATED_TO = "related_to"


class SessionStage(StrEnum):
    """Uma sessão nasce pendente de MFA e só vira ativa depois do segundo fator."""

    MFA_PENDING = "mfa_pending"
    ACTIVE = "active"


class AIModelProvider(StrEnum):
    """Provedores de modelo aceitos na configuração BYOK do usuário."""

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"


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
class DocumentBookmark:
    """Página escolhida pelo usuário para retomar um documento."""

    document_id: UUID
    page_number: int
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("bookmark page must be at least 1")


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
    study_session_id: UUID | None = None
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
class CurationRequest:
    annotation_id: UUID
    study_session_id: UUID
    id: UUID = field(default_factory=uuid4)
    version: int = 1
    status: CurationStatus = CurationStatus.PENDING
    attempt_count: int = 0
    last_error: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CuratedSource:
    request_id: UUID
    document_id: UUID
    page_number: int
    chunk_number: int
    chunk_sha256: str
    start_offset: int
    end_offset: int
    excerpt: str
    reason: str
    rank: int
    provider: str
    model: str
    retrieval_version: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)


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


@dataclass(frozen=True, slots=True)
class Concept:
    """Conceito nomeado. Vive no PostgreSQL; o grafo recebe apenas a projeção."""

    name: str
    normalized_name: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("concept name is required")
        if not self.normalized_name.strip():
            raise ValueError("concept normalized_name is required")


@dataclass(frozen=True, slots=True)
class AnnotationConcept:
    """Relação entre uma anotação e um conceito, com proveniência explícita.

    Nasce como inferência de modelo e só passa a autoral por aceite do usuário
    (AGENTS.md §1). Enquanto sugerida, não representa o que o usuário afirmou.
    """

    annotation_id: UUID
    concept_id: UUID
    relation: ConceptRelation
    provider: str
    model: str
    provenance: RelationProvenance = RelationProvenance.AI_INFERENCE
    status: RelationStatus = RelationStatus.SUGGESTED
    confidence: float | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    accepted_at: datetime | None = None

    def __post_init__(self) -> None:
        for label, value in (("provider", self.provider), ("model", self.model)):
            if not value.strip():
                raise ValueError(f"{label} is required for an extracted relation")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if (self.status is RelationStatus.ACCEPTED) != (self.accepted_at is not None):
            raise ValueError("accepted_at must be set only for accepted relations")


@dataclass(frozen=True, slots=True)
class Claim:
    """Afirmação do leitor: a proposição que ele sustenta a partir do que leu.

    Conhecimento autoral, com proveniência autoral por padrão. Uma sugestão de modelo entra
    como `ai_inference` e só se torna autoral por aceite explícito (AGENTS.md §1); o serviço
    que atende a rota de afirmação grava `accepted` porque foi o usuário quem escreveu.

    A evidência não mora aqui, e sim em `ClaimRelation`: a mesma afirmação pode ser
    sustentada por vários trechos. ``normalized_text`` é a chave de reaproveitamento —
    afirmar o mesmo texto duas vezes reaproveita a afirmação. Ela não é derivada aqui: quem
    normaliza é `notely.core.claims.normalize_claim_text`.
    """

    text: str
    normalized_text: str
    provenance: RelationProvenance = RelationProvenance.USER_AUTHORED
    # Espelha `AnnotationConcept`: o estado inicial é sugerido e o aceite é um ato
    # separado, que grava `accepted_at`. Afirmar pela API é um aceite explícito.
    status: RelationStatus = RelationStatus.SUGGESTED
    provider: str | None = None
    model: str | None = None
    confidence: float | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    accepted_at: datetime | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("claim text is required")
        if not self.normalized_text.strip():
            raise ValueError("claim normalized_text is required")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        _require_inference_source(self.provenance, self.provider, self.model, "claim")
        if (self.status is RelationStatus.ACCEPTED) != (self.accepted_at is not None):
            raise ValueError("accepted_at must be set only for accepted claims")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(frozen=True, slots=True)
class ClaimRelation:
    """Aresta semântica, tipada por `relation` e com chaves estrangeiras reais por ponta.

    Cada relação usa exatamente um par de pontas, como os CHECKs do banco exigem:
    `supports`/`contradicts` usam `annotation_id` → `claim_id`, `answered_by` usa
    `question_annotation_id` → `answer_annotation_id` e `related_to` usa
    `source_concept_id` ↔ `target_concept_id`.

    ``question_annotation_id`` é o mesmo campo de ponta usado por `ANSWERED_BY`; ele não
    aparece nas demais relações. As pontas não são validadas contra o banco aqui — quem faz
    isso é o serviço, que confirma que a anotação, a afirmação ou o conceito existem **e
    pertencem ao dono da unidade de trabalho**.
    """

    relation: ClaimRelationKind
    provenance: RelationProvenance = RelationProvenance.USER_AUTHORED
    status: RelationStatus = RelationStatus.SUGGESTED
    annotation_id: UUID | None = None
    claim_id: UUID | None = None
    question_annotation_id: UUID | None = None
    answer_annotation_id: UUID | None = None
    source_concept_id: UUID | None = None
    target_concept_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    confidence: float | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    accepted_at: datetime | None = None

    def __post_init__(self) -> None:
        endpoints = _endpoints(self)
        if len(endpoints) != 2:
            raise ValueError("a claim relation needs exactly two endpoints")
        if self.relation in (ClaimRelationKind.SUPPORTS, ClaimRelationKind.CONTRADICTS):
            if self.annotation_id is None or self.claim_id is None:
                raise ValueError(f"{self.relation.value} requires annotation_id and claim_id")
        elif self.relation is ClaimRelationKind.ANSWERED_BY:
            if self.question_annotation_id is None or self.answer_annotation_id is None:
                raise ValueError(
                    "answered_by requires question_annotation_id and answer_annotation_id"
                )
        elif self.relation is ClaimRelationKind.RELATED_TO:
            if self.source_concept_id is None or self.target_concept_id is None:
                raise ValueError("related_to requires source_concept_id and target_concept_id")
        if self.question_annotation_id is not None and (
            self.question_annotation_id == self.answer_annotation_id
        ):
            raise ValueError("a question cannot answer itself")
        if self.source_concept_id is not None and self.source_concept_id == self.target_concept_id:
            raise ValueError("a concept cannot be related to itself")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        _require_inference_source(self.provenance, self.provider, self.model, "claim relation")
        if (self.status is RelationStatus.ACCEPTED) != (self.accepted_at is not None):
            raise ValueError("accepted_at must be set only for accepted relations")


class _EndpointCarrier(Protocol):
    """Só as pontas, para as checagens compartilhadas das arestas."""

    annotation_id: UUID | None
    claim_id: UUID | None
    question_annotation_id: UUID | None
    answer_annotation_id: UUID | None
    source_concept_id: UUID | None
    target_concept_id: UUID | None


def _endpoints(relation: _EndpointCarrier) -> list[UUID]:
    """As pontas preenchidas, na ordem das colunas — o banco exige exatamente duas."""
    return [
        value
        for value in (
            relation.annotation_id,
            relation.claim_id,
            relation.question_annotation_id,
            relation.answer_annotation_id,
            relation.source_concept_id,
            relation.target_concept_id,
        )
        if value is not None
    ]


def _require_inference_source(
    provenance: RelationProvenance, provider: str | None, model: str | None, label: str
) -> None:
    """Reproduz o CHECK `*_inference_source`: inferência registra quem inferiu.

    O caminho inverso também vale: registro autoral não carrega provedor nem modelo, porque
    não houve modelo — aceitar um `provider` ali seria provenance ambígua no grafo.
    """
    if provenance is RelationProvenance.AI_INFERENCE:
        for name, value in (("provider", provider), ("model", model)):
            if value is None or not value.strip():
                raise ValueError(f"{name} is required for an inferred {label}")
        return
    if provider is not None or model is not None:
        raise ValueError(f"provider and model are only set for an inferred {label}")


@dataclass(frozen=True, slots=True)
class User:
    """Conta do usuário. A senha é opcional até o bootstrap definir uma."""

    email: str
    normalized_email: str
    password_hash: str | None = None
    totp_secret_encrypted: str | None = None
    totp_enabled: bool = False
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.email.strip():
            raise ValueError("email is required")
        if not self.normalized_email.strip():
            raise ValueError("normalized_email is required")
        if self.totp_enabled and not self.totp_secret_encrypted:
            raise ValueError("totp_enabled requires a stored secret")


@dataclass(frozen=True, slots=True)
class AuthSession:
    """Sessão opaca. O token em claro só existe no cookie do cliente."""

    user_id: UUID
    token_hash: str
    expires_at: datetime
    stage: SessionStage = SessionStage.ACTIVE
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.token_hash.strip():
            raise ValueError("token_hash is required")

    def is_valid_at(self, moment: datetime) -> bool:
        return (
            self.stage is SessionStage.ACTIVE
            and self.revoked_at is None
            and self.expires_at > moment
        )


@dataclass(frozen=True, slots=True)
class RecoveryCode:
    """Código de recuperação de uso único, guardado apenas como hash."""

    user_id: UUID
    code_hash: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    used_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.code_hash.strip():
            raise ValueError("code_hash is required")

    @property
    def is_used(self) -> bool:
        return self.used_at is not None


@dataclass(frozen=True, slots=True)
class AIModelConfig:
    """Configuração de modelo do usuário (BYOK).

    A chave viaja cifrada: o domínio só a transporta, quem cifra e decifra é a camada de
    segurança. A chave em claro nunca é devolvida pela API.
    """

    provider: AIModelProvider
    model: str
    api_key_encrypted: str | None = None
    base_url: str | None = None
    enabled: bool = True
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model is required")
        if self.base_url is not None and not self.base_url.strip():
            raise ValueError("base_url cannot be blank when provided")


@dataclass(frozen=True, slots=True)
class CorpusPassage:
    """Trecho do corpus recuperado para ancorar uma resposta. É corpus, não conhecimento."""

    document_id: UUID
    document_title: str
    page_number: int
    excerpt: str
    score: float = 0.0

