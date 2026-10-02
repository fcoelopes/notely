from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from notely.db.models import ReaderEventRow


@dataclass(frozen=True, slots=True)
class ReaderEvent:
    event_id: UUID
    event_type: str
    time: datetime
    document_id: UUID
    session_id: UUID | None = None
    annotation_id: UUID | None = None
    annotation_type: str | None = None
    passage_id: str | None = None
    passage_id_version: int | None = None
    page_number: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


async def insert_reader_events(*, session: AsyncSession, events: list[ReaderEvent]) -> int:
    """Projeta eventos na trilha temporal de forma idempotente.

    ``reader_events`` tem chave única em (event_id, time); reprocessar o mesmo evento do
    outbox não cria linha nova. Retorna quantas linhas foram realmente inseridas.
    """
    if not events:
        return 0
    inserted = 0
    for event in events:
        statement = (
            insert(ReaderEventRow)
            .values(
                time=event.time,
                event_id=event.event_id,
                event_type=event.event_type,
                session_id=event.session_id,
                document_id=event.document_id,
                annotation_id=event.annotation_id,
                annotation_type=event.annotation_type,
                passage_id=event.passage_id,
                passage_id_version=event.passage_id_version,
                page_number=event.page_number,
                metadata_json=event.metadata,
            )
            .on_conflict_do_nothing(index_elements=["event_id", "time"])
            .returning(ReaderEventRow.event_id)
        )
        result = await session.execute(statement)
        inserted += len(result.scalars().all())
    await session.flush()
    return inserted
