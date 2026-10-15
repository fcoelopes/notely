from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from notely.core.models import (
    AISuggestion,
    AISuggestionType,
    Annotation,
    AnnotationSource,
    AnnotationType,
    Claim,
    ClaimRelation,
    ClaimRelationKind,
    CurationRequest,
    CurationStatus,
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
    utc_now,
)
from notely.core.claims import ClaimEvidence, normalize_claim_text
from notely.core.curation import CurationView, CuratedSourceView
from notely.core.passage import anchor_from_position, passage_id
from notely.core.progress import (
    ReadingProgressSnapshot,
    ReadingProgressUpdate,
    build_progress_snapshot,
)
from notely.core.ports import UnitOfWork
from notely.providers.topic import TopicSuggestionContext, TopicSuggestionProvider

STUDY_SESSION_SUBJECT = "study_session"


class DocumentNotFoundError(Exception):
    pass


class DocumentConflictError(Exception):
    pass


class PageOutsideDocumentError(Exception):
    pass


class ReadingSessionNotFoundError(Exception):
    pass


class ReadingSessionDocumentMismatchError(Exception):
    pass


class StudySessionNotFoundError(Exception):
    pass


class SessionDocumentConflictError(Exception):
    pass


class SessionDocumentNotFoundError(Exception):
    pass


class CurationNotFoundError(Exception):
    pass


class CurationRetryConflictError(Exception):
    pass


class SuggestionNotFoundError(Exception):
    pass


class SuggestionAlreadyResolvedError(Exception):
    pass


class SessionWithoutDocumentsError(Exception):
    pass


class ClaimNotFoundError(Exception):
    pass


class ClaimRelationNotFoundError(Exception):
    pass


class RelatedConceptNotFoundError(Exception):
    pass


class AnsweredQuestionNotFoundError(Exception):
    pass


