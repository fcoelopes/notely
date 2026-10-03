"""Indexação real no banco de teste; pula quando PostgreSQL/migration não estão disponíveis."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from notely.core.services import NotelyService
from notely.db.models import DocumentCorpusIndexRow, DocumentCorpusPageRow, OutboxEventRow
from notely.db.outbox import SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.workers.corpus import enqueue_missing_documents, run_batch
from test_corpus import sample_pdf

DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)


class MemoryStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0

    async def open_pdf(self, *, storage_uri: str) -> AsyncIterator[bytes]:
        self.calls += 1
        yield self.content


@pytest.fixture
async def context():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("select 1 from document_corpus_pages limit 1"))
            await conn.execute(text("delete from reader_events"))
            await conn.execute(text("delete from outbox_events"))
            await conn.execute(text("delete from documents"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Corpus integration database is unavailable: {exc}")
    yield sessions
    await engine.dispose()


async def register(sessions, content: bytes, *, page_count: int = 1):
    service = NotelyService(lambda: SqlAlchemyUnitOfWork(sessions))
    return await service.register_document(
        sha256=hashlib.sha256(content).hexdigest(),
        title="Fonte de teste",
        filename=f"{uuid4()}.pdf",
        page_count=page_count,
        storage_uri="s3://notely-documents/test.pdf",
    )


async def test_indexes_text_and_retry_does_not_duplicate_pages(context) -> None:
    content = sample_pdf()
    document = await register(context, content)
    storage = MemoryStorage(content)
    store = SqlAlchemyOutboxStore(context)

    result = await run_batch(store=store, sessions=context, storage=storage)  # type: ignore[arg-type]
    assert result == {"claimed": 1, "indexed": 1, "failed": 0}
    async with context() as db:
        page = await db.get(DocumentCorpusPageRow, (document.id, 1))
        index = await db.get(DocumentCorpusIndexRow, document.id)
        assert page is not None and page.text_content == "Searchable evidence on page one"
        assert index is not None and index.status == "ready"
        assert await db.scalar(
            select(func.count()).select_from(DocumentCorpusPageRow).where(
                DocumentCorpusPageRow.search_vector.op("@@")(
                    func.plainto_tsquery("simple", "evidence")
                )
            )
        ) == 1
        event = await db.scalar(select(OutboxEventRow))
        assert event is not None
        event.processed_at = None
        await db.commit()

    retry = await run_batch(store=store, sessions=context, storage=storage)  # type: ignore[arg-type]
    assert retry == {"claimed": 1, "indexed": 0, "failed": 0}
    assert storage.calls == 1
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(DocumentCorpusPageRow)) == 1


async def test_wrong_page_count_records_failure_without_source_pages(context) -> None:
    content = sample_pdf()
    document = await register(context, content, page_count=2)
    result = await run_batch(
        store=SqlAlchemyOutboxStore(context),
        sessions=context,
        storage=MemoryStorage(content),  # type: ignore[arg-type]
    )
    assert result == {"claimed": 1, "indexed": 0, "failed": 1}
    async with context() as db:
        index = await db.get(DocumentCorpusIndexRow, document.id)
        assert index is not None and index.status == "failed"
        assert "page count" in (index.last_error or "")
        assert index.actual_page_count == 1
        assert await db.scalar(select(func.count()).select_from(DocumentCorpusPageRow)) == 0


async def test_sha_mismatch_never_indexes_text(context) -> None:
    content = sample_pdf()
    document = await register(context, content)
    result = await run_batch(
        store=SqlAlchemyOutboxStore(context),
        sessions=context,
        storage=MemoryStorage(sample_pdf("Different source")),  # type: ignore[arg-type]
    )
    assert result == {"claimed": 1, "indexed": 0, "failed": 1}
    async with context() as db:
        index = await db.get(DocumentCorpusIndexRow, document.id)
        assert index is not None and index.status == "failed"
        assert "SHA-256" in (index.last_error or "")
        assert await db.scalar(select(func.count()).select_from(DocumentCorpusPageRow)) == 0


async def test_backfill_queues_missing_document_once(context) -> None:
    content = sample_pdf()
    await register(context, content)
    async with context() as db:
        event = await db.scalar(select(OutboxEventRow))
        assert event is not None
        event.processed_at = event.created_at
        await db.commit()

    async with context() as db:
        assert await enqueue_missing_documents(db) == 1
        await db.commit()
    async with context() as db:
        assert await enqueue_missing_documents(db) == 0
        await db.commit()

    result = await run_batch(
        store=SqlAlchemyOutboxStore(context),
        sessions=context,
        storage=MemoryStorage(content),  # type: ignore[arg-type]
    )
    assert result == {"claimed": 1, "indexed": 1, "failed": 0}
