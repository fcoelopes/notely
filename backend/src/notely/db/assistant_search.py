"""Recuperação do que ancora uma resposta do assistente: corpus da sessão e anotações.

Diferente da busca de fontes para uma dúvida, aqui a consulta é livre e não parte de uma
anotação específica, então não há página de origem para desempatar relevância.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from notely.core.curation import query_terms
from notely.core.models import CorpusPassage
from notely.db.models import (
    AnnotationRow,
    DocumentCorpusChunkRow,
    DocumentCorpusIndexRow,
    DocumentRow,
    StudySessionDocumentRow,
)
from notely.providers.pdf_text import EXTRACTOR_VERSION


async def search_session_passages(
    session: AsyncSession,
    *,
    study_session_id: UUID,
    user_id: UUID,
    question: str,
    document_id: UUID | None = None,
    limit: int = 5,
) -> list[CorpusPassage]:
    """Trechos do corpus da sessão que respondem à pergunta, por busca lexical."""
    terms = query_terms(question, "")
    if not terms or limit < 1:
        return []
    # Termos vêm de regex e são passados como parâmetro para to_tsquery, nunca
    # interpolados em SQL bruto.
    tsquery = func.to_tsquery("simple", " | ".join(terms))
    rank = func.ts_rank_cd(DocumentCorpusChunkRow.search_vector, tsquery)
    statement = (
        select(DocumentCorpusChunkRow, DocumentRow, rank.label("rank"))
        .join(
            StudySessionDocumentRow,
            StudySessionDocumentRow.document_id == DocumentCorpusChunkRow.document_id,
        )
        .join(DocumentRow, DocumentRow.id == DocumentCorpusChunkRow.document_id)
        .join(
            DocumentCorpusIndexRow,
            DocumentCorpusIndexRow.document_id == DocumentCorpusChunkRow.document_id,
        )
        .where(
            StudySessionDocumentRow.study_session_id == study_session_id,
            StudySessionDocumentRow.user_id == user_id,
            DocumentRow.user_id == user_id,
            DocumentCorpusIndexRow.status == "ready",
            DocumentCorpusIndexRow.extractor_version == EXTRACTOR_VERSION,
            DocumentCorpusIndexRow.source_sha256 == DocumentRow.sha256,
            DocumentCorpusChunkRow.search_vector.op("@@")(tsquery),
        )
        .order_by(
            rank.desc(),
            DocumentCorpusChunkRow.document_id,
            DocumentCorpusChunkRow.page_number,
        )
        .limit(max(limit * 4, 12))
    )
    if document_id is not None:
        statement = statement.where(DocumentCorpusChunkRow.document_id == document_id)

    rows = (await session.execute(statement)).all()
    passages: list[CorpusPassage] = []
    seen: set[tuple[UUID, int]] = set()
    for chunk, document, score in rows:
        key = (document.id, chunk.page_number)
        if key in seen or chunk.page_number > document.page_count:
            continue
        seen.add(key)
        passages.append(
            CorpusPassage(
                document_id=document.id,
                document_title=document.title,
                page_number=chunk.page_number,
                excerpt=chunk.text_content,
                score=float(score),
            )
        )
        if len(passages) == limit:
            break
    return passages


async def list_session_annotations(
    session: AsyncSession,
    *,
    study_session_id: UUID,
    user_id: UUID,
    limit: int = 8,
) -> list[AnnotationRow]:
    """Anotações do usuário na sessão: é a voz dele que ancora a resposta."""
    return list(
        (
            await session.scalars(
                select(AnnotationRow)
                .where(
                    AnnotationRow.study_session_id == study_session_id,
                    AnnotationRow.user_id == user_id,
                    AnnotationRow.deleted_at.is_(None),
                )
                .order_by(AnnotationRow.created_at.desc(), AnnotationRow.id)
                .limit(limit)
            )
        ).all()
    )