class NotelyService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def find_document_by_sha256(self, sha256: str) -> Document | None:
        async with self._uow_factory() as uow:
            return await uow.get_document_by_sha256(sha256.lower())

    async def list_documents(self) -> list[Document]:
        async with self._uow_factory() as uow:
            return await uow.list_documents()

    async def get_document(self, document_id: UUID) -> Document:
        async with self._uow_factory() as uow:
            document = await uow.get_document(document_id)
            if document is None:
                raise DocumentNotFoundError(str(document_id))
            return document

    async def list_document_bookmarks(self) -> list[DocumentBookmark]:
        async with self._uow_factory() as uow:
            return await uow.list_document_bookmarks()

    async def set_document_bookmark(
        self, *, document_id: UUID, page_number: int
    ) -> DocumentBookmark:
        async with self._uow_factory() as uow:
            document = await uow.get_document(document_id)
            if document is None:
                raise DocumentNotFoundError(str(document_id))
            if not 1 <= page_number <= document.page_count:
                raise PageOutsideDocumentError(
                    f"page {page_number} is outside document page count {document.page_count}"
                )
            now = utc_now()
            saved = await uow.save_document_bookmark(DocumentBookmark(
                document_id=document_id, page_number=page_number,
                created_at=now, updated_at=now,
            ))
            await uow.commit()
            return saved

    async def delete_document_bookmark(self, document_id: UUID) -> None:
        async with self._uow_factory() as uow:
            if await uow.get_document(document_id) is None:
                raise DocumentNotFoundError(str(document_id))
            await uow.delete_document_bookmark(document_id)
            await uow.commit()

    async def register_document(
        self,
        *,
        sha256: str,
        title: str,
        filename: str,
        page_count: int,
        storage_uri: str,
    ) -> Document:
        document = Document(
            sha256=sha256.lower(),
            title=title,
            filename=filename,
            page_count=page_count,
            storage_uri=storage_uri,
        )
        async with self._uow_factory() as uow:
            if await uow.get_document_by_sha256(document.sha256):
                raise DocumentConflictError("a document with this sha256 already exists")
            await uow.add_document(document)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="document",
                    aggregate_id=document.id,
                    event_type="document.created",
                    payload={"document_id": str(document.id)},
                )
            )
            await uow.commit()
        return document

    async def create_annotation(
        self,
        *,
        document_id: UUID,
        page_number: int,
        annotation_type: AnnotationType,
        quote: str,
        position: dict[str, Any],
        comment: str | None = None,
        source: AnnotationSource = AnnotationSource.USER_SELECTION,
        reading_session_id: UUID | None = None,
        study_session_id: UUID | None = None,
    ) -> Annotation:
        async with self._uow_factory() as uow:
            document = await uow.get_document(document_id)
            if document is None:
                raise DocumentNotFoundError(str(document_id))
            if page_number > document.page_count:
                raise PageOutsideDocumentError(
                    f"page {page_number} exceeds document page count {document.page_count}"
                )

            if reading_session_id is not None:
                session = await uow.get_reading_session(reading_session_id)
                if session is None:
                    raise ReadingSessionNotFoundError(str(reading_session_id))
                if session.document_id != document_id:
                    raise ReadingSessionDocumentMismatchError(
                        "a reading session belongs to a single document"
                    )

            if study_session_id is not None:
                if annotation_type is AnnotationType.QUESTION and not (comment or "").strip():
                    raise ValueError("question text is required for source curation")
                if await uow.get_study_session(study_session_id) is None:
                    raise StudySessionNotFoundError(str(study_session_id))
                if await uow.get_study_session_document(study_session_id, document_id) is None:
                    raise SessionDocumentNotFoundError("document is not in this study session")

            exact, prefix, suffix = anchor_from_position(position)
            annotation = Annotation(
                document_id=document_id,
                page_number=page_number,
                type=annotation_type,
                quote=quote,
                comment=comment,
                position=position,
                source=source,
                reading_session_id=reading_session_id,
                study_session_id=study_session_id,
                passage_id=passage_id(
                    document_sha256=document.sha256,
                    page_number=page_number,
                    quote=exact or quote,
                    prefix=prefix,
                    suffix=suffix,
                ),
            )
            await uow.add_annotation(annotation)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="annotation",
                    aggregate_id=annotation.id,
                    event_type="annotation.created",
                    payload={
                        "annotation_id": str(annotation.id),
                        "document_id": str(document_id),
                        "reading_session_id": (
                            str(reading_session_id) if reading_session_id else None
                        ),
                        "page_number": page_number,
                        "annotation_type": annotation.type.value,
                        "passage_id": annotation.passage_id,
                        "passage_id_version": annotation.passage_id_version,
                        "source": annotation.source.value,
                        "author_type": annotation.author_type.value,
                        "occurred_at": annotation.created_at.isoformat(),
                    },
                )
            )
            if annotation_type is AnnotationType.QUESTION and study_session_id is not None:
                request = CurationRequest(
                    annotation_id=annotation.id, study_session_id=study_session_id
                )
                await uow.add_curation_request(request)
                await uow.add_outbox_event(_curation_event(request))
            await uow.commit()
        return annotation

    async def get_question_sources(
        self, *, session_id: UUID, annotation_id: UUID
    ) -> CurationView:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            annotation = await uow.get_annotation(annotation_id)
            request = await uow.get_curation_request(annotation_id)
            if (
                annotation is None or annotation.type is not AnnotationType.QUESTION
                or annotation.study_session_id != session_id or request is None
                or request.study_session_id != session_id
            ):
                raise CurationNotFoundError(str(annotation_id))
            sources: list[CuratedSourceView] = []
            if request.status is CurationStatus.READY:
                for source in await uow.list_curated_sources(request.id):
                    document = await uow.get_document(source.document_id)
                    sources.append(CuratedSourceView(
                        source=source,
                        document_title=document.title if document else "Documento indisponível",
                        available=await uow.source_is_current(source, session_id),
                    ))
            return CurationView(request=request, sources=sources)

    async def retry_question_curation(
        self, *, session_id: UUID, annotation_id: UUID
    ) -> CurationRequest:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            annotation = await uow.get_annotation(annotation_id)
            request = await uow.get_curation_request(annotation_id)
            if (
                annotation is None or annotation.type is not AnnotationType.QUESTION
                or annotation.study_session_id != session_id or request is None
                or request.study_session_id != session_id
            ):
                raise CurationNotFoundError(str(annotation_id))
            if request.status not in {CurationStatus.FAILED, CurationStatus.NO_SOURCE}:
                raise CurationRetryConflictError("curation is already pending or ready")
            updated = replace(
                request, version=request.version + 1, status=CurationStatus.PENDING,
                attempt_count=0, last_error=None, updated_at=utc_now(), completed_at=None,
            )
            await uow.update_curation_request(updated)
            await uow.add_outbox_event(_curation_event(updated))
            await uow.commit()
            return updated

    async def start_reading_session(
        self,
        *,
        document_id: UUID,
        page_number: int | None = None,
        filename: str | None = None,
    ) -> ReadingSession:
        async with self._uow_factory() as uow:
            document = await uow.get_document(document_id)
            if document is None:
                raise DocumentNotFoundError(str(document_id))
            if page_number is not None and page_number > document.page_count:
                raise PageOutsideDocumentError(
                    f"page {page_number} exceeds document page count {document.page_count}"
                )

            started_at = utc_now()
            session = ReadingSession(
                document_id=document_id,
                filename_snapshot=(filename or document.filename).strip(),
                started_at=started_at,
                last_activity_at=started_at,
                start_page=page_number,
                end_page=page_number,
                created_at=started_at,
                updated_at=started_at,
            )
            await uow.add_reading_session(session)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="reading_session",
                    aggregate_id=session.id,
                    event_type="reading.started",
                    payload={
                        "reading_session_id": str(session.id),
                        "document_id": str(document_id),
                        "page_number": page_number,
                        "filename_snapshot": session.filename_snapshot,
                        "occurred_at": session.started_at.isoformat(),
                    },
                )
            )
            await uow.commit()
        return session

    async def update_reading_session(
        self,
        *,
        session_id: UUID,
        page_number: int | None = None,
        ended: bool = False,
    ) -> ReadingSession:
        async with self._uow_factory() as uow:
            session = await uow.get_reading_session(session_id)
            if session is None:
                raise ReadingSessionNotFoundError(str(session_id))
            # Uma sessão já encerrada ignora atividade atrasada do Reader.
            if not session.is_open:
                return session

            if page_number is not None:
                document = await uow.get_document(session.document_id)
                if document is not None and page_number > document.page_count:
                    raise PageOutsideDocumentError(
                        f"page {page_number} exceeds document page count {document.page_count}"
                    )

            now = utc_now()
            ended_at = now if ended else None
            updated = ReadingSession(
                id=session.id,
                document_id=session.document_id,
                filename_snapshot=session.filename_snapshot,
                started_at=session.started_at,
                ended_at=ended_at,
                start_page=session.start_page if session.start_page is not None else page_number,
                end_page=page_number if page_number is not None else session.end_page,
                last_activity_at=now,
                created_at=session.created_at,
                updated_at=now,
            )
            await uow.update_reading_session(updated)
            if ended:
                await uow.add_outbox_event(
                    OutboxEvent(
                        aggregate_type="reading_session",
                        aggregate_id=session.id,
                        event_type="reading.ended",
                        payload={
                            "reading_session_id": str(session.id),
                            "document_id": str(session.document_id),
                            "end_page": updated.end_page,
                            "started_at": session.started_at.isoformat(),
                            "occurred_at": now.isoformat(),
                        },
                    )
                )
            await uow.commit()
        return updated

    async def list_document_annotations(self, document_id: UUID) -> list[Annotation]:
        async with self._uow_factory() as uow:
            if await uow.get_document(document_id) is None:
                raise DocumentNotFoundError(str(document_id))
            return await uow.list_annotations(document_id)

    async def create_claim(
        self, *, annotation_id: UUID, text: str, relation: ClaimRelationKind
    ) -> ClaimEvidence:
        """Afirma algo a partir de uma anotação e liga a evidência à afirmação.

        A afirmação é conhecimento autoral: nasce `user_authored` e `accepted`, porque foi o
        próprio usuário quem a escreveu. Afirmar o mesmo texto de novo reaproveita a
        afirmação existente e apenas acrescenta a nova evidência — não duplica conhecimento.

        Anotação de outra conta responde como inexistente: a leitura já é escopada ao dono.

        O evento é publicado apenas para o que nasceu agora. Afirmar de novo o mesmo texto com
        a mesma evidência é uma repetição idempotente: nada mudou no PostgreSQL, então não há
        o que projetar. Eventos de fato pontual são deduplicados por agregado + tipo, então
        publicar de novo seria rejeitado pelo outbox de qualquer forma.
        """
        if relation not in (ClaimRelationKind.SUPPORTS, ClaimRelationKind.CONTRADICTS):
            raise ValueError(
                "a claim is asserted from an annotation as supports or contradicts"
            )
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("claim text is required")
        async with self._uow_factory() as uow:
            annotation = await uow.get_annotation(annotation_id)
            if annotation is None:
                raise DocumentNotFoundError(f"annotation {annotation_id} does not exist")
            now = utc_now()
            claim = await uow.upsert_claim(Claim(
                text=clean_text,
                normalized_text=normalize_claim_text(clean_text),
                provenance=RelationProvenance.USER_AUTHORED,
                status=RelationStatus.ACCEPTED,
                created_at=now,
                updated_at=now,
                accepted_at=now,
            ))
            # `created_at` diferente do enviado significa que a linha já existia: o upsert
            # preserva a data da afirmação original.
            claim_created = claim.created_at == now
            # Afirmar é um aceite: a aresta nasce autoral e valendo.
            candidate = ClaimRelation(
                relation=relation,
                provenance=RelationProvenance.USER_AUTHORED,
                status=RelationStatus.ACCEPTED,
                annotation_id=annotation_id,
                claim_id=claim.id,
                created_at=now,
                accepted_at=now,
            )
            edge = await uow.add_claim_relation(candidate)
            # O upsert devolve a aresta já existente, com o id dela.
            edge_created = edge.id == candidate.id
            if claim_created:
                await uow.add_outbox_event(_claim_event(claim))
            if edge_created:
                # A projeção da aresta garante o nó das duas pontas, então ela alcança o
                # grafo mesmo quando o nó do Claim vem de uma afirmação anterior.
                await uow.add_outbox_event(_claim_relation_event(edge, claim_id=claim.id))
            evidence = await _evidence_for(uow, claim=claim, relation=edge)
            await uow.commit()
        return evidence

    async def list_claim_evidence(self) -> list[ClaimEvidence]:
        """Afirmações do usuário com a evidência que as sustenta, do mais recente ao antigo."""
        async with self._uow_factory() as uow:
            entries: list[ClaimEvidence] = []
            for claim in reversed(await uow.list_claims()):
                for relation in await uow.list_claim_relations(claim_id=claim.id):
                    entries.append(await _evidence_for(uow, claim=claim, relation=relation))
            return entries

    async def delete_claim(self, claim_id: UUID) -> None:
        """Exclusão suave da afirmação, com a remoção do nó publicada no outbox.

        As arestas saem em cascata no PostgreSQL; o worker de grafo derruba o nó `Claim` e,
        com ele, as arestas que o citavam.
        """
        async with self._uow_factory() as uow:
            claim = await uow.get_claim(claim_id)
            if claim is None:
                raise ClaimNotFoundError(str(claim_id))
            now = utc_now()
            await uow.mark_claim_deleted(claim_id, now)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="claim",
                    aggregate_id=claim_id,
                    event_type="claim.deleted",
                    event_key=f"claim-deleted:{claim_id}",
                    payload={"claim_id": str(claim_id)},
                )
            )
            await uow.commit()

    async def answer_question(
        self, *, question_annotation_id: UUID, answer_annotation_id: UUID
    ) -> ClaimRelation:
        """Liga a dúvida à anotação que a responde (`ANSWERED_BY`).

        A origem precisa ser realmente uma dúvida; a resposta é qualquer anotação do mesmo
        dono. Ambas as pontas são validadas no escopo do usuário.
        """
        if question_annotation_id == answer_annotation_id:
            raise ValueError("an annotation cannot answer itself")
        async with self._uow_factory() as uow:
            question = await uow.get_annotation(question_annotation_id)
            if question is None or question.type is not AnnotationType.QUESTION:
                raise AnsweredQuestionNotFoundError(str(question_annotation_id))
            if await uow.get_annotation(answer_annotation_id) is None:
                raise DocumentNotFoundError(f"annotation {answer_annotation_id} does not exist")
            now = utc_now()
            candidate = ClaimRelation(
                relation=ClaimRelationKind.ANSWERED_BY,
                provenance=RelationProvenance.USER_AUTHORED,
                status=RelationStatus.ACCEPTED,
                question_annotation_id=question_annotation_id,
                answer_annotation_id=answer_annotation_id,
                created_at=now,
                accepted_at=now,
            )
            relation = await uow.add_claim_relation(candidate)
            # Repetir a mesma resposta é idempotente: nada mudou, nada a projetar.
            if relation.id == candidate.id:
                await uow.add_outbox_event(_claim_relation_event(relation))
            await uow.commit()
        return relation

    async def relate_concepts(
        self, *, source_concept_id: UUID, target_concept_id: UUID
    ) -> ClaimRelation:
        """Associa dois conceitos já visíveis para o usuário (`RELATED_TO`).

        Conceito de outra conta responde como inexistente: a associação só existe entre
        conceitos do próprio dono.
        """
        if source_concept_id == target_concept_id:
            raise ValueError("a concept cannot be related to itself")
        async with self._uow_factory() as uow:
            for concept_id in (source_concept_id, target_concept_id):
                if await uow.get_concept(concept_id) is None:
                    raise RelatedConceptNotFoundError(str(concept_id))
            now = utc_now()
            candidate = ClaimRelation(
                relation=ClaimRelationKind.RELATED_TO,
                provenance=RelationProvenance.USER_AUTHORED,
                status=RelationStatus.ACCEPTED,
                source_concept_id=source_concept_id,
                target_concept_id=target_concept_id,
                created_at=now,
                accepted_at=now,
            )
            relation = await uow.add_claim_relation(candidate)
            # Repetir a mesma associação é idempotente: nada mudou, nada a projetar.
            if relation.id == candidate.id:
                await uow.add_outbox_event(_claim_relation_event(relation))
            await uow.commit()
        return relation

    async def delete_claim_relation(self, relation_id: UUID) -> ClaimRelation:
        """Remove qualquer aresta semântica. Aresta alheia responde como inexistente.

        A aresta é devolvida porque a remoção no grafo precisa das pontas: o evento carrega
        os identificadores que identificam o par removido.
        """
        async with self._uow_factory() as uow:
            relation = await uow.get_claim_relation(relation_id)
            if relation is None:
                raise ClaimRelationNotFoundError(str(relation_id))
            await uow.remove_claim_relation(relation_id)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="claim_relation",
                    aggregate_id=relation_id,
                    event_type="claim.relation.removed",
                    event_key=f"claim-relation-removed:{relation_id}",
                    payload=_relation_payload(relation),
                )
            )
            await uow.commit()
        return relation

    async def get_reading_progress(self) -> ReadingProgressSnapshot:
        async with self._uow_factory() as uow:
            sessions = await uow.list_study_sessions()
            session_counts = await uow.list_session_document_page_counts()
            document_counts = await uow.list_global_document_page_counts()
        return build_progress_snapshot(
            [session.id for session in sessions], session_counts, document_counts
        )

    async def record_viewed_page(
        self, *, session_id: UUID, document_id: UUID, page_number: int
    ) -> ReadingProgressUpdate:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            document = await uow.get_document(document_id)
            if document is None:
                raise DocumentNotFoundError(str(document_id))
            if await uow.get_study_session_document(session_id, document_id) is None:
                raise SessionDocumentNotFoundError("document is not in this study session")
            if not 1 <= page_number <= document.page_count:
                raise PageOutsideDocumentError(
                    f"page {page_number} is outside document page count {document.page_count}"
                )
            await uow.add_viewed_page(session_id, document_id, page_number, utc_now())
            await uow.commit()
            session_counts = await uow.list_session_document_page_counts(session_id)
            document_counts = await uow.list_global_document_page_counts(document_id)
        snapshot = build_progress_snapshot([session_id], session_counts, document_counts)
        return ReadingProgressUpdate(
            study_session_id=session_id,
            document_id=document_id,
            session=snapshot.sessions[session_id],
            document_global=snapshot.documents[document_id],
        )

    async def create_study_session(self, *, theme: str | None = None) -> StudySession:
        clean_theme = theme.strip() if theme else None
        session = StudySession(
            theme=clean_theme or None,
            theme_origin=ThemeOrigin.USER if clean_theme else None,
            theme_updated_at=utc_now() if clean_theme else None,
        )
        async with self._uow_factory() as uow:
            await uow.add_study_session(session)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session",
                    aggregate_id=session.id,
                    event_type="study_session.created",
                    payload={
                        "study_session_id": str(session.id),
                        "theme_origin": session.theme_origin.value if session.theme_origin else None,
                    },
                )
            )
            await uow.commit()
        return session

    async def list_study_sessions(self) -> list[StudySession]:
        async with self._uow_factory() as uow:
            return await uow.list_study_sessions()

    async def get_study_session(self, session_id: UUID) -> StudySession:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            return session

    async def list_study_session_documents(
        self, session_id: UUID
    ) -> list[tuple[StudySessionDocument, Document]]:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            links = await uow.list_study_session_documents(session_id)
            entries: list[tuple[StudySessionDocument, Document]] = []
            for link in links:
                document = await uow.get_document(link.document_id)
                if document is not None:
                    entries.append((link, document))
            return entries

    async def attach_document(
        self, *, session_id: UUID, document_id: UUID
    ) -> StudySessionDocument:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            if await uow.get_document(document_id) is None:
                raise DocumentNotFoundError(str(document_id))
            if await uow.get_study_session_document(session_id, document_id) is not None:
                raise SessionDocumentConflictError("document is already part of this session")

            position = await uow.next_study_session_position(session_id)
            link = StudySessionDocument(
                study_session_id=session_id,
                document_id=document_id,
                position=position,
            )
            await uow.add_study_session_document(link)
            await uow.update_study_session(_touched(session))
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session_document",
                    aggregate_id=link.id,
                    event_type="study_session.document_attached",
                    payload={
                        "study_session_id": str(session_id),
                        "document_id": str(document_id),
                        "position": position,
                    },
                )
            )
            await uow.commit()
        return link

    async def detach_document(self, *, session_id: UUID, document_id: UUID) -> None:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            link = await uow.get_study_session_document(session_id, document_id)
            if link is None:
                raise SessionDocumentNotFoundError(
                    f"document {document_id} is not part of session {session_id}"
                )
            await uow.remove_study_session_document(session_id, document_id)
            await uow.update_study_session(_touched(session))
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session_document",
                    aggregate_id=link.id,
                    event_type="study_session.document_removed",
                    payload={
                        "study_session_id": str(session_id),
                        "document_id": str(document_id),
                    },
                )
            )
            await uow.commit()

    async def set_study_session_theme(self, *, session_id: UUID, theme: str) -> StudySession:
        clean_theme = theme.strip()
        if not clean_theme:
            raise ValueError("theme cannot be blank")
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            updated = _with_theme(session, clean_theme, ThemeOrigin.USER)
            await uow.update_study_session(updated)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session",
                    aggregate_id=session_id,
                    event_type="study_session.theme_set",
                    event_key=(
                        f"study_session:{session_id}:theme_set:"
                        f"{updated.theme_updated_at.isoformat()}"
                    ),
                    payload={
                        "study_session_id": str(session_id),
                        "theme_origin": ThemeOrigin.USER.value,
                    },
                )
            )
            await uow.commit()
        return updated

    async def list_study_session_suggestions(self, session_id: UUID) -> list[AISuggestion]:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            return await uow.list_ai_suggestions(STUDY_SESSION_SUBJECT, session_id)

    async def create_theme_suggestion(
        self, *, session_id: UUID, provider: TopicSuggestionProvider
    ) -> AISuggestion:
        context = await self._topic_context(session_id)
        # A chamada ao modelo acontece fora de qualquer unidade de trabalho: a IA nunca
        # participa da transação que grava o domínio.
        suggestion = await provider.suggest(context)

        record = AISuggestion(
            suggestion_type=AISuggestionType.STUDY_SESSION_THEME.value,
            subject_type=STUDY_SESSION_SUBJECT,
            subject_id=session_id,
            payload={"theme": suggestion.theme, "rationale": suggestion.rationale},
            provider=suggestion.provider,
            model=suggestion.model,
        )
        async with self._uow_factory() as uow:
            await uow.add_ai_suggestion(record)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="ai_suggestion",
                    aggregate_id=record.id,
                    event_type="ai_suggestion.created",
                    payload={
                        "suggestion_id": str(record.id),
                        "subject_type": STUDY_SESSION_SUBJECT,
                        "subject_id": str(session_id),
                        "suggestion_type": record.suggestion_type,
                        "provider": record.provider,
                        "model": record.model,
                    },
                )
            )
            await uow.commit()
        return record

    async def accept_theme_suggestion(
        self, *, session_id: UUID, suggestion_id: UUID
    ) -> StudySession:
        async with self._uow_factory() as uow:
            session = await uow.get_study_session(session_id)
            if session is None:
                raise StudySessionNotFoundError(str(session_id))
            suggestion = await _pending_suggestion(uow, session_id, suggestion_id)
            theme = str(suggestion.payload.get("theme", "")).strip()
            if not theme:
                raise ValueError("suggestion payload has no usable theme")

            accepted = AISuggestion(
                id=suggestion.id,
                suggestion_type=suggestion.suggestion_type,
                subject_type=suggestion.subject_type,
                subject_id=suggestion.subject_id,
                payload=suggestion.payload,
                provider=suggestion.provider,
                model=suggestion.model,
                status=SuggestionStatus.ACCEPTED,
                created_at=suggestion.created_at,
                accepted_at=utc_now(),
            )
            updated = _with_theme(session, theme, ThemeOrigin.AI_SUGGESTION)
            await uow.update_ai_suggestion(accepted)
            await uow.update_study_session(updated)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="ai_suggestion",
                    aggregate_id=accepted.id,
                    event_type="ai_suggestion.accepted",
                    payload={
                        "suggestion_id": str(accepted.id),
                        "subject_id": str(session_id),
                        "provider": accepted.provider,
                        "model": accepted.model,
                    },
                )
            )
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="study_session",
                    aggregate_id=session_id,
                    event_type="study_session.theme_set",
                    event_key=(
                        f"study_session:{session_id}:theme_set:"
                        f"{updated.theme_updated_at.isoformat()}"
                    ),
                    payload={
                        "study_session_id": str(session_id),
                        "theme_origin": ThemeOrigin.AI_SUGGESTION.value,
                        "suggestion_id": str(accepted.id),
                    },
                )
            )
            await uow.commit()
        return updated

    async def reject_theme_suggestion(
        self, *, session_id: UUID, suggestion_id: UUID
    ) -> AISuggestion:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            suggestion = await _pending_suggestion(uow, session_id, suggestion_id)
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
                rejected_at=utc_now(),
            )
            await uow.update_ai_suggestion(rejected)
            await uow.add_outbox_event(
                OutboxEvent(
                    aggregate_type="ai_suggestion",
                    aggregate_id=rejected.id,
                    event_type="ai_suggestion.rejected",
                    payload={"suggestion_id": str(rejected.id), "subject_id": str(session_id)},
                )
            )
            await uow.commit()
        return rejected

    async def _topic_context(self, session_id: UUID) -> TopicSuggestionContext:
        async with self._uow_factory() as uow:
            if await uow.get_study_session(session_id) is None:
                raise StudySessionNotFoundError(str(session_id))
            links = await uow.list_study_session_documents(session_id)
            if not links:
                raise SessionWithoutDocumentsError(
                    "a study session needs at least one document before a theme suggestion"
                )
            titles: list[str] = []
            quotes: list[str] = []
            comments: list[str] = []
            for link in links:
                document = await uow.get_document(link.document_id)
                if document is None:
                    continue
                titles.append(document.title)
                for annotation in await uow.list_annotations(link.document_id):
                    quotes.append(annotation.quote)
                    if annotation.comment:
                        comments.append(annotation.comment)
        return TopicSuggestionContext(
            document_titles=tuple(titles),
            quotes=tuple(quotes),
            comments=tuple(comments),
        )


