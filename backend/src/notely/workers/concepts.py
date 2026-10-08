"""Extrai conceitos das anotações e publica o pedido de projeção no grafo.

Conceito extraído é inferência de modelo, nunca dado autoral: as relações nascem
`suggested` e com proveniência `ai_inference`, e só o aceite explícito do usuário as
promove (AGENTS.md §1). O worker é idempotente e reprocessar não gasta modelo de novo.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from notely.core.concepts import normalize_concept_name, relation_for
from notely.core.models import AnnotationType, OutboxEvent, utc_now
from notely.db.models import (
    AnnotationConceptRow,
    AnnotationRow,
    ConceptRow,
    DocumentRow,
    OutboxDeliveryRow,
    OutboxEventRow,
)
from notely.db.outbox import (
    CONSUMER_CONCEPTS,
    DEFAULT_BATCH_SIZE,
    ClaimedEvent,
    SqlAlchemyOutboxStore,
)
from notely.providers.chat_model import AnthropicChatProvider, OpenAICompatibleChatProvider
from notely.providers.concept_extraction import (
    ConceptExtractionContext,
    ConceptExtractionProvider,
    ConceptExtractionUnavailableError,
    ExtractedConcept,
    HeuristicConceptExtractionProvider,
    ModelConceptExtractionProvider,
)

logger = logging.getLogger("notely.workers.concepts")

REQUESTED_EVENT = "concepts.requested"
PUBLISHED_EVENT = "concepts.extracted"
EVENT_TYPES = frozenset({"annotation.created", REQUESTED_EVENT})
DEFAULT_DATABASE_URL = "postgresql+asyncpg://notely:notely@localhost:5432/notely"
DEFAULT_BACKFILL_LIMIT = 500


async def extract_annotation(
    *,
    session: AsyncSession,
    provider: ConceptExtractionProvider,
    annotation: AnnotationRow,
) -> int:
    """Extrai conceitos de uma anotação e grava as relações sugeridas. Devolve quantas
    relações foram realmente criadas."""
    document = await session.get(DocumentRow, annotation.document_id)
    if document is None:
        return 0

    extracted = await provider.extract(
        ConceptExtractionContext(
            document_title=document.title,
            annotation_type=annotation.type,
            quote=annotation.quote,
            comment=annotation.comment,
        )
    )
    relation = relation_for(AnnotationType(annotation.type)).value
    created = 0
    for concept in extracted:
        concept_id = await _upsert_concept(session, concept, user_id=annotation.user_id)
        if await _add_relation(
            session=session,
            annotation_id=annotation.id,
            concept_id=concept_id,
            relation=relation,
            provider=provider.name,
            model=provider.model,
            confidence=concept.confidence,
        ):
            created += 1
    return created


async def _upsert_concept(
    session: AsyncSession, concept: ExtractedConcept, *, user_id: UUID
) -> UUID:
    normalized = normalize_concept_name(concept.name)
    statement = (
        insert(ConceptRow)
        .values(
            id=uuid4(),
            user_id=user_id,
            name=concept.name,
            normalized_name=normalized,
            created_at=utc_now(),
        )
        # O conceito pertence a um usuário: a unicidade é (user_id, normalized_name).
        .on_conflict_do_update(
            index_elements=["user_id", "normalized_name"], set_={"name": concept.name}
        )
        .returning(ConceptRow.id)
    )
    return (await session.execute(statement)).scalar_one()


async def _add_relation(
    *,
    session: AsyncSession,
    annotation_id: UUID,
    concept_id: UUID,
    relation: str,
    provider: str,
    model: str,
    confidence: float | None,
) -> bool:
    statement = (
        insert(AnnotationConceptRow)
        .values(
            id=uuid4(),
            annotation_id=annotation_id,
            concept_id=concept_id,
            relation=relation,
            provenance="ai_inference",
            status="suggested",
            provider=provider,
            model=model,
            confidence=confidence,
            created_at=utc_now(),
        )
        .on_conflict_do_nothing(index_elements=["annotation_id", "concept_id", "relation"])
        .returning(AnnotationConceptRow.id)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


async def _has_relations(session: AsyncSession, annotation_id: UUID) -> bool:
    return (
        await session.scalar(
            select(AnnotationConceptRow.id)
            .where(AnnotationConceptRow.annotation_id == annotation_id)
            .limit(1)
        )
    ) is not None


async def _publish_extraction(
    session: AsyncSession,
    annotation_id: UUID,
    *,
    provider: ConceptExtractionProvider,
) -> bool:
    """Publica `concepts.extracted` para o worker de grafo projetar os conceitos.

    A chave inclui o provider: reextrair com outro modelo volta a disparar a projeção. A
    projeção é idempotente por `MERGE`, e `--rebuild` reconcilia o grafo com o PostgreSQL.
    """
    statement = (
        insert(OutboxEventRow)
        .values(
            id=uuid4(),
            aggregate_type="annotation",
            aggregate_id=annotation_id,
            event_type=PUBLISHED_EVENT,
            payload={"annotation_id": str(annotation_id)},
            event_key=f"concepts:{annotation_id}:{provider.name}:{provider.model}",
            created_at=utc_now(),
            available_at=utc_now(),
            attempt_count=0,
        )
        .on_conflict_do_nothing()
        .returning(OutboxEventRow.id)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


def _annotation_id(event: ClaimedEvent) -> UUID:
    return UUID(str(event.payload.get("annotation_id", event.aggregate_id)))


async def run_batch(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    provider: ConceptExtractionProvider,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    stats = {"claimed": 0, "extracted": 0, "relations": 0, "failed": 0}
    async with sessions() as session:
        claimed = await store.claim(
            session=session,
            consumer=CONSUMER_CONCEPTS,
            batch_size=batch_size,
            event_types=EVENT_TYPES,
        )
        stats["claimed"] = len(claimed)
        if not claimed:
            await session.commit()
            return stats

        processed: list[UUID] = []
        for event in claimed:
            try:
                annotation = await session.get(AnnotationRow, _annotation_id(event))
                if annotation is None or annotation.deleted_at is not None:
                    processed.append(event.id)
                    continue
                if await _has_relations(session, annotation.id):
                    # Já extraído: reprocessar não gasta modelo nem duplica relação, mas
                    # republica para o grafo alcançar o estado do PostgreSQL.
                    await _publish_extraction(session, annotation.id, provider=provider)
                else:
                    created = await extract_annotation(
                        session=session, provider=provider, annotation=annotation
                    )
                    stats["extracted"] += 1
                    stats["relations"] += created
                    if created:
                        await _publish_extraction(session, annotation.id, provider=provider)
            except ConceptExtractionUnavailableError as exc:
                # Falha do provider fica pendente para retry com backoff; a anotação
                # permanece intacta.
                stats["failed"] += 1
                await store.mark_failed(
                    session=session,
                    event_id=event.id,
                    consumer=CONSUMER_CONCEPTS,
                    error=str(exc),
                )
                continue
            processed.append(event.id)

        for event_id in processed:
            await store.mark_processed(
                session=session, event_id=event_id, consumer=CONSUMER_CONCEPTS
            )
        await session.commit()
    return stats


async def enqueue_missing_annotations(
    session: AsyncSession,
    *,
    provider: ConceptExtractionProvider,
    limit: int = DEFAULT_BACKFILL_LIMIT,
) -> int:
    """Enfileira extração para anotações ainda sem conceitos (backfill explícito).

    A pendência é checada na entrega do consumidor, e a chave do evento inclui o provider:
    trocar de modelo permite reextrair o acervo, sem duplicar um pedido já na fila.
    """
    has_relations = exists().where(AnnotationConceptRow.annotation_id == AnnotationRow.id)
    undelivered = ~exists().where(
        and_(
            OutboxDeliveryRow.event_id == OutboxEventRow.id,
            OutboxDeliveryRow.consumer == CONSUMER_CONCEPTS,
        )
    )
    unresolved = exists().where(
        and_(
            OutboxDeliveryRow.event_id == OutboxEventRow.id,
            OutboxDeliveryRow.consumer == CONSUMER_CONCEPTS,
            OutboxDeliveryRow.processed_at.is_(None),
        )
    )
    pending = exists().where(
        OutboxEventRow.event_type == REQUESTED_EVENT,
        OutboxEventRow.aggregate_id == AnnotationRow.id,
        or_(undelivered, unresolved),
    )
    rows = (
        await session.scalars(
            select(AnnotationRow.id)
            .where(AnnotationRow.deleted_at.is_(None), ~has_relations, ~pending)
            .order_by(AnnotationRow.created_at, AnnotationRow.id)
            .limit(limit)
        )
    ).all()
    queued = 0
    for annotation_id in rows:
        statement = (
            insert(OutboxEventRow)
            .values(
                id=uuid4(),
                aggregate_type="annotation",
                aggregate_id=annotation_id,
                event_type=REQUESTED_EVENT,
                payload={"annotation_id": str(annotation_id)},
                event_key=(
                    f"concepts-backfill:{annotation_id}:{provider.name}:{provider.model}:{uuid4()}"
                ),
                created_at=utc_now(),
                available_at=utc_now(),
                attempt_count=0,
            )
            .on_conflict_do_nothing()
            .returning(OutboxEventRow.id)
        )
        if (await session.execute(statement)).scalar_one_or_none() is not None:
            queued += 1
    return queued


async def run_forever(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    provider: ConceptExtractionProvider,
    batch_size: int = DEFAULT_BATCH_SIZE,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        stats = await run_batch(
            store=store, sessions=sessions, provider=provider, batch_size=batch_size
        )
        if stats["claimed"] == 0:
            await asyncio.sleep(interval_seconds)


def build_provider() -> ConceptExtractionProvider:
    """Provider por configuração. Sem configuração, o heurístico mantém o fluxo local."""
    kind = os.environ.get("NOTELY_CONCEPT_PROVIDER", "heuristic")
    if kind == "heuristic":
        return HeuristicConceptExtractionProvider()
    model = os.environ.get("NOTELY_CONCEPT_MODEL", "")
    api_key = os.environ.get("NOTELY_CONCEPT_API_KEY", "")
    timeout = float(os.environ.get("NOTELY_CONCEPT_TIMEOUT_SECONDS", "30"))
    endpoint = os.environ.get("NOTELY_CONCEPT_ENDPOINT", "")
    if kind == "anthropic":
        chat = AnthropicChatProvider(
            model=model,
            api_key=api_key,
            endpoint=endpoint or AnthropicChatProvider.DEFAULT_ENDPOINT,
            timeout_seconds=timeout,
        )
    elif kind == "openai_compatible":
        chat = OpenAICompatibleChatProvider(
            endpoint=endpoint, model=model, api_key=api_key, timeout_seconds=timeout
        )
    else:
        raise ValueError(f"unsupported concept provider: {kind}")
    return ModelConceptExtractionProvider(chat=chat)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract concepts from annotations")
    parser.add_argument("--once", action="store_true", help="process a single batch and exit")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="enqueue extraction for annotations without concepts, then continue",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("NOTELY_OUTBOX_INTERVAL_SECONDS", "2")),
        help="seconds to wait between batches",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("NOTELY_OUTBOX_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("NOTELY_DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    arguments = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def run() -> None:
        provider = build_provider()
        engine = create_async_engine(arguments.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyOutboxStore(sessions)
        try:
            if arguments.backfill:
                async with sessions() as session:
                    queued = await enqueue_missing_annotations(session, provider=provider)
                    await session.commit()
                logger.info("concept backfill queued %s annotations", queued)
            if arguments.once:
                stats = await run_batch(
                    store=store,
                    sessions=sessions,
                    provider=provider,
                    batch_size=arguments.batch_size,
                )
                logger.info("batch processed %s", stats)
                return
            await run_forever(
                store=store,
                sessions=sessions,
                provider=provider,
                batch_size=arguments.batch_size,
                interval_seconds=arguments.interval,
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
