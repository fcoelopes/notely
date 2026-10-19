from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from notely.core.models import (
    AIModelProvider,
    AnnotationSource,
    AnnotationType,
    ClaimRelationKind,
    CurationStatus,
    AuthorType,
    RelationProvenance,
    RelationStatus,
    SessionStage,
    SuggestionStatus,
    ThemeOrigin,
)

MAX_THEME_LENGTH = 200
MAX_CLAIM_LENGTH = 2000


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


class DocumentBookmarkSet(BaseModel):
    page_number: int = Field(ge=1)


class DocumentBookmarkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    page_number: int
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
    reading_session_id: UUID | None = None
    study_session_id: UUID | None = None


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    page_number: int
    type: AnnotationType
    quote: str
    comment: str | None
    position: dict[str, Any]
    passage_id: str
    passage_id_version: int
    source: AnnotationSource
    author_type: AuthorType
    reading_session_id: UUID | None
    study_session_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ReadingSessionStart(BaseModel):
    document_id: UUID
    page_number: int | None = Field(default=None, ge=1)
    filename: str | None = Field(default=None, max_length=400)


class ReadingSessionUpdate(BaseModel):
    page_number: int | None = Field(default=None, ge=1)
    ended: bool = False


class ReadingSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    filename_snapshot: str
    started_at: datetime
    ended_at: datetime | None
    start_page: int | None
    end_page: int | None
    last_activity_at: datetime
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


class CuratedSourceResponse(BaseModel):
    id: UUID
    document_id: UUID
    document_title: str
    page_number: int
    excerpt: str
    reason: str
    rank: int
    provider: str
    model: str
    available: bool


class QuestionSourcesResponse(BaseModel):
    annotation_id: UUID
    study_session_id: UUID
    status: CurationStatus
    version: int
    last_error: str | None
    sources: list[CuratedSourceResponse]


class PageProgressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    viewed_pages: int
    total_pages: int
    percent: int


class SessionProgressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    progress: PageProgressResponse
    documents: dict[UUID, PageProgressResponse]


class ReadingProgressSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    documents: dict[UUID, PageProgressResponse]
    sessions: dict[UUID, SessionProgressResponse]


class ReadingProgressUpdateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    study_session_id: UUID
    document_id: UUID
    session: SessionProgressResponse
    document_global: PageProgressResponse


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    totp_enabled: bool


class AuthSessionResponse(BaseModel):
    """`mfa_required` diz ao Reader que a sessão ainda não vale para ler."""

    stage: SessionStage
    mfa_required: bool
    user: UserResponse


class EnrollmentResponse(BaseModel):
    secret: str
    uri: str


class RecoveryCodesResponse(BaseModel):
    """Exibidos uma única vez; o servidor guarda apenas o hash."""

    recovery_codes: list[str]


class DisableMfaRequest(BaseModel):
    # Desativar o segundo fator exige a senha: uma sessão roubada não basta.
    password: str = Field(min_length=1, max_length=1024)


class AIModelConfigRequest(BaseModel):
    provider: AIModelProvider
    model: str = Field(min_length=1, max_length=200)
    # Sem chave, a que já está guardada é mantida; a chave nunca é devolvida.
    api_key: str | None = Field(default=None, max_length=400)
    base_url: str | None = Field(default=None, max_length=500)
    enabled: bool = True


class AIModelConfigResponse(BaseModel):
    provider: AIModelProvider
    model: str
    base_url: str | None
    enabled: bool
    has_key: bool


class AIModelListRequest(BaseModel):
    """Pedido da lista de modelos: a chave digitada, se houver, vale só para esta chamada."""

    provider: AIModelProvider
    api_key: str | None = Field(default=None, max_length=400)
    base_url: str | None = Field(default=None, max_length=500)


class AIModelListItemResponse(BaseModel):
    """Modelo oferecido pelo provedor: o id que se grava e, quando houver, o nome exibido."""

    id: str
    display_name: str | None = None