def _touched(session: StudySession) -> StudySession:
    return StudySession(
        id=session.id,
        theme=session.theme,
        theme_origin=session.theme_origin,
        theme_updated_at=session.theme_updated_at,
        created_at=session.created_at,
        updated_at=utc_now(),
    )


def _with_theme(session: StudySession, theme: str, origin: ThemeOrigin) -> StudySession:
    now = utc_now()
    return StudySession(
        id=session.id,
        theme=theme,
        theme_origin=origin,
        theme_updated_at=now,
        created_at=session.created_at,
        updated_at=now,
    )


async def _pending_suggestion(
    uow: UnitOfWork, session_id: UUID, suggestion_id: UUID
) -> AISuggestion:
    suggestion = await uow.get_ai_suggestion(suggestion_id)
    if suggestion is None or suggestion.subject_id != session_id:
        raise SuggestionNotFoundError(str(suggestion_id))
    if suggestion.status is not SuggestionStatus.PENDING:
        raise SuggestionAlreadyResolvedError(
            f"suggestion {suggestion_id} is already {suggestion.status.value}"
        )
    return suggestion


def _curation_event(request: CurationRequest) -> OutboxEvent:
    return OutboxEvent(
        aggregate_type="question_curation", aggregate_id=request.id,
        event_type="question.curation.requested",
        event_key=f"question-curation:{request.annotation_id}:{request.version}",
        payload={
            "request_id": str(request.id),
            "annotation_id": str(request.annotation_id),
            "study_session_id": str(request.study_session_id),
            "version": request.version,
        },
    )


