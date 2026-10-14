"""Progresso de páginas visualizadas; não mede compreensão ou tempo de estudo."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PageProgress:
    viewed_pages: int
    total_pages: int
    percent: int

    @classmethod
    def from_counts(cls, viewed_pages: int, total_pages: int) -> PageProgress:
        if viewed_pages < 0 or total_pages < 0:
            raise ValueError("page counts cannot be negative")
        covered = min(viewed_pages, total_pages)
        percent = (covered * 100 + total_pages // 2) // total_pages if total_pages else 0
        return cls(covered, total_pages, percent)


@dataclass(frozen=True, slots=True)
class SessionProgress:
    progress: PageProgress
    documents: dict[UUID, PageProgress]


@dataclass(frozen=True, slots=True)
class ReadingProgressSnapshot:
    documents: dict[UUID, PageProgress]
    sessions: dict[UUID, SessionProgress]


@dataclass(frozen=True, slots=True)
class ReadingProgressUpdate:
    study_session_id: UUID
    document_id: UUID
    session: SessionProgress
    document_global: PageProgress


def build_progress_snapshot(
    session_ids: list[UUID],
    session_counts: list[tuple[UUID, UUID, int, int]],
    global_counts: list[tuple[UUID, int, int]],
) -> ReadingProgressSnapshot:
    grouped: dict[UUID, dict[UUID, PageProgress]] = {sid: {} for sid in session_ids}
    for session_id, document_id, total, viewed in session_counts:
        grouped.setdefault(session_id, {})[document_id] = PageProgress.from_counts(viewed, total)
    sessions = {
        sid: SessionProgress(
            progress=PageProgress.from_counts(
                sum(item.viewed_pages for item in documents.values()),
                sum(item.total_pages for item in documents.values()),
            ),
            documents=documents,
        )
        for sid, documents in grouped.items()
    }
    documents = {
        document_id: PageProgress.from_counts(viewed, total)
        for document_id, total, viewed in global_counts
    }
    return ReadingProgressSnapshot(documents=documents, sessions=sessions)