class AIModelListResponse(BaseModel):
    models: list[AIModelListItemResponse] = Field(default_factory=list)


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    study_session_id: UUID | None = None
    document_id: UUID | None = None


class AssistantSourceResponse(BaseModel):
    document_id: UUID
    document_title: str
    page_number: int
    excerpt: str


class AssistantAnswerResponse(BaseModel):
    """Resposta de modelo. `grounded` diz se ela se apoiou no material do usuário."""

    answer: str
    provider: str
    model: str
    grounded: bool
    sources: list[AssistantSourceResponse]


class ClaimAssertion(BaseModel):
    """Afirmação a partir de uma anotação: o trecho sustenta ou contradiz o que se afirma."""

    annotation_id: UUID
    text: str = Field(min_length=1, max_length=MAX_CLAIM_LENGTH)
    relation: ClaimRelationKind = ClaimRelationKind.SUPPORTS


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    text: str
    normalized_text: str
    provenance: RelationProvenance
    status: RelationStatus
    provider: str | None
    model: str | None
    confidence: float | None
    created_at: datetime
    updated_at: datetime
    accepted_at: datetime | None


class ClaimEvidenceResponse(BaseModel):
    """Afirmação com a evidência que a sustenta.

    `relation` é a aresta que liga o trecho à afirmação, com proveniência e status. Ela é nula
    quando a afirmação ficou sem evidência — a última aresta foi removida —, porque o dado
    autoral continua sendo do usuário mesmo sem nenhum trecho apontando para ele. O trecho
    também vem vazio quando a anotação de origem não existe mais no acervo.
    """

    claim: ClaimResponse
    relation: ClaimRelationKind | None = None
    relation_id: UUID | None = None
    relation_provenance: RelationProvenance | None = None
    relation_status: RelationStatus | None = None
    annotation_id: UUID | None
    document_id: UUID | None
    document_title: str | None
    page_number: int | None
    quote: str
    comment: str | None


class ClaimRelationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    relation: ClaimRelationKind
    provenance: RelationProvenance
    status: RelationStatus
    annotation_id: UUID | None
    claim_id: UUID | None
    question_annotation_id: UUID | None
    answer_annotation_id: UUID | None
    source_concept_id: UUID | None
    target_concept_id: UUID | None
    created_at: datetime
    accepted_at: datetime | None


class AnsweredByRequest(BaseModel):
    answer_annotation_id: UUID


class ConceptRelationRequest(BaseModel):
    source_concept_id: UUID
    target_concept_id: UUID


class ConceptReferenceResponse(BaseModel):
    """Documento cujo acervo cita o conceito, com quantos trechos o citam ali."""

    document_id: UUID
    document_title: str
    annotation_count: int


class ConceptAssociationResponse(BaseModel):
    """Associação `RELATED_TO` vista de uma das pontas.

    O par é ordenado no banco: `outgoing` diz se o conceito listado é a origem da aresta, e
    associar na ordem inversa cria outra aresta. A proveniência e o status vêm junto para que
    a tela não apresente como autoral o que não é.
    """

    relation_id: UUID
    source_concept_id: UUID
    target_concept_id: UUID
    other_concept_id: UUID
    other_concept_name: str
    outgoing: bool
    provenance: RelationProvenance
    status: RelationStatus
    created_at: datetime


class ConceptOverviewResponse(BaseModel):
    """Conceito extraído por modelo ou heurística: inferência, não conhecimento confirmado.

    `annotation_count` e `documents` são o lastro que a pessoa tem para decidir uma
    associação, e `associations` são as `RELATED_TO` que já existem tocando este conceito,
    em qualquer das duas pontas.
    """

    id: UUID
    name: str
    annotation_count: int
    documents: list[ConceptReferenceResponse] = Field(default_factory=list)
    associations: list[ConceptAssociationResponse] = Field(default_factory=list)
