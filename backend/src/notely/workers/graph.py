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
from notely.db.models import AnnotationRow, DocumentRow
from notely.db.outbox import DEFAULT_BATCH_SIZE, ClaimedEvent, SqlAlchemyOutboxStore

logger = logging.getLogger("notely.workers.graph")

PROJECTED_EVENT_TYPES = frozenset({"document.created", "annotation.created"})

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

    # Não reivindicamos outros tipos; se chegar aqui, nada a projetar.
    logger.warning("unprojectable event type %s ignored", event.event_type)


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
            session=session, batch_size=batch_size, event_types=PROJECTED_EVENT_TYPES
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
                await store.mark_failed(session=session, event_id=event.id, error=str(exc))
                continue
            projected_ids.append(event.id)

        for event_id in projected_ids:
            await store.mark_processed(session=session, event_id=event_id)
        stats["projected"] = len(projected_ids)
        await session.commit()
    return stats


async def rebuild(*, sessions: async_sessionmaker[AsyncSession], graph: AgeGraph) -> int:
    """Reconstrói o grafo inteiro a partir do PostgreSQL (documentos + anotações)."""
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
        if stats["claimed"] == 0:
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