def _claim_event(claim: Claim) -> OutboxEvent:
    """Evento de projeção do nó `Claim`.

    O payload carrega o que o worker precisa para reconstruir o nó a partir do PostgreSQL: a
    própria referência e o estado que o grafo exibe. A chave é de fato pontual — o nó é o
    mesmo, e reprojetá-lo é idempotente por `MERGE`.
    """
    return OutboxEvent(
        aggregate_type="claim",
        aggregate_id=claim.id,
        event_type="claim.created",
        payload={
            "claim_id": str(claim.id),
            "provenance": claim.provenance.value,
            "status": claim.status.value,
        },
    )


def _claim_relation_event(
    relation: ClaimRelation, *, claim_id: UUID | None = None
) -> OutboxEvent:
    """Evento de projeção de uma aresta semântica.

    As pontas viajam no payload porque são elas que identificam a aresta no grafo: o worker
    confirma no PostgreSQL que ela ainda existe antes de reprojetar, e o mesmo payload
    identifica a aresta a remover. `claim_id` é preenchido quando a aresta é criada junto com
    a afirmação, para a projeção da aresta não depender da ordem das entregas.
    """
    return OutboxEvent(
        aggregate_type="claim_relation",
        aggregate_id=relation.id,
        event_type="claim.relation.created",
        payload=_relation_payload(relation, claim_id=claim_id),
    )


