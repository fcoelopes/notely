"""Fluxo real de curadoria em PostgreSQL: escopo, estados e reentrega."""

from __future__ import annotations

import asyncio
import hashlib
import os
from uuid import uuid4

from notely.providers.source_curation import SourceChoice
from notely.core.services import SessionDocumentNotFoundError

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from notely.api.app import create_app
from notely.core.models import AnnotationType
from notely.core.services import NotelyService
from notely.db.models import CuratedSourceRow, CurationRequestRow, DocumentCorpusIndexRow, OutboxEventRow
from notely.db.outbox import SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.workers.corpus import run_batch as index_batch
from notely.workers.curation import run_batch as curate_batch
from test_corpus import sample_pdf
from test_integration_corpus import MemoryStorage

DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)


@pytest.fixture
async def context():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("select 1 from question_curated_sources limit 1"))
            await conn.execute(text("delete from reader_events"))
            await conn.execute(text("delete from outbox_events"))
            await conn.execute(text("delete from documents"))
            await conn.execute(text("delete from study_sessions"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Curation integration database is unavailable: {exc}")
    yield sessions
    await engine.dispose()


async def register_and_index(sessions, title: str, passage: str):
    content = sample_pdf(passage)
    service = NotelyService(lambda: SqlAlchemyUnitOfWork(sessions))
    document = await service.register_document(
        sha256=hashlib.sha256(content).hexdigest(), title=title,
        filename=f"{uuid4()}.pdf", page_count=1,
        storage_uri="s3://notely-documents/test.pdf",
    )
    result = await index_batch(
        store=SqlAlchemyOutboxStore(sessions), sessions=sessions,
        storage=MemoryStorage(content),
    )
    assert result["indexed"] == 1
    return document


async def make_question(sessions, *, index: bool = True):
    service = NotelyService(lambda: SqlAlchemyUnitOfWork(sessions))
    content_a = sample_pdf("Enzyme catalysis lowers activation energy")
    content_b = sample_pdf("Enzyme catalysis can lower activation energy through stabilization")
    content_c = sample_pdf("Enzyme catalysis outside the study session")
    documents = []
    for title, content in (("Origin", content_a), ("Other source", content_b), ("Outside", content_c)):
        document = await service.register_document(
            sha256=hashlib.sha256(content).hexdigest(), title=title,
            filename=f"{uuid4()}.pdf", page_count=1,
            storage_uri="s3://notely-documents/test.pdf",
        )
        documents.append(document)
        if index:
            # Each event points to a distinct object; the in-memory storage
            # must be paired with the matching event in registration order.
            result = await index_batch(
                store=SqlAlchemyOutboxStore(sessions), sessions=sessions,
                storage=MemoryStorage(content), batch_size=1,
            )
            assert result["indexed"] == 1
    study_session = await service.create_study_session(theme="Biochemistry")
    for document in documents[:2]:
        await service.attach_document(session_id=study_session.id, document_id=document.id)
    annotation = await service.create_annotation(
        document_id=documents[0].id, page_number=1,
        annotation_type=AnnotationType.QUESTION,
        quote="Enzyme catalysis", comment="How does enzyme catalysis lower activation energy?",
        position={"rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}]},
        study_session_id=study_session.id,
    )
    return service, study_session, annotation, documents


async def test_question_gets_verified_sources_only_from_its_session(context) -> None:
    service, study_session, annotation, documents = await make_question(context)
    pending = await service.get_question_sources(
        session_id=study_session.id, annotation_id=annotation.id
    )
    assert pending.request.status == "pending" and pending.sources == []
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(CurationRequestRow)) == 1
        assert await db.scalar(select(func.count()).select_from(OutboxEventRow).where(
            OutboxEventRow.event_type == "question.curation.requested"
        )) == 1

    result = await curate_batch(store=SqlAlchemyOutboxStore(context), sessions=context)
    assert result["ready"] == 1
    view = await service.get_question_sources(
        session_id=study_session.id, annotation_id=annotation.id
    )
    assert view.request.status == "ready"
    assert 1 <= len(view.sources) <= 3
    assert documents[2].id not in {item.source.document_id for item in view.sources}
    assert any(item.source.document_id == documents[1].id for item in view.sources)
    assert all(item.available and item.source.page_number == 1 for item in view.sources)
    assert all("Enzyme catalysis" in item.source.excerpt for item in view.sources)

    app = create_app(lambda: service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/study-sessions/{study_session.id}/questions/{annotation.id}/sources"
        )
        wrong_scope = await client.get(
            f"/api/study-sessions/{uuid4()}/questions/{annotation.id}/sources"
        )
    assert response.status_code == 200
    assert response.json()["sources"][0]["provider"] == "lexical_search"
    assert wrong_scope.status_code == 404

    async with context() as db:
        event = await db.scalar(select(OutboxEventRow).where(
            OutboxEventRow.event_type == "question.curation.requested"
        ))
        assert event is not None
        event.processed_at = None
        await db.commit()
    await curate_batch(store=SqlAlchemyOutboxStore(context), sessions=context)
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(CuratedSourceRow)) == len(view.sources)

    await service.detach_document(session_id=study_session.id, document_id=documents[1].id)
    detached = await service.get_question_sources(
        session_id=study_session.id, annotation_id=annotation.id
    )
    assert any(not item.available for item in detached.sources)


async def test_two_workers_do_not_duplicate_sources(context) -> None:
    service, study_session, annotation, _ = await make_question(context)
    results = await asyncio.gather(
        curate_batch(store=SqlAlchemyOutboxStore(context), sessions=context),
        curate_batch(store=SqlAlchemyOutboxStore(context), sessions=context),
    )
    assert sum(result["claimed"] for result in results) == 1
    view = await service.get_question_sources(session_id=study_session.id, annotation_id=annotation.id)
    assert view.request.status == "ready"
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(CuratedSourceRow)) == len(view.sources)


