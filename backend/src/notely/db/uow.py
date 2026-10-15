from __future__ import annotations

from datetime import datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notely.core.models import (
    AIModelConfig,
    AIModelProvider,
    AISuggestion,
    Annotation,
    AnnotationSource,
    AnnotationType,
    AuthorType,
    Claim,
    ClaimRelation,
    ClaimRelationKind,
    Concept,
    CorpusPassage,
    CurationRequest,
    CurationStatus,
    CuratedSource,
    Document,
    DocumentBookmark,
    OutboxEvent,
    ReadingSession,
    RelationProvenance,
    RelationStatus,
    StudySession,
    StudySessionDocument,
    SuggestionStatus,
    ThemeOrigin,
)
from notely.db.assistant_search import (
    list_session_annotations as list_session_annotation_rows,
)
from notely.db.assistant_search import search_session_passages
from notely.providers.pdf_text import EXTRACTOR_VERSION
from notely.db.models import (
    AIModelConfigRow,
    AISuggestionRow,
    AnnotationConceptRow,
    AnnotationRow,
    ClaimRelationRow,
    ClaimRow,
    ConceptRow,
    CurationRequestRow,
    CuratedSourceRow,
    DocumentCorpusChunkRow,
    DocumentCorpusIndexRow,
    DocumentRow,
    DocumentBookmarkRow,
    OutboxEventRow,
    ReadingSessionRow,
    StudySessionDocumentRow,
    StudySessionViewedPageRow,
    StudySessionRow,
)


