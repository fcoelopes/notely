from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from notely.db.models import AnnotationRow
from notely.db.outbox import DEFAULT_BATCH_SIZE, ClaimedEvent, SqlAlchemyOutboxStore
from notely.db.reader_events import ReaderEvent, insert_reader_events

logger = logging.getLogger("notely.workers.timescale")

PROJECTED_EVENT_TYPES = frozenset(
    {
        "reading.started",
        "reading.ended",
        "annotation.created",
        "annotation.updated",
    }
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://notely:notely@localhost:5432/notely"


class UnprojectableEventError(Exception):
    """O payload do evento não permite construir uma linha temporal."""


def project_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    event_id: UUID,
    fallback_time: datetime | None = None,
) -> ReaderEvent:
    """Traduz um evento de domínio do outbox em uma linha de reader_events.

    ``fallback_time`` cobre eventos gravados antes desta entrega, quando o payload ainda
    não carregava ``occurred_at``: nesses casos o timestamp do próprio outbox é a hora do
    fato, porque o evento é escrito na mesma transação da mudança de domínio.
    """
    document_id = _uuid(payload, "document_id")
    occurred_at = _timestamp(payload, "occurred_at", fallback=fallback_time)

    if event_type == "reading.started":
        return ReaderEvent(
            event_id=event_id,
            event_type=event_type,
            time=occurred_at,
            document_id=document_id,
            session_id=_uuid(payload, "reading_session_id", required=False),
            page_number=_int(payload, "page_number"),
            metadata={"filename_snapshot": payload.get("filename_snapshot")},
        )

    if event_type == "reading.ended":
        return ReaderEvent(
            event_id=event_id,
            event_type=event_type,
            time=occurred_at,
            document_id=document_id,
            session_id=_uuid(payload, "reading_session_id", required=False),
            page_number=_int(payload, "end_page"),
            metadata={"started_at": payload.get("started_at")},
        )

    return ReaderEvent(
        event_id=event_id,
        event_type=event_type,
        time=occurred_at,
        document_id=document_id,
        session_id=_uuid(payload, "reading_session_id", required=False),
        annotation_id=_uuid(payload, "annotation_id", required=False),
        annotation_type=_str(payload, "annotation_type"),
        passage_id=_str(payload, "passage_id"),
        passage_id_version=_int(payload, "passage_id_version"),
        page_number=_int(payload, "page_number"),
        metadata={"source": payload.get("source"), "author_type": payload.get("author_type")},
    )


def _uuid(payload: dict[str, Any], key: str, *, required: bool = True) -> UUID | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise UnprojectableEventError(f"payload has no {key}")
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise UnprojectableEventError(f"payload {key} is not a uuid") from exc


def _timestamp(
    payload: dict[str, Any], key: str, *, fallback: datetime | None = None
) -> datetime:
    value = payload.get(key)
    if value is None:
        if fallback is not None:
            return fallback
        raise UnprojectableEventError(f"payload has no {key}")
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise UnprojectableEventError(f"payload {key} is not an ISO timestamp") from exc


def _int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return None if value is None else int(value)


def _str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return None if value is None else str(value)


async def run_batch(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """Processa um lote do outbox. Idempotente: reprocessar não duplica a trilha."""
    stats = {"claimed": 0, "projected": 0, "failed": 0}
    async with sessions() as session:
        claimed = await store.claim(
            session=session,
            batch_size=batch_size,
            event_types=PROJECTED_EVENT_TYPES,
        )
        stats["claimed"] = len(claimed)
        if not claimed:
            await session.commit()
            return stats

        reader_events: list[ReaderEvent] = []
        projected_ids: list[UUID] = []
        for event in claimed:
            try:
                payload = await _complete_payload(session=session, event=event)
                reader_events.append(
                    project_event(
                        event.event_type,
                        payload,
                        event_id=event.id,
                        fallback_time=event.created_at,
                    )
                )
            except UnprojectableEventError as exc:
                # Falha de projeção não é sucesso: o evento fica pendente para retry com
                # backoff, com o motivo registrado em last_error.
                stats["failed"] += 1
                await store.mark_failed(session=session, event_id=event.id, error=str(exc))
                continue
            projected_ids.append(event.id)

        stats["projected"] = await insert_reader_events(session=session, events=reader_events)
        for event_id in projected_ids:
            await store.mark_processed(session=session, event_id=event_id)

        await session.commit()
    return stats


async def run_forever(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        stats = await run_batch(store=store, sessions=sessions, batch_size=batch_size)
        if stats["claimed"] == 0:
            await asyncio.sleep(interval_seconds)


async def _complete_payload(*, session: AsyncSession, event: ClaimedEvent) -> dict[str, Any]:
    """Completa payloads gravados antes desta entrega.

    Eventos de annotation antigos não carregam identidade do trecho. Como a Annotation é a
    fonte de verdade, o worker lê o registro para projetar a trilha completa em vez de
    descartar o evento.
    """
    payload = dict(event.payload)
    if payload.get("occurred_at") and payload.get("passage_id"):
        return payload
    if not event.event_type.startswith("annotation."):
        return payload

    annotation_id = payload.get("annotation_id")
    if annotation_id is None:
        return payload
    try:
        parsed_id = UUID(str(annotation_id))
    except ValueError:
        return payload

    row = await session.scalar(select(AnnotationRow).where(AnnotationRow.id == parsed_id))
    if row is None:
        return payload

    payload.setdefault("document_id", str(row.document_id))
    payload.setdefault("annotation_type", row.type)
    payload.setdefault("page_number", row.page_number)
    payload.setdefault("passage_id", row.passage_id)
    payload.setdefault("passage_id_version", row.passage_id_version)
    payload["reading_session_id"] = payload.get(
        "reading_session_id", str(row.reading_session_id) if row.reading_session_id else None
    )
    payload["occurred_at"] = row.created_at.isoformat()
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Project the Notely reading trail into TimescaleDB")
    parser.add_argument("--once", action="store_true", help="process a single batch and exit")
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
        try:
            if arguments.once:
                stats = await run_batch(
                    store=store, sessions=sessions, batch_size=arguments.batch_size
                )
                logger.info("batch processed %s", stats)
                return
            await run_forever(
                store=store,
                sessions=sessions,
                batch_size=arguments.batch_size,
                interval_seconds=arguments.interval,
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
