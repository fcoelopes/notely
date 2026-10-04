from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from notely.db.outbox import MAX_BACKOFF_SECONDS, SqlAlchemyOutboxStore
from notely.workers.timescale import (
    PROJECTED_EVENT_TYPES,
    UnprojectableEventError,
    project_event,
)

OCCURRED_AT = datetime(2026, 10, 2, 9, 3, 12, tzinfo=UTC)


def test_reading_started_becomes_a_timeline_row() -> None:
    event_id = uuid4()
    document_id = uuid4()
    session_id = uuid4()

    event = project_event(
        "reading.started",
        {
            "reading_session_id": str(session_id),
            "document_id": str(document_id),
            "page_number": 12,
            "filename_snapshot": "ias-1.pdf",
            "occurred_at": OCCURRED_AT.isoformat(),
        },
        event_id=event_id,
    )

    assert event.event_id == event_id
    assert event.event_type == "reading.started"
    assert event.time == OCCURRED_AT
    assert event.document_id == document_id
    assert event.session_id == session_id
    assert event.page_number == 12
    # O nome do arquivo não é repetido na trilha: fica na sessão.
    assert "ias-1.pdf" in str(event.metadata["filename_snapshot"])


def test_reading_ended_carries_the_end_page() -> None:
    document_id = uuid4()
    event = project_event(
        "reading.ended",
        {
            "document_id": str(document_id),
            "end_page": 20,
            "started_at": (OCCURRED_AT - timedelta(minutes=30)).isoformat(),
            "occurred_at": OCCURRED_AT.isoformat(),
        },
        event_id=uuid4(),
    )

    assert event.session_id is None
    assert event.page_number == 20
    assert event.time == OCCURRED_AT


def test_annotation_created_carries_type_page_and_passage_identity() -> None:
    event = project_event(
        "annotation.created",
        {
            "annotation_id": str(uuid4()),
            "document_id": str(uuid4()),
            "reading_session_id": str(uuid4()),
            "page_number": 12,
            "annotation_type": "question",
            "passage_id": "a" * 64,
            "passage_id_version": 1,
            "source": "user_selection",
            "author_type": "user",
            "occurred_at": OCCURRED_AT.isoformat(),
        },
        event_id=uuid4(),
    )

    assert event.annotation_type == "question"
    assert event.passage_id == "a" * 64
    assert event.passage_id_version == 1
    assert event.page_number == 12
    assert event.metadata["author_type"] == "user"


def test_annotation_imported_without_session_keeps_session_empty() -> None:
    event = project_event(
        "annotation.updated",
        {
            "annotation_id": str(uuid4()),
            "document_id": str(uuid4()),
            "reading_session_id": None,
            "occurred_at": OCCURRED_AT.isoformat(),
        },
        event_id=uuid4(),
    )

    assert event.session_id is None
    assert event.annotation_id is not None


def test_project_event_rejects_payloads_without_required_fields() -> None:
    with pytest.raises(UnprojectableEventError, match="document_id"):
        project_event("annotation.created", {"occurred_at": OCCURRED_AT.isoformat()}, event_id=uuid4())

    with pytest.raises(UnprojectableEventError, match="occurred_at"):
        project_event("annotation.created", {"document_id": str(uuid4())}, event_id=uuid4())

    with pytest.raises(UnprojectableEventError, match="not a uuid"):
        project_event(
            "reading.started",
            {"document_id": "not-a-uuid", "occurred_at": OCCURRED_AT.isoformat()},
            event_id=uuid4(),
        )


def test_only_temporal_events_are_projected() -> None:
    assert PROJECTED_EVENT_TYPES == {
        "reading.started",
        "reading.ended",
        "annotation.created",
        "annotation.updated",
    }
    assert "study_session.created" not in PROJECTED_EVENT_TYPES


def test_backoff_grows_exponentially_and_is_capped() -> None:
    store = SqlAlchemyOutboxStore(session_factory=None, backoff_seconds=5)  # type: ignore[arg-type]

    assert store._backoff_seconds(1) == 5
    assert store._backoff_seconds(2) == 10
    assert store._backoff_seconds(3) == 20
    assert store._backoff_seconds(20) == MAX_BACKOFF_SECONDS
