"""Integração real: PostgreSQL + Transactional Outbox + TimescaleDB.

Requer um PostgreSQL com a extensão TimescaleDB e as migrations aplicadas:

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U notely -d postgres -c "create database notely_test owner notely"
for m in 0001_initial 0002_study_sessions 0003_reading_sessions_and_reader_events; do
  docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U notely -d notely_test \
    < infra/db/migrations/$m.up.sql
done
```

Cada teste limpa as tabelas do banco de teste antes de rodar. Sem esse banco os testes são
pulados, para que a suíte continue rodando em qualquer ambiente.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from notely.core.models import AnnotationType
from notely.core.services import NotelyService
from notely.db.models import OutboxEventRow, ReaderEventRow
from notely.db.outbox import SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.workers.timescale import run_batch

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


@pytest.fixture
async def context() -> AsyncIterator[IntegrationContext]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    context = IntegrationContext(engine)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("select 1 from reader_events limit 1"))
            await connection.execute(text("delete from reader_events"))
            await connection.execute(text("delete from outbox_events"))
            await connection.execute(text("delete from documents"))
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"TimescaleDB integration database is unavailable: {exc}")

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


async def _timeline(context: IntegrationContext, **filters: object) -> list[ReaderEventRow]:
    conditions = [getattr(ReaderEventRow, key) == value for key, value in filters.items()]
    async with context.sessions() as db:
        rows = (
            await db.scalars(
                select(ReaderEventRow).where(*conditions).order_by(ReaderEventRow.time)
            )
        ).all()
    return list(rows)


async def test_outbox_reaches_the_timeline_and_survives_a_retry(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    session = await context.service.start_reading_session(document_id=document_id, page_number=6)
    annotation = await context.service.create_annotation(
        document_id=document_id,
        page_number=6,
        annotation_type=AnnotationType.QUESTION,
        quote=ANCHOR_QUOTE,
        comment="Como isso se aplica ao consolidado?",
        position={
            "rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}],
            "textQuoteSelector": {"exact": ANCHOR_QUOTE},
        },
        reading_session_id=session.id,
    )

    stats = await run_batch(store=context.store, sessions=context.sessions)
    assert stats["claimed"] == 2
    assert stats["projected"] == 2
    assert stats["failed"] == 0

    rows = await _timeline(context, document_id=document_id)
    started = next(row for row in rows if row.event_type == "reading.started")
    created = next(row for row in rows if row.event_type == "annotation.created")

    assert started.session_id == session.id
    assert started.time == session.started_at
    assert created.annotation_id == annotation.id
    assert created.session_id == session.id
    assert created.annotation_type == "question"
    assert created.page_number == 6
    assert created.passage_id == annotation.passage_id
    assert created.passage_id_version == annotation.passage_id_version
    assert created.time == annotation.created_at
    # O filename fica na sessão, não é repetido em cada linha da trilha.
    assert "filename" not in ReaderEventRow.__table__.columns

    # Simula retry (o processo morreu depois de projetar, antes de marcar): não duplica.
    async with context.sessions() as db:
        await db.execute(
            text(
                "update outbox_events set processed_at = null, claimed_at = null, attempt_count = 0"
            )
        )
        await db.commit()

    retry = await run_batch(store=context.store, sessions=context.sessions)
    assert retry["claimed"] == 2
    assert retry["projected"] == 0
    assert len(await _timeline(context, document_id=document_id)) == len(rows)


async def test_ending_the_session_is_projected_with_the_end_page(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    session = await context.service.start_reading_session(document_id=document_id, page_number=1)
    await context.service.update_reading_session(session_id=session.id, page_number=4)
    await context.service.update_reading_session(session_id=session.id, page_number=9, ended=True)

    await run_batch(store=context.store, sessions=context.sessions)

    ended = (await _timeline(context, event_type="reading.ended"))[0]
    assert ended.session_id == session.id
    assert ended.page_number == 9

    # O toque intermediário não gera linha temporal: só início e fim.
    types = [row.event_type for row in await _timeline(context, document_id=document_id)]
    assert types == ["reading.started", "reading.ended"]


async def test_sql_anchor_matches_the_python_algorithm(context: IntegrationContext) -> None:
    document_id = await _register_document(context)
    annotation = await context.service.create_annotation(
        document_id=document_id,
        page_number=6,
        annotation_type=AnnotationType.HIGHLIGHT,
        quote="  Materialidade   depende da natureza  ",
        position={"rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}]},
    )

    async with context.sessions() as db:
        recomputed = await db.scalar(
            text(
                """
                select encode(
                    sha256(
                        convert_to(
                            'notely-passage:v1'
                            || chr(10) || d.sha256
                            || chr(10) || a.page_number::text
                            || chr(10) || btrim(regexp_replace(a.quote, '\\s+', ' ', 'g'))
                            || chr(10) || ''
                            || chr(10) || '',
                            'UTF8'
                        )
                    ),
                    'hex'
                )
                from annotations a
                join documents d on d.id = a.document_id
                where a.id = :annotation_id
                """
            ),
            {"annotation_id": annotation.id},
        )

    assert recomputed == annotation.passage_id


async def test_timeline_keeps_annotations_imported_without_a_session(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    annotation = await context.service.create_annotation(
        document_id=document_id,
        page_number=2,
        annotation_type=AnnotationType.HIGHLIGHT,
        quote="Trecho importado de um PDF antigo",
        position={"rects": [{"x": 0.4, "y": 0.5, "width": 0.2, "height": 0.02}]},
    )
    assert annotation.reading_session_id is None

    await run_batch(store=context.store, sessions=context.sessions)

    rows = await _timeline(context, annotation_id=annotation.id)
    assert len(rows) == 1
    assert rows[0].session_id is None
    assert rows[0].passage_id == annotation.passage_id


async def test_failed_projection_is_recorded_and_retried_later(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    broken_event_id = uuid4()

    async with context.sessions() as db:
        await db.execute(
            text(
                """
                insert into outbox_events (
                    id, aggregate_type, aggregate_id, event_type, payload, created_at, available_at
                ) values (
                    :id, 'reading_session', :aggregate_id, 'reading.started', cast(:payload as jsonb),
                    :now, :now
                )
                """
            ),
            {
                "id": broken_event_id,
                "aggregate_id": document_id,
                "payload": '{"document_id": "not-a-uuid"}',
                "now": datetime.now(UTC),
            },
        )
        await db.commit()

    stats = await run_batch(store=context.store, sessions=context.sessions)
    assert stats["failed"] == 1
    assert stats["projected"] == 0

    async with context.sessions() as db:
        row = await db.get(OutboxEventRow, broken_event_id)
        assert row is not None
        assert row.processed_at is None
        assert row.attempt_count == 1
        assert row.last_error is not None
        assert row.available_at > datetime.now(UTC) + timedelta(seconds=1)

        claimed = await context.store.claim(session=db, batch_size=50)
        assert broken_event_id not in [event.id for event in claimed]
        await db.rollback()

    assert await _timeline(context, event_id=broken_event_id) == []


async def test_events_outside_the_reading_trail_are_not_claimed(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)

    stats = await run_batch(store=context.store, sessions=context.sessions)

    # document.created pertence a outro consumidor: não é reivindicado por este worker,
    # continua pendente e não ocupa o lote nem trava eventos novos.
    assert stats["claimed"] == 0
    assert stats["projected"] == 0
    assert await _timeline(context, document_id=document_id) == []

    async with context.sessions() as db:
        pending = await db.scalar(
            text(
                "select count(*) from outbox_events "
                "where processed_at is null and last_error is null"
            )
        )
    assert pending == 1
