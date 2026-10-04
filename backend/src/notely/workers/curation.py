"""Consome pedidos de fontes sem bloquear o salvamento da dúvida no Reader."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from notely.core.curation import RETRIEVAL_VERSION, SourceCandidate
from notely.core.models import utc_now
from notely.db.corpus_search import search_session_candidates
from notely.db.models import (
    AnnotationRow, CuratedSourceRow, CurationRequestRow,
    DocumentCorpusChunkRow, DocumentCorpusIndexRow, DocumentRow,
    StudySessionDocumentRow,
)
from notely.db.outbox import DEFAULT_BATCH_SIZE, SqlAlchemyOutboxStore
from notely.providers.pdf_text import EXTRACTOR_VERSION
from notely.providers.source_curation import (
    LexicalSourceCurationProvider, SourceCurationProvider,
)

logger = logging.getLogger("notely.workers.curation")
EVENT_TYPES = frozenset({"question.curation.requested"})


async def _candidate_is_current(
    session: AsyncSession, study_session_id: UUID, candidate: SourceCandidate
) -> bool:
    link = await session.scalar(select(StudySessionDocumentRow.id).where(
        StudySessionDocumentRow.study_session_id == study_session_id,
        StudySessionDocumentRow.document_id == candidate.document_id,
    ))
    document = await session.get(DocumentRow, candidate.document_id)
    index = await session.get(DocumentCorpusIndexRow, candidate.document_id)
    chunk = await session.get(DocumentCorpusChunkRow, (
        candidate.document_id, candidate.page_number, candidate.chunk_number,
    ))
    return bool(
        link and document and index and chunk
        and index.status == "ready" and index.extractor_version == EXTRACTOR_VERSION
        and index.source_sha256 == document.sha256
        and 1 <= candidate.page_number <= document.page_count
        and chunk.content_sha256 == candidate.content_sha256
        and chunk.text_content == candidate.excerpt
        and chunk.start_offset == candidate.start_offset
        and chunk.end_offset == candidate.end_offset
    )


async def _index_state(session: AsyncSession, study_session_id: UUID) -> str:
    rows = (await session.execute(
        select(DocumentRow, DocumentCorpusIndexRow)
        .join(StudySessionDocumentRow, StudySessionDocumentRow.document_id == DocumentRow.id)
        .outerjoin(DocumentCorpusIndexRow, DocumentCorpusIndexRow.document_id == DocumentRow.id)
        .where(StudySessionDocumentRow.study_session_id == study_session_id)
    )).all()
    if not rows:
        return "empty_session"
    if any(index is not None and index.status == "failed" for _, index in rows):
        return "failed"
    if any(
        index is None or index.source_sha256 != document.sha256
        or index.extractor_version != EXTRACTOR_VERSION
        for document, index in rows
    ):
        return "pending"
    return "ready"


async def run_batch(
    *, store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    provider: SourceCurationProvider | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    provider = provider or LexicalSourceCurationProvider()
    stats = {"claimed": 0, "ready": 0, "no_source": 0, "pending": 0, "failed": 0}
    async with sessions() as session:
        events = await store.claim(session=session, batch_size=batch_size, event_types=EVENT_TYPES)
        stats["claimed"] = len(events)
        for event in events:
            request = await session.get(CurationRequestRow, event.aggregate_id)
            if request is None or request.version != event.payload.get("version") or request.status != "pending":
                await store.mark_processed(session=session, event_id=event.id)
                continue
            annotation = await session.get(AnnotationRow, request.annotation_id)
            try:
                if annotation is None or annotation.deleted_at is not None or annotation.type != "question":
                    raise ValueError("question is unavailable")
                origin_link = await session.scalar(select(StudySessionDocumentRow.id).where(
                    StudySessionDocumentRow.study_session_id == request.study_session_id,
                    StudySessionDocumentRow.document_id == annotation.document_id,
                ))
                if origin_link is None:
                    raise ValueError("question document is no longer in the study session")
                index_state = await _index_state(session, request.study_session_id)
                if index_state == "pending":
                    raise RuntimeError("Indexação em andamento")
                if index_state == "failed":
                    raise ValueError("Indexação indisponível para um documento da sessão")
                if index_state == "empty_session":
                    raise ValueError("session has no documents")
                candidates = await search_session_candidates(
                    session, study_session_id=request.study_session_id,
                    question=annotation.comment or "", quote=annotation.quote,
                    origin_document_id=annotation.document_id,
                    origin_page_number=annotation.page_number,
                )
                choices = []
                if candidates:
                    try:
                        choices = await provider.evaluate(
                            question=annotation.comment or "", quote=annotation.quote,
                            candidates=candidates,
                        )
                    except Exception as exc:
                        raise RuntimeError("Provider de curadoria indisponível") from exc
                by_id = {candidate.id: candidate for candidate in candidates}
                validated: list[tuple[SourceCandidate, str]] = []
                used_ids: set[UUID] = set()
                for choice in choices:
                    candidate = by_id.get(choice.candidate_id)
                    if (
                        candidate is None or candidate.id in used_ids
                        or not choice.reason.strip() or len(choice.reason) > 300
                        or not await _candidate_is_current(session, request.study_session_id, candidate)
                    ):
                        continue
                    used_ids.add(candidate.id)
                    validated.append((candidate, choice.reason.strip()))
                    if len(validated) == 3:
                        break
                await session.execute(delete(CuratedSourceRow).where(CuratedSourceRow.request_id == request.id))
                for rank, (candidate, reason) in enumerate(validated, 1):
                    session.add(CuratedSourceRow(
                        id=uuid4(), request_id=request.id,
                        document_id=candidate.document_id,
                        page_number=candidate.page_number,
                        chunk_number=candidate.chunk_number,
                        chunk_sha256=candidate.content_sha256,
                        start_offset=candidate.start_offset,
                        end_offset=candidate.end_offset,
                        excerpt=candidate.excerpt, reason=reason, rank=rank,
                        provider=provider.name, model=provider.model,
                        retrieval_version=RETRIEVAL_VERSION, created_at=utc_now(),
                    ))
                request.status = "ready" if validated else "no_source"
                request.attempt_count = event.attempt_count + 1
                request.last_error = None
                request.updated_at = utc_now()
                request.completed_at = request.updated_at
                await store.mark_processed(session=session, event_id=event.id)
                stats[request.status] += 1
            except (RuntimeError, ValueError) as exc:
                logger.warning("curation failed for request %s: %s", request.id, exc)
                await store.mark_failed(session=session, event_id=event.id, error=str(exc))
                request.attempt_count = event.attempt_count + 1
                request.updated_at = utc_now()
                request.last_error = str(exc)[:500]
                if request.attempt_count >= store.max_attempts or isinstance(exc, ValueError):
                    request.status = "failed"
                    request.completed_at = request.updated_at
                    await store.mark_processed(session=session, event_id=event.id)
                    stats["failed"] += 1
                else:
                    stats["pending"] += 1
        await session.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Curate question sources from session PDFs")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--database-url", default=os.environ.get(
        "NOTELY_DATABASE_URL", "postgresql+asyncpg://notely:notely@localhost:5432/notely"
    ))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def run() -> None:
        engine = create_async_engine(args.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyOutboxStore(sessions)
        try:
            while True:
                stats = await run_batch(
                    store=store, sessions=sessions, batch_size=args.batch_size
                )
                logger.info("batch processed %s", stats)
                if args.once:
                    break
                if stats["claimed"] == 0:
                    await asyncio.sleep(args.interval)
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
