from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Text, and_, exists, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notely.core.models import utc_now
from notely.db.models import OutboxDeliveryRow, OutboxEventRow

DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 900

# Nomes estáveis dos consumidores do outbox. A migration 0009 adota o histórico para
# todos eles; a migration que introduzir um consumidor novo precisa fazer o mesmo, ou o
# consumidor reprojetaria todo o acervo. Ver docs/adr/0004.
CONSUMER_TIMESCALE = "timescale"
CONSUMER_CORPUS = "corpus"
CONSUMER_CURATION = "curation"
CONSUMER_GRAPH = "graph"
CONSUMERS = (CONSUMER_TIMESCALE, CONSUMER_CORPUS, CONSUMER_CURATION, CONSUMER_GRAPH)


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, object]
    attempt_count: int
    created_at: datetime


class SqlAlchemyOutboxStore:
    """Claim e baixa de eventos do outbox, com entrega independente por consumidor.

    A decisão de entrega vive em ``outbox_deliveries``, não em ``outbox_events``: mais de
    um consumidor projeta o mesmo evento, e o retry/backoff de um não interfere no outro.
    O claim materializa a entrega pendente do consumidor e trava apenas as linhas de
    entrega com ``FOR UPDATE SKIP LOCKED``; se o processo cair no meio, a entrega volta a
    ficar disponível para outro worker.

    Um consumidor novo precisa que a migration que o introduz marque os eventos
    anteriores como já entregues — caso contrário ele reprojetaria todo o acervo. Ver
    ``docs/adr/0004``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds

    async def claim(
        self,
        *,
        session: AsyncSession,
        consumer: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        event_types: Collection[str] | None = None,
    ) -> list[ClaimedEvent]:
        """Reivindica as entregas pendentes deste consumidor.

        ``event_types`` restringe o claim aos tipos que o consumidor projeta, para que os
        eventos de outros consumidores não ocupem o lote.
        """
        await self._materialize(session=session, consumer=consumer, event_types=event_types)

        statement = (
            select(OutboxEventRow, OutboxDeliveryRow)
            .join(
                OutboxDeliveryRow,
                and_(
                    OutboxDeliveryRow.event_id == OutboxEventRow.id,
                    OutboxDeliveryRow.consumer == consumer,
                ),
            )
            .where(
                OutboxDeliveryRow.processed_at.is_(None),
                OutboxDeliveryRow.available_at <= utc_now(),
                OutboxDeliveryRow.attempt_count < self.max_attempts,
            )
            .order_by(
                OutboxDeliveryRow.available_at,
                OutboxEventRow.created_at,
                OutboxEventRow.id,
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=OutboxDeliveryRow)
        )
        if event_types is not None:
            statement = statement.where(OutboxEventRow.event_type.in_(list(event_types)))

        rows = (await session.execute(statement)).all()
        claimed_at = utc_now()
        claimed: list[ClaimedEvent] = []
        for event, delivery in rows:
            delivery.claimed_at = claimed_at
            claimed.append(
                ClaimedEvent(
                    id=event.id,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    attempt_count=delivery.attempt_count,
                    created_at=event.created_at,
                )
            )
        await session.flush()
        return claimed

    async def mark_processed(
        self, *, session: AsyncSession, event_id: UUID, consumer: str
    ) -> None:
        delivery = await session.get(OutboxDeliveryRow, (event_id, consumer))
        if delivery is None:
            return
        delivery.processed_at = utc_now()
        delivery.last_error = None

    async def mark_failed(
        self, *, session: AsyncSession, event_id: UUID, consumer: str, error: str
    ) -> None:
        delivery = await session.get(OutboxDeliveryRow, (event_id, consumer))
        if delivery is None:
            return
        delivery.attempt_count += 1
        delivery.last_error = error[:2000]
        delivery.claimed_at = None
        delivery.available_at = utc_now() + timedelta(
            seconds=self._backoff_seconds(delivery.attempt_count)
        )

    async def _materialize(
        self,
        *,
        session: AsyncSession,
        consumer: str,
        event_types: Collection[str] | None,
    ) -> None:
        """Cria a entrega pendente deste consumidor para eventos que ele ainda não viu."""
        already_known = exists().where(
            and_(
                OutboxDeliveryRow.event_id == OutboxEventRow.id,
                OutboxDeliveryRow.consumer == consumer,
            )
        )
        source = select(
            OutboxEventRow.id,
            literal(consumer, type_=Text),
            OutboxEventRow.available_at,
        ).where(~already_known)
        if event_types is not None:
            source = source.where(OutboxEventRow.event_type.in_(list(event_types)))
        await session.execute(
            insert(OutboxDeliveryRow)
            .from_select(["event_id", "consumer", "available_at"], source)
            .on_conflict_do_nothing(index_elements=["event_id", "consumer"])
        )

    def _backoff_seconds(self, attempt_count: int) -> float:
        exponential = self.backoff_seconds * (2 ** max(attempt_count - 1, 0))
        return min(exponential, MAX_BACKOFF_SECONDS)

    async def session(self) -> AsyncSession:
        return self._session_factory()