def _relation_payload(
    relation: ClaimRelation, *, claim_id: UUID | None = None
) -> dict[str, object]:
    """Identificadores da aresta. Nenhum deles é dado de conteúdo: é referência de origem.

    `relation_id` viaja junto para que a remoção possa ser logada e depurada sem adivinhar
    qual aresta o par identifica.
    """
    return {
        "relation_id": str(relation.id),
        "relation": relation.relation.value,
        "annotation_id": _optional_id(relation.annotation_id),
        "claim_id": _optional_id(claim_id if claim_id is not None else relation.claim_id),
        "question_annotation_id": _optional_id(relation.question_annotation_id),
        "answer_annotation_id": _optional_id(relation.answer_annotation_id),
        "source_concept_id": _optional_id(relation.source_concept_id),
        "target_concept_id": _optional_id(relation.target_concept_id),
        "provenance": relation.provenance.value,
        "status": relation.status.value,
    }


def _optional_id(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


async def _evidence_for(
    uow: UnitOfWork, *, claim: Claim, relation: ClaimRelation
) -> ClaimEvidence:
    """Monta a evidência a partir da anotação de origem, quando ela ainda existe.

    O trecho fica vazio se a anotação não existe mais: a afirmação continua sendo do usuário
    e não desaparece por causa da evidência.
    """
    annotation_id = relation.annotation_id or relation.question_annotation_id
    if annotation_id is None:
        return ClaimEvidence(claim=claim, relation=relation)
    annotation = await uow.get_annotation(annotation_id)
    if annotation is None:
        return ClaimEvidence(claim=claim, relation=relation)
    document = await uow.get_document(annotation.document_id)
    return ClaimEvidence(
        claim=claim,
        relation=relation,
        quote=annotation.quote,
        comment=annotation.comment,
        document_id=annotation.document_id,
        document_title=document.title if document else None,
        page_number=annotation.page_number,
    )
