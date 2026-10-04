"""Data that historical downgrades must not discard."""

# revision: (tables to lock, (description, EXISTS query) checks)
DOWNGRADE_GUARDS: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "0001": (("documents", "annotations", "outbox_events"), (
        ("documents", "SELECT EXISTS (SELECT 1 FROM documents)"),
        ("annotations", "SELECT EXISTS (SELECT 1 FROM annotations)"),
        ("outbox events", "SELECT EXISTS (SELECT 1 FROM outbox_events)"),
    )),
    "0002": (("study_sessions", "study_session_documents", "ai_suggestions", "outbox_events"), (
        ("study sessions", "SELECT EXISTS (SELECT 1 FROM study_sessions)"),
        ("session documents", "SELECT EXISTS (SELECT 1 FROM study_session_documents)"),
        ("AI suggestions", "SELECT EXISTS (SELECT 1 FROM ai_suggestions)"),
        ("outbox event keys", "SELECT EXISTS (SELECT 1 FROM outbox_events WHERE event_key IS NOT NULL)"),
    )),
    "0003": (("reading_sessions", "reader_events", "annotations"), (
        ("reading sessions", "SELECT EXISTS (SELECT 1 FROM reading_sessions)"),
        ("reader events", "SELECT EXISTS (SELECT 1 FROM reader_events)"),
        ("annotation passage identities", "SELECT EXISTS (SELECT 1 FROM annotations)"),
    )),
    # Corpus tables are derived from PDFs and can be rebuilt after rollback.
    "0004": ((), ()),
    "0005": ((), ()),
    "0006": (("annotations", "question_curation_requests", "question_curated_sources"), (
        ("annotation study sessions", "SELECT EXISTS (SELECT 1 FROM annotations WHERE study_session_id IS NOT NULL)"),
        ("question curation requests", "SELECT EXISTS (SELECT 1 FROM question_curation_requests)"),
        ("curated sources", "SELECT EXISTS (SELECT 1 FROM question_curated_sources)"),
    )),
    "0007": (("study_session_viewed_pages",), (
        ("reading progress", "SELECT EXISTS (SELECT 1 FROM study_session_viewed_pages)"),
    )),
}


def protect_downgrade(revision: str) -> None:
    """Reject unreviewed or data-losing downgrades within Alembic's transaction."""
    from alembic import op

    if revision not in DOWNGRADE_GUARDS:
        raise RuntimeError(f"Downgrade {revision} has no rollback policy")
    tables, checks = DOWNGRADE_GUARDS[revision]
    if not tables:
        return
    connection = op.get_bind()
    connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
    connection.exec_driver_sql("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE")
    blocked = [label for label, query in checks if connection.exec_driver_sql(query).scalar()]
    if blocked:
        raise RuntimeError(
            f"Downgrade {revision} would discard: {', '.join(blocked)}; restore a backup into a separate database if recovery is needed"
        )
