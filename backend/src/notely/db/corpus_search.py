"""Recuperação lexical limitada aos documentos de uma sessão de estudo."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from notely.core.curation import SourceCandidate, candidate_id, query_terms, rank_candidates
from notely.providers.pdf_text import EXTRACTOR_VERSION
from notely.db.models import (
    DocumentCorpusChunkRow,
    DocumentCorpusIndexRow,
    DocumentCorpusPageRow,
    DocumentRow,
    StudySessionDocumentRow,
)


async def search_session_candidates(
    session: AsyncSession,
    *,
    study_session_id: UUID,
    question: str,
    quote: str,
    origin_document_id: UUID,
    origin_page_number: int,
    limit: int = 12,
) -> list[SourceCandidate]:
    origin_text = await session.scalar(
        select(DocumentCorpusPageRow.text_content).where(
            DocumentCorpusPageRow.document_id == origin_document_id,
            DocumentCorpusPageRow.page_number == origin_page_number,
        )
    )
    context = ""
    if origin_text and quote:
        offset = origin_text.casefold().find(quote.casefold())
        if offset >= 0:
            context = origin_text[max(0, offset - 150):offset + len(quote) + 150]
    terms = query_terms(question, quote, context)
    if not terms or limit < 1:
        return []
    # Termos são extraídos por regex; cada termo é passado como parâmetro para
    # to_tsquery, nunca interpolado em SQL bruto.
    tsquery = func.to_tsquery("simple", " | ".join(terms))
    rank = func.ts_rank_cd(DocumentCorpusChunkRow.search_vector, tsquery)
    rows = (
        await session.execute(
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
                DocumentCorpusIndexRow.status == "ready",
                DocumentCorpusIndexRow.extractor_version == EXTRACTOR_VERSION,
                DocumentCorpusIndexRow.source_sha256 == DocumentRow.sha256,
                DocumentCorpusChunkRow.search_vector.op("@@")(tsquery),
            )
            .order_by(rank.desc(), DocumentCorpusChunkRow.document_id, DocumentCorpusChunkRow.page_number)
            .limit(max(limit * 6, 24))
        )
    ).all()
    candidates: list[SourceCandidate] = []
    seen_pages: set[tuple[UUID, int]] = set()
    for chunk, document, score in rows:
        key = (document.id, chunk.page_number)
        if key in seen_pages or chunk.page_number > document.page_count:
            continue
        seen_pages.add(key)
        candidates.append(SourceCandidate(
            id=candidate_id(document.id, chunk.page_number, chunk.chunk_number, chunk.content_sha256),
            document_id=document.id,
            document_title=document.title,
            page_number=chunk.page_number,
            page_count=document.page_count,
            chunk_number=chunk.chunk_number,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            excerpt=chunk.text_content,
            content_sha256=chunk.content_sha256,
            score=float(score),
        ))
    return rank_candidates(candidates, origin_document_id, origin_page_number)[:limit]