async def test_index_wait_and_provider_failure_preserve_question(context) -> None:
    service, study_session, annotation, _ = await make_question(context, index=False)
    store = SqlAlchemyOutboxStore(context, max_attempts=2, backoff_seconds=0)
    first = await curate_batch(store=store, sessions=context)
    assert first["pending"] == 1
    view = await service.get_question_sources(session_id=study_session.id, annotation_id=annotation.id)
    assert view.request.status == "pending"
    assert view.request.last_error == "Indexação em andamento"

    second = await curate_batch(store=store, sessions=context)
    assert second["failed"] == 1
    view = await service.get_question_sources(session_id=study_session.id, annotation_id=annotation.id)
    assert view.request.status == "failed"
    assert view.sources == []
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(CuratedSourceRow)) == 0
    updated = await service.retry_question_curation(
        session_id=study_session.id, annotation_id=annotation.id
    )
    assert updated.status == "pending" and updated.version == 2
    assert await service.list_document_annotations(annotation.document_id)


async def test_old_corpus_version_waits_for_chunk_backfill(context) -> None:
    service, study_session, annotation, documents = await make_question(context)
    async with context() as db:
        index = await db.get(DocumentCorpusIndexRow, documents[1].id)
        assert index is not None
        index.extractor_version = "old-extractor"
        await db.commit()
    result = await curate_batch(
        store=SqlAlchemyOutboxStore(context, backoff_seconds=0), sessions=context
    )
    assert result["pending"] == 1
    view = await service.get_question_sources(session_id=study_session.id, annotation_id=annotation.id)
    assert view.request.status == "pending" and view.sources == []


async def test_invalid_provider_ids_are_discarded_and_retry_can_recover(context) -> None:
    service, study_session, annotation, _ = await make_question(context)

    class InvalidProvider:
        name = "invalid"
        model = "invalid-v1"

        async def evaluate(self, **_: object):
            return [SourceChoice(candidate_id=uuid4(), reason="Invented source")]

    result = await curate_batch(
        store=SqlAlchemyOutboxStore(context), sessions=context, provider=InvalidProvider()
    )
    assert result["no_source"] == 1
    view = await service.get_question_sources(session_id=study_session.id, annotation_id=annotation.id)
    assert view.request.status == "no_source" and view.sources == []
    await service.retry_question_curation(session_id=study_session.id, annotation_id=annotation.id)
    recovered = await curate_batch(store=SqlAlchemyOutboxStore(context), sessions=context)
    assert recovered["ready"] == 1
    view = await service.get_question_sources(session_id=study_session.id, annotation_id=annotation.id)
    assert view.request.version == 2 and view.sources


async def test_provider_failure_is_recoverable_without_losing_annotation(context) -> None:
    service, study_session, annotation, _ = await make_question(context)

    class BrokenProvider:
        name = "broken"
        model = "broken-v1"

        async def evaluate(self, **_: object):
            raise OSError("provider down")

    result = await curate_batch(
        store=SqlAlchemyOutboxStore(context, max_attempts=1),
        sessions=context, provider=BrokenProvider(),
    )
    assert result["failed"] == 1
    view = await service.get_question_sources(session_id=study_session.id, annotation_id=annotation.id)
    assert view.request.status == "failed" and view.sources == []
    assert await service.list_document_annotations(annotation.document_id)
    await service.retry_question_curation(session_id=study_session.id, annotation_id=annotation.id)
    assert (await curate_batch(store=SqlAlchemyOutboxStore(context), sessions=context))["ready"] == 1


async def test_question_rejects_a_document_outside_its_study_session(context) -> None:
    service, study_session, _, documents = await make_question(context)
    with pytest.raises(SessionDocumentNotFoundError):
        await service.create_annotation(
            document_id=documents[2].id, page_number=1,
            annotation_type=AnnotationType.QUESTION, quote="Enzyme catalysis",
            comment="Is this outside?", position={"rects": [1]},
            study_session_id=study_session.id,
        )
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(CurationRequestRow)) == 1


def test_chunks_keep_exact_page_offsets() -> None:
    from notely.core.corpus import chunk_page

    page = ("enzyme catalysis changes activation energy. " * 80).strip()
    chunks = chunk_page(page)
    assert len(chunks) > 1
    assert all(page[item.start:item.end] == item.text for item in chunks)
    assert all(len(item.text) <= 700 for item in chunks)
    assert all(item.number == number for number, item in enumerate(chunks))