class SqlAlchemyUnitOfWork:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, user_id: UUID
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self.session: AsyncSession | None = None

    @property
    def user_id(self) -> UUID:
        return self._user_id

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    def _active_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("unit of work must be used as an async context manager")
        return self.session

    async def get_document(self, document_id: UUID) -> Document | None:
        row = await self._active_session().scalar(
            select(DocumentRow).where(
                DocumentRow.id == document_id, DocumentRow.user_id == self._user_id
            )
        )
        return _to_document(row) if row else None

    async def get_document_by_sha256(self, sha256: str) -> Document | None:
        row = await self._active_session().scalar(
            select(DocumentRow).where(
                DocumentRow.sha256 == sha256, DocumentRow.user_id == self._user_id
            )
        )
        return _to_document(row) if row else None

    async def list_documents(self) -> list[Document]:
        rows = (
            await self._active_session().scalars(
                select(DocumentRow)
                .where(DocumentRow.user_id == self._user_id)
                .order_by(DocumentRow.created_at.desc(), DocumentRow.id)
            )
        ).all()
        return [_to_document(row) for row in rows]

    async def add_document(self, document: Document) -> None:
        self._active_session().add(
            DocumentRow(
                id=document.id,
                user_id=self._user_id,
                sha256=document.sha256,
                title=document.title,
                filename=document.filename,
                mime_type=document.mime_type,
                page_count=document.page_count,
                storage_uri=document.storage_uri,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )

    async def list_document_bookmarks(self) -> list[DocumentBookmark]:
        rows = (await self._active_session().scalars(
            select(DocumentBookmarkRow)
            .join(DocumentRow, DocumentRow.id == DocumentBookmarkRow.document_id)
            .where(
                DocumentBookmarkRow.user_id == self._user_id,
                DocumentRow.user_id == self._user_id,
                DocumentBookmarkRow.page_number <= DocumentRow.page_count,
            )
            .order_by(DocumentBookmarkRow.document_id)
        )).all()
        return [_to_document_bookmark(row) for row in rows]

    async def save_document_bookmark(self, bookmark: DocumentBookmark) -> DocumentBookmark:
        row = (await self._active_session().scalars(
            insert(DocumentBookmarkRow).values(
                user_id=self._user_id,
                document_id=bookmark.document_id,
                page_number=bookmark.page_number,
                created_at=bookmark.created_at,
                updated_at=bookmark.updated_at,
            ).on_conflict_do_update(
                index_elements=[DocumentBookmarkRow.user_id, DocumentBookmarkRow.document_id],
                set_={
                    "page_number": bookmark.page_number,
                    "updated_at": bookmark.updated_at,
                },
            ).returning(DocumentBookmarkRow)
        )).one()
        return _to_document_bookmark(row)

    async def delete_document_bookmark(self, document_id: UUID) -> None:
        await self._active_session().execute(
            delete(DocumentBookmarkRow).where(
                DocumentBookmarkRow.user_id == self._user_id,
                DocumentBookmarkRow.document_id == document_id,
            )
        )

    async def add_annotation(self, annotation: Annotation) -> None:
        self._active_session().add(
            AnnotationRow(
                id=annotation.id,
                user_id=self._user_id,
                document_id=annotation.document_id,
                page_number=annotation.page_number,
                type=annotation.type.value,
                quote=annotation.quote,
                comment=annotation.comment,
                position_json=annotation.position,
                passage_id=annotation.passage_id,
                passage_id_version=annotation.passage_id_version,
                source=annotation.source.value,
                author_type=annotation.author_type.value,
                reading_session_id=annotation.reading_session_id,
                study_session_id=annotation.study_session_id,
                created_at=annotation.created_at,
                updated_at=annotation.updated_at,
            )
        )

    async def get_annotation(self, annotation_id: UUID) -> Annotation | None:
        row = await self._active_session().scalar(
            select(AnnotationRow).where(
                AnnotationRow.id == annotation_id, AnnotationRow.user_id == self._user_id
            )
        )
        return _to_annotation(row) if row and row.deleted_at is None else None

    async def add_curation_request(self, request: CurationRequest) -> None:
        self._active_session().add(CurationRequestRow(
            id=request.id, user_id=self._user_id, annotation_id=request.annotation_id,
            study_session_id=request.study_session_id, version=request.version,
            status=request.status.value, attempt_count=request.attempt_count,
            last_error=request.last_error, created_at=request.created_at,
            updated_at=request.updated_at, completed_at=request.completed_at,
        ))

    async def get_curation_request(self, annotation_id: UUID) -> CurationRequest | None:
        row = await self._active_session().scalar(
            select(CurationRequestRow).where(
                CurationRequestRow.annotation_id == annotation_id,
                CurationRequestRow.user_id == self._user_id,
            )
        )
        return _to_curation_request(row) if row else None

    async def update_curation_request(self, request: CurationRequest) -> None:
        row = await self._active_session().scalar(
            select(CurationRequestRow).where(
                CurationRequestRow.id == request.id,
                CurationRequestRow.user_id == self._user_id,
            )
        )
        if row is None:
            raise LookupError("curation request does not exist")
        row.version = request.version
        row.status = request.status.value
        row.attempt_count = request.attempt_count
        row.last_error = request.last_error
        row.updated_at = request.updated_at
        row.completed_at = request.completed_at

    async def list_curated_sources(self, request_id: UUID) -> list[CuratedSource]:
        rows = (await self._active_session().scalars(
            select(CuratedSourceRow).where(
                CuratedSourceRow.request_id == request_id,
                CuratedSourceRow.user_id == self._user_id,
            )
            .order_by(CuratedSourceRow.rank)
        )).all()
        return [_to_curated_source(row) for row in rows]

    async def source_is_current(self, source: CuratedSource, session_id: UUID) -> bool:
        if await self.get_study_session_document(session_id, source.document_id) is None:
            return False
        chunk = await self._active_session().get(
            DocumentCorpusChunkRow,
            (source.document_id, source.page_number, source.chunk_number),
        )
        index = await self._active_session().get(DocumentCorpusIndexRow, source.document_id)
        document = await self.get_document(source.document_id)
        return bool(
            chunk and index and document and index.status == "ready"
            and index.extractor_version == EXTRACTOR_VERSION
            and index.source_sha256 == document.sha256
            and source.page_number <= document.page_count
            and chunk.content_sha256 == source.chunk_sha256
            and chunk.text_content == source.excerpt
            and chunk.start_offset == source.start_offset
            and chunk.end_offset == source.end_offset
        )

    async def add_outbox_event(self, event: OutboxEvent) -> None:
        self._active_session().add(
            OutboxEventRow(
                id=event.id,
                user_id=self._user_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=event.payload,
                event_key=event.event_key,
                created_at=event.created_at,
                available_at=event.available_at,
                attempt_count=0,
            )
        )

    async def list_annotations(self, document_id: UUID) -> list[Annotation]:
        rows = (
            await self._active_session().scalars(
                select(AnnotationRow)
                .where(
                    AnnotationRow.document_id == document_id,
                    AnnotationRow.user_id == self._user_id,
                    AnnotationRow.deleted_at.is_(None),
                )
                .order_by(AnnotationRow.page_number, AnnotationRow.created_at)
            )
        ).all()
        return [_to_annotation(row) for row in rows]

    async def add_reading_session(self, session: ReadingSession) -> None:
        self._active_session().add(_reading_session_row(session, user_id=self._user_id))

    async def update_reading_session(self, session: ReadingSession) -> None:
        row = await self._owned(ReadingSessionRow, session.id, "reading session")
        row.ended_at = session.ended_at
        row.start_page = session.start_page
        row.end_page = session.end_page
        row.last_activity_at = session.last_activity_at
        row.updated_at = session.updated_at

    async def get_reading_session(self, session_id: UUID) -> ReadingSession | None:
        row = await self._active_session().scalar(
            select(ReadingSessionRow).where(
                ReadingSessionRow.id == session_id,
                ReadingSessionRow.user_id == self._user_id,
            )
        )
        return _to_reading_session(row) if row else None

    async def add_study_session(self, session: StudySession) -> None:
        self._active_session().add(_study_session_row(session, user_id=self._user_id))

    async def update_study_session(self, session: StudySession) -> None:
        row = await self._owned(StudySessionRow, session.id, "study session")
        row.theme = session.theme
        row.theme_origin = session.theme_origin.value if session.theme_origin else None
        row.theme_updated_at = session.theme_updated_at
        row.updated_at = session.updated_at

    async def get_study_session(self, session_id: UUID) -> StudySession | None:
        row = await self._active_session().scalar(
            select(StudySessionRow).where(
                StudySessionRow.id == session_id, StudySessionRow.user_id == self._user_id
            )
        )
        return _to_study_session(row) if row else None

    async def _owned(self, row_type: type, row_id: UUID, label: str):
        """Busca a linha exigindo o dono do escopo; registro alheio não é encontrado."""
        row = await self._active_session().scalar(
            select(row_type).where(
                row_type.id == row_id, row_type.user_id == self._user_id
            )
        )
        if row is None:
            raise LookupError(f"{label} {row_id} does not exist")
        return row

    async def list_study_sessions(self) -> list[StudySession]:
        rows = (
            await self._active_session().scalars(
                select(StudySessionRow)
                .where(StudySessionRow.user_id == self._user_id)
                .order_by(
                    StudySessionRow.updated_at.desc(),
                    StudySessionRow.created_at.desc(),
                    StudySessionRow.id,
                )
            )
        ).all()
        return [_to_study_session(row) for row in rows]

    async def add_study_session_document(self, link: StudySessionDocument) -> None:
        self._active_session().add(
            StudySessionDocumentRow(
                id=link.id,
                user_id=self._user_id,
                study_session_id=link.study_session_id,
                document_id=link.document_id,
                position=link.position,
                added_at=link.added_at,
            )
        )

    async def get_study_session_document(
        self, session_id: UUID, document_id: UUID
    ) -> StudySessionDocument | None:
        row = await self._active_session().scalar(
            select(StudySessionDocumentRow).where(
                StudySessionDocumentRow.study_session_id == session_id,
                StudySessionDocumentRow.document_id == document_id,
                StudySessionDocumentRow.user_id == self._user_id,
            )
        )
        return _to_study_session_document(row) if row else None

    async def list_study_session_documents(
        self, session_id: UUID
    ) -> list[StudySessionDocument]:
        rows = (
            await self._active_session().scalars(
                select(StudySessionDocumentRow)
                .where(
                    StudySessionDocumentRow.study_session_id == session_id,
                    StudySessionDocumentRow.user_id == self._user_id,
                )
                .order_by(
                    StudySessionDocumentRow.position,
                    StudySessionDocumentRow.added_at,
                    StudySessionDocumentRow.id,
                )
            )
        ).all()
        return [_to_study_session_document(row) for row in rows]

    async def remove_study_session_document(self, session_id: UUID, document_id: UUID) -> None:
        await self._active_session().execute(
            delete(StudySessionDocumentRow).where(
                StudySessionDocumentRow.study_session_id == session_id,
                StudySessionDocumentRow.document_id == document_id,
                StudySessionDocumentRow.user_id == self._user_id,
            )
        )

    async def next_study_session_position(self, session_id: UUID) -> int:
        highest = await self._active_session().scalar(
            select(func.max(StudySessionDocumentRow.position)).where(
                StudySessionDocumentRow.study_session_id == session_id,
                StudySessionDocumentRow.user_id == self._user_id,
            )
        )
        return 0 if highest is None else int(highest) + 1

    async def add_viewed_page(
        self, session_id: UUID, document_id: UUID, page_number: int, first_viewed_at: datetime
    ) -> None:
        await self._active_session().execute(
            insert(StudySessionViewedPageRow).values(
                user_id=self._user_id,
                study_session_id=session_id,
                document_id=document_id,
                page_number=page_number,
                first_viewed_at=first_viewed_at,
            ).on_conflict_do_nothing()
        )

    async def list_session_document_page_counts(
        self, session_id: UUID | None = None
    ) -> list[tuple[UUID, UUID, int, int]]:
        visit = StudySessionViewedPageRow
        statement = (
            select(
                StudySessionDocumentRow.study_session_id,
                DocumentRow.id,
                DocumentRow.page_count,
                func.count(visit.page_number),
            )
            .join(DocumentRow, DocumentRow.id == StudySessionDocumentRow.document_id)
            .outerjoin(visit, and_(
                visit.study_session_id == StudySessionDocumentRow.study_session_id,
                visit.document_id == StudySessionDocumentRow.document_id,
            ))
            .where(
                StudySessionDocumentRow.user_id == self._user_id,
                DocumentRow.user_id == self._user_id,
            )
            .group_by(
                StudySessionDocumentRow.study_session_id,
                DocumentRow.id,
                DocumentRow.page_count,
            )
        )
        if session_id is not None:
            statement = statement.where(StudySessionDocumentRow.study_session_id == session_id)
        rows = (await self._active_session().execute(statement)).all()
        return [(sid, did, int(total), int(viewed)) for sid, did, total, viewed in rows]

    async def list_global_document_page_counts(
        self, document_id: UUID | None = None
    ) -> list[tuple[UUID, int, int]]:
        visit = StudySessionViewedPageRow
        statement = (
            select(
                DocumentRow.id,
                DocumentRow.page_count,
                func.count(func.distinct(visit.page_number)),
            )
            .outerjoin(visit, visit.document_id == DocumentRow.id)
            .where(DocumentRow.user_id == self._user_id)
            .group_by(DocumentRow.id, DocumentRow.page_count)
        )
        if document_id is not None:
            statement = statement.where(DocumentRow.id == document_id)
        rows = (await self._active_session().execute(statement)).all()
        return [(did, int(total), int(viewed)) for did, total, viewed in rows]

    async def add_ai_suggestion(self, suggestion: AISuggestion) -> None:
        self._active_session().add(
            AISuggestionRow(
                id=suggestion.id,
                user_id=self._user_id,
                suggestion_type=suggestion.suggestion_type,
                subject_type=suggestion.subject_type,
                subject_id=suggestion.subject_id,
                payload=suggestion.payload,
                provider=suggestion.provider,
                model=suggestion.model,
                status=suggestion.status.value,
                created_at=suggestion.created_at,
                accepted_at=suggestion.accepted_at,
                rejected_at=suggestion.rejected_at,
            )
        )

    async def update_ai_suggestion(self, suggestion: AISuggestion) -> None:
        row = await self._owned(AISuggestionRow, suggestion.id, "suggestion")
        row.status = suggestion.status.value
        row.accepted_at = suggestion.accepted_at
        row.rejected_at = suggestion.rejected_at

    async def get_ai_suggestion(self, suggestion_id: UUID) -> AISuggestion | None:
        row = await self._active_session().scalar(
            select(AISuggestionRow).where(
                AISuggestionRow.id == suggestion_id,
                AISuggestionRow.user_id == self._user_id,
            )
        )
        return _to_ai_suggestion(row) if row else None

    async def list_ai_suggestions(
        self, subject_type: str, subject_id: UUID
    ) -> list[AISuggestion]:
        rows = (
            await self._active_session().scalars(
                select(AISuggestionRow)
                .where(
                    AISuggestionRow.subject_type == subject_type,
                    AISuggestionRow.subject_id == subject_id,
                    AISuggestionRow.user_id == self._user_id,
                )
                .order_by(AISuggestionRow.created_at.desc(), AISuggestionRow.id)
            )
        ).all()
        return [_to_ai_suggestion(row) for row in rows]

    async def get_ai_model_config(self) -> AIModelConfig | None:
        row = await self._active_session().scalar(
            select(AIModelConfigRow).where(AIModelConfigRow.user_id == self._user_id)
        )
        return _to_ai_model_config(row) if row else None

    async def get_claim(self, claim_id: UUID) -> Claim | None:
        row = await self._active_session().scalar(
            select(ClaimRow).where(
                ClaimRow.id == claim_id,
                ClaimRow.user_id == self._user_id,
                ClaimRow.deleted_at.is_(None),
            )
        )
        return _to_claim(row) if row else None

    async def upsert_claim(self, claim: Claim) -> tuple[Claim, bool]:
        """Reaproveita a afirmação de mesmo texto normalizado e diz se ela nasceu agora.

        A deduplicação é o índice único **parcial** `(user_id, normalized_text) WHERE
        deleted_at IS NULL`: duas afirmações com o mesmo texto convivem quando a primeira
        foi removida. O `WHERE` do alvo precisa ser declarado aqui, senão o PostgreSQL não
        reconhece o índice e o `ON CONFLICT` falha.

        O conflito não altera a linha existente. Reafirmar acrescenta evidência, e reescrever
        texto, proveniência, provedor ou modelo destruiria a origem de uma sugestão de modelo
        que o usuário aceitou. Como nada muda no PostgreSQL nesse ramo, nada precisa ser
        reprojetado, e o `False` diz exatamente isso a quem chama.
        """
        session = self._active_session()
        inserted = (
            await session.scalars(
                insert(ClaimRow)
                .values(
                    id=claim.id,
                    user_id=self._user_id,
                    text=claim.text,
                    normalized_text=claim.normalized_text,
                    provenance=claim.provenance.value,
                    status=claim.status.value,
                    provider=claim.provider,
                    model=claim.model,
                    confidence=claim.confidence,
                    created_at=claim.created_at,
                    updated_at=claim.updated_at,
                    accepted_at=claim.accepted_at,
                    deleted_at=claim.deleted_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[ClaimRow.user_id, ClaimRow.normalized_text],
                    index_where=text("deleted_at IS NULL"),
                )
                .returning(ClaimRow)
            )
        ).one_or_none()
        if inserted is not None:
            return _to_claim(inserted), True
        existing = (
            await session.scalars(
                select(ClaimRow).where(
                    ClaimRow.user_id == self._user_id,
                    ClaimRow.normalized_text == claim.normalized_text,
                    ClaimRow.deleted_at.is_(None),
                )
            )
        ).one()
        return _to_claim(existing), False

    async def list_claims(self) -> list[Claim]:
        rows = (
            await self._active_session().scalars(
                select(ClaimRow)
                .where(ClaimRow.user_id == self._user_id, ClaimRow.deleted_at.is_(None))
                .order_by(ClaimRow.created_at, ClaimRow.id)
            )
        ).all()
        return [_to_claim(row) for row in rows]

    async def mark_claim_deleted(self, claim_id: UUID, deleted_at: datetime) -> None:
        """Exclusão suave: a afirmação sai do que o usuário vê, mas não do banco.

        O `WHERE deleted_at IS NULL` mantém a operação idempotente sem reescrever a data
        original da primeira remoção.
        """
        await self._active_session().execute(
            update(ClaimRow)
            .where(
                ClaimRow.id == claim_id,
                ClaimRow.user_id == self._user_id,
                ClaimRow.deleted_at.is_(None),
            )
            .values(deleted_at=deleted_at, updated_at=deleted_at)
        )

    async def add_claim_relation(self, relation: ClaimRelation) -> ClaimRelation:
        """Cria a aresta ou devolve a que já existe para o mesmo par ordenado.

        Repetir a mesma aresta é idempotente por `UNIQUE NULLS NOT DISTINCT` sobre o par
        ordenado — necessário porque as colunas que a relação não usa ficam nulas. O
        conflito é resolvido reescrevendo a própria chave primária: é um no-op de conteúdo
        que garante `RETURNING` (devolve a aresta já existente, com o id dela) e mantém a
        operação idempotente sob retry. O alvo é a restrição por nome, que é como o
        `NULLS NOT DISTINCT` foi declarado.
        """
        statement = (
            insert(ClaimRelationRow)
            .values(
                id=relation.id,
                user_id=self._user_id,
                relation=relation.relation.value,
                annotation_id=relation.annotation_id,
                claim_id=relation.claim_id,
                question_annotation_id=relation.question_annotation_id,
                answer_annotation_id=relation.answer_annotation_id,
                source_concept_id=relation.source_concept_id,
                target_concept_id=relation.target_concept_id,
                provenance=relation.provenance.value,
                status=relation.status.value,
                provider=relation.provider,
                model=relation.model,
                confidence=relation.confidence,
                created_at=relation.created_at,
                accepted_at=relation.accepted_at,
            )
            .on_conflict_do_update(
                constraint="claim_relations_pair_key",
                set_={"id": ClaimRelationRow.id},
            )
            .returning(ClaimRelationRow)
        )
        row = (await self._active_session().scalars(statement)).one()
        return _to_claim_relation(row)

    async def get_concept(self, concept_id: UUID) -> Concept | None:
        """Escopado por dono: conceito de outra conta simplesmente não é encontrado."""
        row = await self._active_session().scalar(
            select(ConceptRow).where(
                ConceptRow.id == concept_id, ConceptRow.user_id == self._user_id
            )
        )
        return _to_concept(row) if row else None

    async def list_concepts(self) -> list[Concept]:
        """Conceitos do dono, em ordem estável de nome."""
        rows = (
            await self._active_session().scalars(
                select(ConceptRow)
                .where(ConceptRow.user_id == self._user_id)
                .order_by(ConceptRow.name, ConceptRow.id)
            )
        ).all()
        return [_to_concept(row) for row in rows]

    async def list_concept_references(
        self, concept_ids: list[UUID]
    ) -> list[tuple[UUID, UUID, UUID, str]]:
        """Citações dos conceitos: `(conceito, anotação, documento, título)`.

        O escopo é do dono nas duas pontas — anotação e documento —, e anotação removida não
        conta: a lista que a tela mostra é a do acervo vivo. O `distinct` evita contar duas
        vezes a mesma anotação ligada ao mesmo conceito por relações diferentes.
        """
        if not concept_ids:
            return []
        rows = await self._active_session().execute(
            select(
                AnnotationConceptRow.concept_id,
                AnnotationRow.id,
                AnnotationRow.document_id,
                DocumentRow.title,
            )
            .join(AnnotationRow, AnnotationRow.id == AnnotationConceptRow.annotation_id)
            .join(DocumentRow, DocumentRow.id == AnnotationRow.document_id)
            .where(
                AnnotationConceptRow.concept_id.in_(concept_ids),
                AnnotationRow.user_id == self._user_id,
                AnnotationRow.deleted_at.is_(None),
                DocumentRow.user_id == self._user_id,
            )
            .distinct()
        )
        return [(row[0], row[1], row[2], row[3]) for row in rows]

    async def list_concept_associations(self, concept_ids: list[UUID]) -> list[ClaimRelation]:
        """Arestas `RELATED_TO` do dono que tocam os conceitos, em qualquer das duas pontas."""
        if not concept_ids:
            return []
        rows = (
            await self._active_session().scalars(
                select(ClaimRelationRow)
                .where(
                    ClaimRelationRow.user_id == self._user_id,
                    ClaimRelationRow.relation == ClaimRelationKind.RELATED_TO.value,
                    or_(
                        ClaimRelationRow.source_concept_id.in_(concept_ids),
                        ClaimRelationRow.target_concept_id.in_(concept_ids),
                    ),
                )
                .order_by(ClaimRelationRow.created_at, ClaimRelationRow.id)
            )
        ).all()
        return [_to_claim_relation(row) for row in rows]

    async def get_claim_relation(self, relation_id: UUID) -> ClaimRelation | None:
        row = await self._active_session().scalar(
            select(ClaimRelationRow).where(
                ClaimRelationRow.id == relation_id,
                ClaimRelationRow.user_id == self._user_id,
            )
        )
        return _to_claim_relation(row) if row else None

    async def list_claim_relations(
        self,
        *,
        annotation_id: UUID | None = None,
        claim_id: UUID | None = None,
    ) -> list[ClaimRelation]:
        statement = select(ClaimRelationRow).where(
            ClaimRelationRow.user_id == self._user_id
        )
        if annotation_id is not None:
            statement = statement.where(
                or_(
                    ClaimRelationRow.annotation_id == annotation_id,
                    ClaimRelationRow.question_annotation_id == annotation_id,
                    ClaimRelationRow.answer_annotation_id == annotation_id,
                )
            )
        if claim_id is not None:
            statement = statement.where(ClaimRelationRow.claim_id == claim_id)
        rows = (
            await self._active_session().scalars(
                statement.order_by(ClaimRelationRow.created_at, ClaimRelationRow.id)
            )
        ).all()
        return [_to_claim_relation(row) for row in rows]

    async def remove_claim_relation(self, relation_id: UUID) -> None:
        await self._active_session().execute(
            delete(ClaimRelationRow).where(
                ClaimRelationRow.id == relation_id,
                ClaimRelationRow.user_id == self._user_id,
            )
        )

    async def search_session_passages(
        self,
        *,
        session_id: UUID,
        question: str,
        document_id: UUID | None = None,
        limit: int = 5,
    ) -> list[CorpusPassage]:
        """Trechos do corpus da sessão que ancoram uma resposta do assistente."""
        return await search_session_passages(
            self._active_session(),
            study_session_id=session_id,
            user_id=self._user_id,
            question=question,
            document_id=document_id,
            limit=limit,
        )

    async def list_session_annotations(
        self, *, session_id: UUID, limit: int = 8
    ) -> list[Annotation]:
        rows = await list_session_annotation_rows(
            self._active_session(),
            study_session_id=session_id,
            user_id=self._user_id,
            limit=limit,
        )
        return [_to_annotation(row) for row in rows]

    async def save_ai_model_config(self, config: AIModelConfig) -> None:
        """Uma configuração por usuário: cria na primeira vez, atualiza depois."""
        row = await self._active_session().scalar(
            select(AIModelConfigRow).where(AIModelConfigRow.user_id == self._user_id)
        )
        if row is None:
            self._active_session().add(
                _ai_model_config_row(config, user_id=self._user_id)
            )
            return
        row.provider = config.provider.value
        row.model = config.model
        row.api_key_encrypted = config.api_key_encrypted
        row.base_url = config.base_url
        row.enabled = config.enabled
        row.updated_at = config.updated_at

    async def delete_ai_model_config(self) -> None:
        await self._active_session().execute(
            delete(AIModelConfigRow).where(AIModelConfigRow.user_id == self._user_id)
        )

    async def commit(self) -> None:
        await self._active_session().commit()


def _reading_session_row(session: ReadingSession, *, user_id: UUID) -> ReadingSessionRow:
    return ReadingSessionRow(
        id=session.id,
        user_id=user_id,
        document_id=session.document_id,
        filename_snapshot=session.filename_snapshot,
        started_at=session.started_at,
        ended_at=session.ended_at,
        start_page=session.start_page,
        end_page=session.end_page,
        last_activity_at=session.last_activity_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _to_reading_session(row: ReadingSessionRow) -> ReadingSession:
    return ReadingSession(
        id=row.id,
        document_id=row.document_id,
        filename_snapshot=row.filename_snapshot,
        started_at=row.started_at,
        ended_at=row.ended_at,
        start_page=row.start_page,
        end_page=row.end_page,
        last_activity_at=row.last_activity_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _study_session_row(session: StudySession, *, user_id: UUID) -> StudySessionRow:
    return StudySessionRow(
        id=session.id,
        user_id=user_id,
        theme=session.theme,
        theme_origin=session.theme_origin.value if session.theme_origin else None,
        theme_updated_at=session.theme_updated_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _to_concept(row: ConceptRow) -> Concept:
    return Concept(
        id=row.id,
        name=row.name,
        normalized_name=row.normalized_name,
        created_at=row.created_at,
    )


def _to_claim(row: ClaimRow) -> Claim:
    return Claim(
        id=row.id,
        text=row.text,
        normalized_text=row.normalized_text,
        provenance=RelationProvenance(row.provenance),
        status=RelationStatus(row.status),
        provider=row.provider,
        model=row.model,
        confidence=row.confidence,
        created_at=row.created_at,
        updated_at=row.updated_at,
        accepted_at=row.accepted_at,
        deleted_at=row.deleted_at,
    )


def _to_claim_relation(row: ClaimRelationRow) -> ClaimRelation:
    return ClaimRelation(
        id=row.id,
        relation=ClaimRelationKind(row.relation),
        provenance=RelationProvenance(row.provenance),
        status=RelationStatus(row.status),
        annotation_id=row.annotation_id,
        claim_id=row.claim_id,
        question_annotation_id=row.question_annotation_id,
        answer_annotation_id=row.answer_annotation_id,
        source_concept_id=row.source_concept_id,
        target_concept_id=row.target_concept_id,
        provider=row.provider,
        model=row.model,
        confidence=row.confidence,
        created_at=row.created_at,
        accepted_at=row.accepted_at,
    )


def _to_document_bookmark(row: DocumentBookmarkRow) -> DocumentBookmark:
    return DocumentBookmark(
        document_id=row.document_id,
        page_number=row.page_number,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_document(row: DocumentRow) -> Document:
    return Document(
        id=row.id,
        sha256=row.sha256,
        title=row.title,
        filename=row.filename,
        mime_type=row.mime_type,
        page_count=row.page_count,
        storage_uri=row.storage_uri,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_annotation(row: AnnotationRow) -> Annotation:
    return Annotation(
        id=row.id,
        document_id=row.document_id,
        page_number=row.page_number,
        type=AnnotationType(row.type),
        quote=row.quote,
        comment=row.comment,
        position=row.position_json,
        passage_id=row.passage_id,
        passage_id_version=row.passage_id_version,
        source=AnnotationSource(row.source),
        author_type=AuthorType(row.author_type),
        reading_session_id=row.reading_session_id,
        study_session_id=row.study_session_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_study_session(row: StudySessionRow) -> StudySession:
    return StudySession(
        id=row.id,
        theme=row.theme,
        theme_origin=ThemeOrigin(row.theme_origin) if row.theme_origin else None,
        theme_updated_at=row.theme_updated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_study_session_document(row: StudySessionDocumentRow) -> StudySessionDocument:
    return StudySessionDocument(
        id=row.id,
        study_session_id=row.study_session_id,
        document_id=row.document_id,
        position=row.position,
        added_at=row.added_at,
    )


def _ai_model_config_row(config: AIModelConfig, *, user_id: UUID) -> AIModelConfigRow:
    return AIModelConfigRow(
        id=config.id,
        user_id=user_id,
        provider=config.provider.value,
        model=config.model,
        api_key_encrypted=config.api_key_encrypted,
        base_url=config.base_url,
        enabled=config.enabled,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def _to_ai_model_config(row: AIModelConfigRow) -> AIModelConfig:
    return AIModelConfig(
        id=row.id,
        provider=AIModelProvider(row.provider),
        model=row.model,
        api_key_encrypted=row.api_key_encrypted,
        base_url=row.base_url,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_ai_suggestion(row: AISuggestionRow) -> AISuggestion:
    return AISuggestion(
        id=row.id,
        suggestion_type=row.suggestion_type,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        payload=row.payload,
        provider=row.provider,
        model=row.model,
        status=SuggestionStatus(row.status),
        created_at=row.created_at,
        accepted_at=row.accepted_at,
        rejected_at=row.rejected_at,
    )


def _to_curation_request(row: CurationRequestRow) -> CurationRequest:
    return CurationRequest(
        id=row.id, annotation_id=row.annotation_id,
        study_session_id=row.study_session_id, version=row.version,
        status=CurationStatus(row.status), attempt_count=row.attempt_count,
        last_error=row.last_error, created_at=row.created_at,
        updated_at=row.updated_at, completed_at=row.completed_at,
    )


def _to_curated_source(row: CuratedSourceRow) -> CuratedSource:
    return CuratedSource(
        id=row.id, request_id=row.request_id, document_id=row.document_id,
        page_number=row.page_number, chunk_number=row.chunk_number,
        chunk_sha256=row.chunk_sha256, start_offset=row.start_offset,
        end_offset=row.end_offset, excerpt=row.excerpt, reason=row.reason,
        rank=row.rank, provider=row.provider, model=row.model,
        retrieval_version=row.retrieval_version, created_at=row.created_at,
    )
