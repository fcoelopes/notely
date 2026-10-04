"""Regra de cobertura de páginas visualizadas."""

from uuid import uuid4

from notely.core.progress import PageProgress, build_progress_snapshot


def test_progress_uses_distinct_page_counts_and_rounds_to_nearest_percent() -> None:
    assert PageProgress.from_counts(2, 4) == PageProgress(2, 4, 50)
    assert PageProgress.from_counts(1, 6) == PageProgress(1, 6, 17)
    assert PageProgress.from_counts(0, 0) == PageProgress(0, 0, 0)
    assert PageProgress.from_counts(5, 4) == PageProgress(4, 4, 100)


def test_session_progress_is_weighted_by_pages_and_empty_session_is_zero() -> None:
    session_id, empty_id, first_id, second_id = (uuid4() for _ in range(4))
    snapshot = build_progress_snapshot(
        [session_id, empty_id],
        [(session_id, first_id, 4, 2), (session_id, second_id, 6, 3)],
        [(first_id, 4, 2), (second_id, 6, 3)],
    )
    assert snapshot.sessions[session_id].progress == PageProgress(5, 10, 50)
    assert snapshot.sessions[empty_id].progress == PageProgress(0, 0, 0)
    assert snapshot.documents[first_id] == PageProgress(2, 4, 50)
