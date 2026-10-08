"""Integração real: anotação → conceitos no PostgreSQL → projeção no Apache AGE.

Requer PostgreSQL com AGE + TimescaleDB e as migrations aplicadas, incluindo a `0010`:

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U notely -d postgres -c "create database notely_test owner notely"
backend/.venv/bin/python scripts/migrate.py --database notely_test
```
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from notely.core.models import AnnotationType
from notely.core.services import NotelyService
from notely.db.graph import AgeGraph
from notely.db.models import AnnotationConceptRow, ConceptRow
from notely.db.outbox import CONSUMER_CONCEPTS, SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.providers.concept_extraction import (
    ConceptExtractionContext,
    ConceptExtractionUnavailableError,
    ExtractedConcept,
)
from notely.workers import concepts as concepts_worker
from notely.workers import graph as graph_worker
from auth_helpers import TEST_USER_ID, ensure_test_user
from auth_helpers import TEST_USER_ID

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

ANCHOR_QUOTE = "A materialidade depende da natureza ou magnitude das informações"


class ScriptedConceptProvider:
    """Provider roteirizado: resultados determinísticos, sem rede."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, concepts: list[ExtractedConcept] | Exception) -> None:
        self.concepts = concepts
        self.contexts: list[ConceptExtractionContext] = []

    async def extract(self, context: ConceptExtractionContext) -> list[ExtractedConcept]:
        self.contexts.append(context)
        if isinstance(self.concepts, Exception):
            raise self.concepts
        return list(self.concepts)


class IntegrationContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.store = SqlAlchemyOutboxStore(self.sessions)
        self.service = NotelyService(lambda: SqlAlchemyUnitOfWork(self.sessions, user_id=TEST_USER_ID))
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
            await connection.execute(text("select 1 from annotation_concepts limit 1"))
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Concepts integration database is unavailable: {exc}")

    async with context.sessions() as db:
        await context.graph.truncate(session=db)
        await db.execute(text("delete from outbox_events"))
        await db.execute(text("delete from concepts"))
        await db.execute(text("delete from documents"))
        await db.commit()

    await ensure_test_user(context.sessions)
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
    *,
    quote: str = ANCHOR_QUOTE,
    comment: str | None = None,
) -> UUID:
    annotation = await context.service.create_annotation(
        document_id=document_id,
        page_number=6,
        annotation_type=annotation_type,
        quote=quote,
        comment=comment,
        position={
            "rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}],
            "textQuoteSelector": {"exact": quote},
        },
    )
    return annotation.id


async def _run_concepts(
    context: IntegrationContext, provider: ScriptedConceptProvider
) -> dict[str, int]:
    return await concepts_worker.run_batch(
        store=context.store, sessions=context.sessions, provider=provider
    )


async def _counts(context: IntegrationContext) -> dict[str, int]:
    async with context.sessions() as db:
        return {
            "documents": await context.graph.count(session=db, label="Document"),
            "annotations": await context.graph.count(session=db, label="Annotation"),
            "concepts": await context.graph.count(session=db, label="Concept"),
            "refers_to": await context.graph.count_labelled_edges(session=db, label="REFERS_TO"),
            "questions": await context.graph.count_labelled_edges(session=db, label="QUESTIONS"),
        }


async def test_extraction_persists_inference_and_reaches_the_graph(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    annotation_id = await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider(
        [ExtractedConcept(name="Materialidade", confidence=0.8), ExtractedConcept(name="Relevância")]
    )

    stats = await _run_concepts(context, provider)

    assert stats == {"claimed": 1, "extracted": 1, "relations": 2, "failed": 0}
    # O contexto enviado ao provider é o mínimo: título, tipo, trecho e comentário.
    assert provider.contexts[0].document_title == "IAS 1 - apresentação"
    assert provider.contexts[0].annotation_type == "highlight"

    async with context.sessions() as db:
        relations = (await db.scalars(select(AnnotationConceptRow))).all()
        concepts = (await db.scalars(select(ConceptRow))).all()
    assert len(concepts) == 2
    assert len(relations) == 2
    # Inferência de modelo: nunca autoral enquanto não houver aceite.
    assert all(relation.provenance == "ai_inference" for relation in relations)
    assert all(relation.status == "suggested" for relation in relations)
    assert all(relation.accepted_at is None for relation in relations)
    assert all(relation.relation == "refers_to" for relation in relations)
    assert all(relation.provider == "scripted" and relation.model == "scripted-1" for relation in relations)
    assert {relation.concept_id for relation in relations} == {concept.id for concept in concepts}

    projected = await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )
    assert projected["projected"] == 3  # documento + anotação + conceitos extraídos

    counts = await _counts(context)
    assert counts["documents"] == 1
    assert counts["annotations"] == 1
    assert counts["concepts"] == 2
    assert counts["refers_to"] == 2


