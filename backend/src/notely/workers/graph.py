from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from notely.db.graph import AgeGraph
from notely.db.models import (
    AnnotationConceptRow,
    AnnotationRow,
    ClaimRelationRow,
    ClaimRow,
    ConceptRow,
    DocumentRow,
)
from notely.db.outbox import (
    CONSUMER_GRAPH,
    DEFAULT_BATCH_SIZE,
    ClaimedEvent,
    SqlAlchemyOutboxStore,
)

logger = logging.getLogger("notely.workers.graph")

PROJECTED_EVENT_TYPES = frozenset(
    {
        "document.created",
        "annotation.created",
        "concepts.extracted",
        "claim.created",
        "claim.relation.created",
        "claim.deleted",
        "claim.relation.removed",
    }
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://notely:notely@localhost:5432/notely"


async def project_event(
    *, session: AsyncSession, graph: AgeGraph, event: ClaimedEvent
) -> None:
    """Projeta um evento de domínio no grafo, lendo o estado autoral do PostgreSQL.

    O outbox é o gatilho; o registro de domínio é a fonte de verdade dos dados que
    entram no nó/aresta. Ausência do registro (ex.: documento removido ou anotação
    excluída) é estado terminal — nada a projetar, sem erro.
    """
    if event.event_type == "document.created":
        document_id = UUID(str(event.payload["document_id"]))
        document = await session.get(DocumentRow, document_id)
        if document is None:
            return
        await graph.project_document(
            session=session, document_id=document.id, title=document.title
        )
        return

    if event.event_type == "annotation.created":
        annotation_id = UUID(str(event.payload["annotation_id"]))
        annotation = await session.get(AnnotationRow, annotation_id)
        if annotation is None or annotation.deleted_at is not None:
            return
        document = await session.get(DocumentRow, annotation.document_id)
        if document is None:
            return
        await graph.project_annotation(
            session=session,
            annotation_id=annotation.id,
            document_id=document.id,
            annotation_type=annotation.type,
            page_number=annotation.page_number,
            document_title=document.title,
        )
        return

    if event.event_type == "concepts.extracted":
        annotation_id = UUID(str(event.payload["annotation_id"]))
        annotation = await session.get(AnnotationRow, annotation_id)
        if annotation is None or annotation.deleted_at is not None:
            return
        await project_annotation_concepts(session=session, graph=graph, annotation=annotation)
        return

    if event.event_type == "claim.created":
        claim = await session.get(ClaimRow, UUID(str(event.payload["claim_id"])))
        if claim is None or claim.deleted_at is not None:
            return
        await project_claim(session=session, graph=graph, claim=claim)
        return

    if event.event_type == "claim.relation.created":
        await project_claim_relation_event(session=session, graph=graph, event=event)
        return

    if event.event_type == "claim.deleted":
        await graph.remove_claim(
            session=session, claim_id=UUID(str(event.payload["claim_id"]))
        )
        return

    if event.event_type == "claim.relation.removed":
        await remove_claim_relation_event(session=session, graph=graph, event=event)
        return

    # Não reivindicamos outros tipos; se chegar aqui, nada a projetar.
    logger.warning("unprojectable event type %s ignored", event.event_type)


async def project_claim(*, session: AsyncSession, graph: AgeGraph, claim: ClaimRow) -> None:
    """Projeta o nó `Claim` a partir do estado persistido no PostgreSQL.

    O nó é criado antes de qualquer aresta que o cite: a projeção da aresta usa `MATCH` na
    ponta, então o nó precisa existir para a aresta existir.
    """
    await graph.project_claim(
        session=session,
        claim_id=claim.id,
        text=claim.text,
        provenance=claim.provenance,
        status=claim.status,
    )


async def project_claim_relation(
    *, session: AsyncSession, graph: AgeGraph, relation: ClaimRelationRow
) -> None:
    """Projeta uma aresta semântica, garantindo que as pontas já existam no grafo.

    Nada aqui é inventado a partir do payload: as pontas são confirmadas no PostgreSQL, e a
    projeção só acontece com os registros presentes e não removidos. Repetir a mesma entrega
    é idempotente, porque tanto os nós das pontas quanto a aresta usam `MERGE`.
    """
    if relation.relation in ("supports", "contradicts"):
        if relation.annotation_id is None or relation.claim_id is None:
            return
        annotation = await session.get(AnnotationRow, relation.annotation_id)
        if annotation is None or annotation.deleted_at is not None:
            return
        claim = await session.get(ClaimRow, relation.claim_id)
        if claim is None or claim.deleted_at is not None:
            return
        await project_claim(session=session, graph=graph, claim=claim)
    elif relation.relation == "answered_by":
        if relation.question_annotation_id is None or relation.answer_annotation_id is None:
            return
        for annotation_id in (
            relation.question_annotation_id,
            relation.answer_annotation_id,
        ):
            annotation = await session.get(AnnotationRow, annotation_id)
            if annotation is None or annotation.deleted_at is not None:
                return
            document = await session.get(DocumentRow, annotation.document_id)
            if document is None:
                return
            await graph.project_annotation(
                session=session,
                annotation_id=annotation.id,
                document_id=document.id,
                annotation_type=annotation.type,
                page_number=annotation.page_number,
                document_title=document.title,
            )
    else:
        if relation.source_concept_id is None or relation.target_concept_id is None:
            return
        for concept_id in (relation.source_concept_id, relation.target_concept_id):
            concept = await session.get(ConceptRow, concept_id)
            if concept is None:
                return
            await graph.project_concept(
                session=session, concept_id=concept.id, name=concept.name
            )

    await graph.project_claim_relation(
        session=session,
        relation=relation.relation,
        provenance=relation.provenance,
        status=relation.status,
        annotation_id=relation.annotation_id,
        claim_id=relation.claim_id,
        question_annotation_id=relation.question_annotation_id,
        answer_annotation_id=relation.answer_annotation_id,
        source_concept_id=relation.source_concept_id,
        target_concept_id=relation.target_concept_id,
    )


async def _relation_row(
    session: AsyncSession, event: ClaimedEvent
) -> ClaimRelationRow | None:
    """Resolve a aresta pelo id do registro; o par ordenado é o caminho de recuperação."""
    relation_id = event.payload.get("relation_id")
    if relation_id is not None:
        row = await session.get(ClaimRelationRow, UUID(str(relation_id)))
        if row is not None:
            return row
    statement = select(ClaimRelationRow).where(
        ClaimRelationRow.relation == str(event.payload.get("relation")),
        # `IS NOT DISTINCT FROM`: as colunas que a relação não usa ficam nulas, e comparar
        # com `=` nunca casaria. É o mesmo critério da restrição `NULLS NOT DISTINCT`.
        ClaimRelationRow.annotation_id.is_not_distinct_from(
            _optional_uuid(event.payload.get("annotation_id"))
        ),
        ClaimRelationRow.claim_id.is_not_distinct_from(
            _optional_uuid(event.payload.get("claim_id"))
        ),
        ClaimRelationRow.question_annotation_id.is_not_distinct_from(
            _optional_uuid(event.payload.get("question_annotation_id"))
        ),
        ClaimRelationRow.answer_annotation_id.is_not_distinct_from(
            _optional_uuid(event.payload.get("answer_annotation_id"))
        ),
        ClaimRelationRow.source_concept_id.is_not_distinct_from(
            _optional_uuid(event.payload.get("source_concept_id"))
        ),
        ClaimRelationRow.target_concept_id.is_not_distinct_from(
            _optional_uuid(event.payload.get("target_concept_id"))
        ),
    )
    return await session.scalar(statement)


def _optional_uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value is not None else None


async def project_claim_relation_event(
    *, session: AsyncSession, graph: AgeGraph, event: ClaimedEvent
) -> None:
    """Projeta a aresta do evento. Aresta já removida no PostgreSQL é estado terminal."""
    relation = await _relation_row(session, event)
    if relation is None:
        return
    await project_claim_relation(session=session, graph=graph, relation=relation)


async def remove_claim_relation_event(
    *, session: AsyncSession, graph: AgeGraph, event: ClaimedEvent
) -> None:
    """Remove a aresta do grafo. O payload carrega as pontas, que identificam a aresta.

    A remoção não exige que a linha ainda exista: ela já foi apagada no PostgreSQL, e o
    grafo precisa acompanhar. `--rebuild` é a reconciliação final se algo ficar para trás.
    """
    payload = event.payload
    await graph.remove_claim_relation(
        session=session,
        relation=str(payload.get("relation")),
        annotation_id=_optional_uuid(payload.get("annotation_id")),
        claim_id=_optional_uuid(payload.get("claim_id")),
        question_annotation_id=_optional_uuid(payload.get("question_annotation_id")),
        answer_annotation_id=_optional_uuid(payload.get("answer_annotation_id")),
        source_concept_id=_optional_uuid(payload.get("source_concept_id")),
        target_concept_id=_optional_uuid(payload.get("target_concept_id")),
    )


async def project_annotation_concepts(
    *, session: AsyncSession, graph: AgeGraph, annotation: AnnotationRow
) -> int:
    """Projeta os conceitos de uma anotação a partir do estado persistido no PostgreSQL."""
    relations = (
        await session.scalars(
            select(AnnotationConceptRow).where(
                AnnotationConceptRow.annotation_id == annotation.id
            )
        )
    ).all()
    projected = 0
    for relation in relations:
        concept = await session.get(ConceptRow, relation.concept_id)
        if concept is None:
            continue
        await graph.project_annotation_concept(
            session=session,
            annotation_id=annotation.id,
            annotation_type=annotation.type,
            concept_id=concept.id,
            concept_name=concept.name,
            relation=relation.relation,
            provenance=relation.provenance,
            status=relation.status,
        )
        projected += 1
    return projected


async def run_batch(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    graph: AgeGraph,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    stats = {"claimed": 0, "projected": 0, "failed": 0}
    async with sessions() as session:
        claimed = await store.claim(
            session=session,
            consumer=CONSUMER_GRAPH,
            batch_size=batch_size,
            event_types=PROJECTED_EVENT_TYPES,
        )
        stats["claimed"] = len(claimed)
        if not claimed:
            await session.commit()
            return stats

        projected_ids: list[UUID] = []
        for event in claimed:
            try:
                await project_event(session=session, graph=graph, event=event)
            except Exception as exc:  # noqa: BLE001 - registra e reprocessa com backoff
                stats["failed"] += 1
                await store.mark_failed(
                    session=session, event_id=event.id, consumer=CONSUMER_GRAPH, error=str(exc)
                )
                continue
            projected_ids.append(event.id)

        for event_id in projected_ids:
            await store.mark_processed(session=session, event_id=event_id, consumer=CONSUMER_GRAPH)
        stats["projected"] = len(projected_ids)
        await session.commit()
    return stats


async def rebuild(*, sessions: async_sessionmaker[AsyncSession], graph: AgeGraph) -> int:
    """Reconstrói o grafo inteiro a partir do PostgreSQL.

    Documentos, anotações, conceitos, relações anotação → conceito, afirmações e as quatro
    arestas semânticas. É a reconciliação de referência do AGE: tudo o que o grafo mostra
    tem um registro de origem aqui.
    """
    async with sessions() as session:
        await graph.truncate(session=session)
        projected = 0
        documents = (
            await session.scalars(select(DocumentRow).order_by(DocumentRow.created_at, DocumentRow.id))
        ).all()
        for document in documents:
            await graph.project_document(
                session=session, document_id=document.id, title=document.title
            )
            projected += 1
        annotations = (
            await session.scalars(
                select(AnnotationRow)
                .where(AnnotationRow.deleted_at.is_(None))
                .order_by(AnnotationRow.created_at, AnnotationRow.id)
            )
        ).all()
        for annotation in annotations:
            document = await session.get(DocumentRow, annotation.document_id)
            if document is None:
                continue
            await graph.project_annotation(
                session=session,
                annotation_id=annotation.id,
                document_id=document.id,
                annotation_type=annotation.type,
                page_number=annotation.page_number,
                document_title=document.title,
            )
            projected += 1
        # Conceitos e relações anotação → conceito, reconstruídos do PostgreSQL.
        concepts = (
            await session.scalars(select(ConceptRow).order_by(ConceptRow.normalized_name))
        ).all()
        for concept in concepts:
            await graph.project_concept(
                session=session, concept_id=concept.id, name=concept.name
            )
            projected += 1
        relations = (
            await session.scalars(
                select(AnnotationConceptRow).order_by(AnnotationConceptRow.created_at)
            )
        ).all()
        for relation in relations:
            annotation = await session.get(AnnotationRow, relation.annotation_id)
            concept = await session.get(ConceptRow, relation.concept_id)
            if annotation is None or annotation.deleted_at is not None or concept is None:
                continue
            await graph.project_annotation_concept(
                session=session,
                annotation_id=annotation.id,
                annotation_type=annotation.type,
                concept_id=concept.id,
                concept_name=concept.name,
                relation=relation.relation,
                provenance=relation.provenance,
                status=relation.status,
            )
            projected += 1
        # Afirmações autorais e as quatro arestas semânticas. Afirmação removida
        # (`deleted_at`) não volta: o grafo reproduz o que o PostgreSQL mostra ao usuário.
        claims = (
            await session.scalars(
                select(ClaimRow)
                .where(ClaimRow.deleted_at.is_(None))
                .order_by(ClaimRow.created_at, ClaimRow.id)
            )
        ).all()
        for claim in claims:
            await project_claim(session=session, graph=graph, claim=claim)
            projected += 1
        claim_relations = (
            await session.scalars(
                select(ClaimRelationRow).order_by(
                    ClaimRelationRow.created_at, ClaimRelationRow.id
                )
            )
        ).all()
        for relation in claim_relations:
            await project_claim_relation(session=session, graph=graph, relation=relation)
            projected += 1
        await session.commit()
        return projected


async def run_forever(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    graph: AgeGraph,
    batch_size: int = DEFAULT_BATCH_SIZE,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        stats = await run_batch(
            store=store, sessions=sessions, graph=graph, batch_size=batch_size
        )
        if stats["claimed"]:
            logger.info("batch processed %s", stats)
        else:
            await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Project domain data into the Apache AGE graph")
    parser.add_argument("--once", action="store_true", help="process a single batch and exit")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the graph from PostgreSQL and exit")
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
        engine = create_async_engine(arguments.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyOutboxStore(sessions)
        graph = AgeGraph()
        try:
            if arguments.rebuild:
                projected = await rebuild(sessions=sessions, graph=graph)
                logger.info("graph rebuilt with %s projections", projected)
                return
            if arguments.once:
                stats = await run_batch(
                    store=store, sessions=sessions, graph=graph, batch_size=arguments.batch_size
                )
                logger.info("batch processed %s", stats)
                return
            await run_forever(
                store=store,
                sessions=sessions,
                graph=graph,
                batch_size=arguments.batch_size,
                interval_seconds=arguments.interval,
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
