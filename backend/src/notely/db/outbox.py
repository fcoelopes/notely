from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notely.core.models import utc_now
from notely.db.models import OutboxEventRow

DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 900


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
    """Claim e baixa de eventos do outbox, com backoff e estado terminal por tentativas.

    O claim usa ``FOR UPDATE SKIP LOCKED`` e só é efetivado no commit da transação:
    se o processo cair no meio, o evento volta a ficar disponível para outro worker.
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
        batch_size: int = DEFAULT_BATCH_SIZE,
        event_types: Collection[str] | None = None,
    ) -> list[ClaimedEvent]:
        """Reivindica eventos pendentes.

        ``event_types`` restringe o claim aos tipos que o consumidor possui: eventos de
        outros consumidores ficam pendentes para quem é responsável por eles, em vez de
        ocuparem o lote deste worker para sempre.
        """
        rows = (
            await session.scalars(
                select(OutboxEventRow)
                .where(
                    OutboxEventRow.processed_at.is_(None),
                    OutboxEventRow.available_at <= utc_now(),
                    OutboxEventRow.attempt_count < self.max_attempts,
                    *(
                        (OutboxEventRow.event_type.in_(list(event_types)),)
                        if event_types is not None
                        else ()
                    ),
                )
                .order_by(OutboxEventRow.available_at, OutboxEventRow.created_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claimed_at = utc_now()
        for row in rows:
            row.claimed_at = claimed_at
        await session.flush()
        return [
            ClaimedEvent(
                id=row.id,
                aggregate_type=row.aggregate_type,
                aggregate_id=row.aggregate_id,
                event_type=row.event_type,
                payload=row.payload,
                attempt_count=row.attempt_count,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def mark_processed(self, *, session: AsyncSession, event_id: UUID) -> None:
        row = await session.get(OutboxEventRow, event_id)
        if row is None:
            return
        row.processed_at = utc_now()
        row.last_error = None

    async def mark_failed(
        self, *, session: AsyncSession, event_id: UUID, error: str
    ) -> None:
        row = await session.get(OutboxEventRow, event_id)
        if row is None:
            return
        row.attempt_count += 1
        row.last_error = error[:2000]
        row.claimed_at = None
        row.available_at = utc_now() + timedelta(
            seconds=self._backoff_seconds(row.attempt_count)
        )

    def _backoff_seconds(self, attempt_count: int) -> float:
        exponential = self.backoff_seconds * (2 ** max(attempt_count - 1, 0))
        return min(exponential, MAX_BACKOFF_SECONDS)

    async def session(self) -> AsyncSession:
        return self._session_factory()