async def test_question_annotations_link_with_questions_edges(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(
        context,
        document_id,
        AnnotationType.QUESTION,
        comment="Como isso se aplica ao consolidado?",
    )
    provider = ScriptedConceptProvider([ExtractedConcept(name="Consolidação")])

    await _run_concepts(context, provider)
    await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )

    async with context.sessions() as db:
        relation = (await db.scalars(select(AnnotationConceptRow))).one()
    assert relation.relation == "questions"

    counts = await _counts(context)
    assert counts["concepts"] == 1
    assert counts["questions"] == 1
    assert counts["refers_to"] == 0


async def test_reprocessing_does_not_duplicate_concepts_or_edges(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider([ExtractedConcept(name="Materialidade")])

    await _run_concepts(context, provider)
    await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )
    before = await _counts(context)

    # Simula retry: a entrega volta a ficar pendente para o consumidor de conceitos.
    async with context.sessions() as db:
        await db.execute(
            text(
                "update outbox_deliveries set processed_at = null, claimed_at = null, "
                "attempt_count = 0 where consumer = 'concepts'"
            )
        )
        await db.commit()

    retry = await _run_concepts(context, provider)

    # A extração já existente é reaproveitada: nenhum conceito novo e nenhuma chamada
    # adicional ao provider.
    assert retry["relations"] == 0
    assert len(provider.contexts) == 1

    await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )
    assert await _counts(context) == before


async def test_backfill_enqueues_missing_annotations_and_can_run_again(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider([ExtractedConcept(name="Materialidade")])

    async with context.sessions() as db:
        assert await concepts_worker.enqueue_missing_annotations(db, provider=provider) == 1
        await db.commit()
    # O pedido já está na fila: reexecutar não duplica.
    async with context.sessions() as db:
        assert await concepts_worker.enqueue_missing_annotations(db, provider=provider) == 0
        await db.commit()

    await _run_concepts(context, provider)
    # Com conceitos gravados, a anotação sai do backfill.
    async with context.sessions() as db:
        assert await concepts_worker.enqueue_missing_annotations(db, provider=provider) == 0
        await db.commit()

    # Reextração com outro provider volta a funcionar: a chave do pedido inclui o modelo.
    async with context.sessions() as db:
        await db.execute(text("delete from annotation_concepts"))
        await db.execute(text("delete from concepts"))
        await db.commit()
    async with context.sessions() as db:
        other = ScriptedConceptProvider([ExtractedConcept(name="Outro")])
        assert await concepts_worker.enqueue_missing_annotations(db, provider=other) == 1
        await db.commit()


async def test_provider_failure_keeps_the_annotation_and_retries_later(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    annotation_id = await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider(ConceptExtractionUnavailableError("provider down"))

    stats = await _run_concepts(context, provider)

    assert stats["failed"] == 1
    assert stats["relations"] == 0
    # A anotação continua intacta e nenhuma relação foi gravada.
    annotations = await context.service.list_document_annotations(document_id)
    assert [annotation.id for annotation in annotations] == [annotation_id]
    async with context.sessions() as db:
        assert (await db.scalars(select(AnnotationConceptRow))).all() == []
        delivery = await db.scalar(
            text(
                "select attempt_count from outbox_deliveries "
                "where consumer = :consumer and attempt_count > 0"
            ).bindparams(consumer=CONSUMER_CONCEPTS)
        )
    assert delivery == 1
