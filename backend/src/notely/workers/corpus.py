"""Constrói em PostgreSQL uma projeção textual reconstruível dos PDFs armazenados."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
from tempfile import SpooledTemporaryFile
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from notely.core.corpus import chunk_page
from notely.core.models import utc_now
from notely.db.models import (
    DocumentCorpusChunkRow,
    DocumentCorpusIndexRow,
    DocumentCorpusPageRow,
    DocumentRow,
    OutboxEventRow,
)
from notely.db.outbox import DEFAULT_BATCH_SIZE, SqlAlchemyOutboxStore
from notely.providers.pdf_text import EXTRACTOR_VERSION, PdfTextExtractionError, extract_page_text
from notely.providers.storage import MinioObjectStorage, ObjectStorageError

logger = logging.getLogger("notely.workers.corpus")
EVENT_TYPES = frozenset({"document.created", "document.corpus.rebuild"})
MAX_PDF_BYTES = 100 * 1024 * 1024


async def read_and_extract(storage: MinioObjectStorage, row: DocumentRow) -> list[str]:
    digest = hashlib.sha256()
    total = 0
    with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as content:
        async for chunk in storage.open_pdf(storage_uri=row.storage_uri):
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                raise PdfTextExtractionError("stored PDF exceeds extraction size limit")
            digest.update(chunk)
            content.write(chunk)
        if digest.hexdigest() != row.sha256:
            raise PdfTextExtractionError("stored PDF SHA-256 differs from document")
        return await asyncio.to_thread(extract_page_text, content, row.page_count)


async def save_pages(session: AsyncSession, row: DocumentRow, pages: list[str]) -> None:
    await session.execute(
        delete(DocumentCorpusPageRow).where(DocumentCorpusPageRow.document_id == row.id)
    )
    for number, text in enumerate(pages, 1):
        session.add(
            DocumentCorpusPageRow(
                document_id=row.id,
                page_number=number,
                text_content=text,
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                extractor_version=EXTRACTOR_VERSION,
            )
        )
    await session.flush()
    for number, text in enumerate(pages, 1):
        for chunk in chunk_page(text):
            session.add(
                DocumentCorpusChunkRow(
                    document_id=row.id,
                    page_number=number,
                    chunk_number=chunk.number,
                    start_offset=chunk.start,
                    end_offset=chunk.end,
                    text_content=chunk.text,
                    content_sha256=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                )
            )
    index = await session.get(DocumentCorpusIndexRow, row.id)
    if index is None:
        index = DocumentCorpusIndexRow(document_id=row.id)
        session.add(index)
    index.source_sha256 = row.sha256
    index.extractor_version = EXTRACTOR_VERSION
    index.status = "ready" if any(pages) else "empty"
    index.actual_page_count = len(pages)
    index.last_error = None
    index.indexed_at = utc_now()


async def save_failure(
    session: AsyncSession, row: DocumentRow, error: str, *, actual_page_count: int | None = None
) -> None:
    await session.execute(
        delete(DocumentCorpusPageRow).where(DocumentCorpusPageRow.document_id == row.id)
    )
    index = await session.get(DocumentCorpusIndexRow, row.id)
    if index is None:
        index = DocumentCorpusIndexRow(document_id=row.id)
        session.add(index)
    index.source_sha256 = row.sha256
    index.extractor_version = EXTRACTOR_VERSION
    index.status = "failed"
    index.actual_page_count = actual_page_count
    index.last_error = error[:500]
    index.indexed_at = utc_now()


async def enqueue_missing_documents(session: AsyncSession) -> int:
    pending = exists(
        select(OutboxEventRow.id).where(
            OutboxEventRow.aggregate_type == "document",
            OutboxEventRow.aggregate_id == DocumentRow.id,
            OutboxEventRow.event_type.in_(list(EVENT_TYPES)),
            OutboxEventRow.processed_at.is_(None),
            OutboxEventRow.attempt_count < 8,
        )
    )
    rows = (
        await session.scalars(
            select(DocumentRow)
            .outerjoin(DocumentCorpusIndexRow, DocumentCorpusIndexRow.document_id == DocumentRow.id)
            .where(
                (DocumentCorpusIndexRow.document_id.is_(None))
                | (DocumentCorpusIndexRow.status == "failed")
                | (DocumentCorpusIndexRow.extractor_version != EXTRACTOR_VERSION),
                ~pending,
            )
        )
    ).all()
    for row in rows:
        now = utc_now()
        statement = insert(OutboxEventRow).values(
            id=uuid4(),
            aggregate_type="document",
            aggregate_id=row.id,
            event_type="document.corpus.rebuild",
            payload={"document_id": str(row.id)},
            event_key=f"corpus-backfill:{EXTRACTOR_VERSION}:{row.id}:{uuid4()}",
            created_at=now,
            available_at=now,
            attempt_count=0,
        ).on_conflict_do_nothing()
        await session.execute(statement)
    return len(rows)


async def run_batch(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    storage: MinioObjectStorage,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    stats = {"claimed": 0, "indexed": 0, "failed": 0}
    async with sessions() as session:
        events = await store.claim(
            session=session, batch_size=batch_size, event_types=EVENT_TYPES
        )
        stats["claimed"] = len(events)
        for event in events:
            document_id = UUID(str(event.payload.get("document_id", event.aggregate_id)))
            row = await session.get(DocumentRow, document_id)
            if row is None:
                await store.mark_processed(session=session, event_id=event.id)
                continue
            index = await session.get(DocumentCorpusIndexRow, document_id)
            if (
                index is not None
                and index.status in {"ready", "empty"}
                and index.extractor_version == EXTRACTOR_VERSION
                and index.source_sha256 == row.sha256
            ):
                await store.mark_processed(session=session, event_id=event.id)
                continue
            try:
                pages = await read_and_extract(storage, row)
            except PdfTextExtractionError as exc:
                await save_failure(
                    session, row, str(exc), actual_page_count=exc.actual_page_count
                )
                await store.mark_processed(session=session, event_id=event.id)
                stats["failed"] += 1
            except (ObjectStorageError, OSError) as exc:
                await store.mark_failed(session=session, event_id=event.id, error=str(exc))
                if event.attempt_count + 1 >= store.max_attempts:
                    await save_failure(session, row, str(exc))
                stats["failed"] += 1
            else:
                await save_pages(session, row, pages)
                await store.mark_processed(session=session, event_id=event.id)
                stats["indexed"] += 1
        await session.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Index stored PDF text by page")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--backfill", action="store_true", help="queue documents missing an index")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "NOTELY_DATABASE_URL", "postgresql+asyncpg://notely:notely@localhost:5432/notely"
        ),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def run() -> None:
        engine = create_async_engine(args.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = MinioObjectStorage(
            endpoint=os.environ.get("NOTELY_MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.environ.get("NOTELY_MINIO_ACCESS_KEY", "notely"),
            secret_key=os.environ.get("NOTELY_MINIO_SECRET_KEY", "notely-development-only"),
            bucket=os.environ.get("NOTELY_MINIO_BUCKET", "notely-documents"),
            secure=os.environ.get("NOTELY_MINIO_SECURE", "false").lower() == "true",
        )
        store = SqlAlchemyOutboxStore(sessions)
        try:
            if args.backfill:
                async with sessions() as session:
                    queued = await enqueue_missing_documents(session)
                    await session.commit()
                    logger.info("backfill considered %s documents", queued)
            while True:
                stats = await run_batch(
                    store=store, sessions=sessions, storage=storage, batch_size=args.batch_size
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
