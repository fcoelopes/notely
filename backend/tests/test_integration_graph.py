"""Integração real: PostgreSQL + Transactional Outbox + Apache AGE.

Requer um PostgreSQL com AGE + TimescaleDB e as migrations aplicadas, incluindo a
``0008`` (que cria o grafo ``notely`` e seus labels):

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U notely -d postgres -c "create database notely_test owner notely"
backend/.venv/bin/python scripts/migrate.py --database notely_test
```

Cada teste limpa o grafo e as tabelas de domínio antes de rodar. Sem esse banco os
testes são pulados, para que a suíte continue rodando em qualquer ambiente.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from notely.core.models import AnnotationType
from notely.core.services import NotelyService
from notely.db.graph import AgeGraph
from notely.db.outbox import SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.workers.graph import rebuild, run_batch

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

ANCHOR_QUOTE = "Materialidade depende da natureza ou magnitude das informações"


class IntegrationContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.store = SqlAlchemyOutboxStore(self.sessions)
        self.service = NotelyService(lambda: SqlAlchemyUnitOfWork(self.sessions))
        self.graph = AgeGraph()


@pytest.fixture
async def context() -> AsyncIterator[IntegrationContext]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    context = IntegrationContext(engine)
    try:
        async with engine.begin() as connection:
            found = await connection.scalar(
                text("select count(*) from ag_catalog.ag_graph where name = 'notely'")
            )
            if not found:
                raise RuntimeError("AGE graph 'notely' not found")
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"AGE integration database is unavailable: {exc}")

    async with context.sessions() as db:
        await context.graph.truncate(session=db)
        await db.execute(text("delete from outbox_events"))
        await db.execute(text("delete from documents"))
        await db.commit()

    yield context
    await engine.dispose()


async def _register_document(context: IntegrationContext) -> UUID:
    document = await context.service.register_document(
        sha256=uuid4().hex + uuid4().hex,
        title="IAS 1 - apresentação",
        filename="ias-1.pdf",
        page_count=38,
        storage_uri="s3://notely-documents/documents/7c/ias-1.pdf",
    )
    return document.id


async def _create_annotation(
    context: IntegrationContext,
    document_id: UUID,
    annotation_type: AnnotationType,
) -> UUID:
    annotation = await context.service.create_annotation(
        document_id=document_id,
        page_number=6,
        annotation_type=annotation_type,
        quote=ANCHOR_QUOTE,
        comment="Como isso se aplica ao consolidado?" if annotation_type is AnnotationType.QUESTION else None,
        position={
            "rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}],
            "textQuoteSelector": {"exact": ANCHOR_QUOTE},
        },
    )
    return annotation.id


async def _counts(context: IntegrationContext) -> dict[str, int]:
    async with context.sessions() as db:
        return {
            "documents": await context.graph.count(session=db, label="Document"),
            "annotations": await context.graph.count(session=db, label="Annotation"),
            "questions": await context.graph.count(session=db, label="Question"),
            "edges": await context.graph.count_edges(session=db),
        }


async def test_document_and_annotation_are_projected(context: IntegrationContext) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)

    stats = await run_batch(store=context.store, sessions=context.sessions, graph=context.graph)
    assert stats["claimed"] == 2
    assert stats["projected"] == 2
    assert stats["failed"] == 0

    counts = await _counts(context)
    assert counts == {"documents": 1, "annotations": 1, "questions": 0, "edges": 1}


async def test_question_is_projected_as_a_question_node(context: IntegrationContext) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.QUESTION)

    await run_batch(store=context.store, sessions=context.sessions, graph=context.graph)

    counts = await _counts(context)
    assert counts == {"documents": 1, "annotations": 0, "questions": 1, "edges": 1}


async def test_projection_survives_a_retry_without_duplicates(context: IntegrationContext) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)

    await run_batch(store=context.store, sessions=context.sessions, graph=context.graph)
    before = await _counts(context)

    # Simula retry: o processo morreu depois de projetar, antes de marcar como processado.
    async with context.sessions() as db:
        await db.execute(
            text("update outbox_events set processed_at = null, claimed_at = null, attempt_count = 0")
        )
        await db.commit()

    retry = await run_batch(store=context.store, sessions=context.sessions, graph=context.graph)
    assert retry["claimed"] == 2
    assert retry["projected"] == 2
    assert await _counts(context) == before


async def test_rebuild_reproduces_the_graph_from_postgresql(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    await _create_annotation(context, document_id, AnnotationType.QUESTION)

    await run_batch(store=context.store, sessions=context.sessions, graph=context.graph)
    incremental = await _counts(context)

    # Apaga o grafo por inteiro e reconstrói a partir do PostgreSQL.
    async with context.sessions() as db:
        await context.graph.truncate(session=db)
        await db.commit()
    assert (await _counts(context)) == {"documents": 0, "annotations": 0, "questions": 0, "edges": 0}

    projected = await rebuild(sessions=context.sessions, graph=context.graph)
    assert projected == 3  # 1 documento + 2 anotações
    assert await _counts(context) == incremental
