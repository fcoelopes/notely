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
    # O grafo AGE é projeção derivada e reconstruível a partir do PostgreSQL;
    # drop_graph no downgrade pode descartá-lo sem perda de dado primário.
    "0008": ((), ()),
    # O estado de entrega é derivado do log de eventos, que permanece intacto; o código
    # anterior volta a decidir por outbox_events.processed_at.
    "0009": ((), ()),
    # Relação apenas sugerida é reconstruível a partir da anotação; relação aceita pelo
    # usuário é dado autoral e não pode ser descartada por um downgrade.
    "0010": (("concepts", "annotation_concepts"), (
        ("accepted concept relations", "SELECT EXISTS (SELECT 1 FROM annotation_concepts WHERE status = 'accepted')"),
    )),
    # Descartar a identidade com contas reais deixaria o usuário sem acesso ao próprio
    # acervo; o dono provisório sem senha não conta como conta.
    "0011": (("users", "auth_sessions", "recovery_codes"), (
        ("user accounts", "SELECT EXISTS (SELECT 1 FROM users WHERE password_hash IS NOT NULL)"),
        ("sessions", "SELECT EXISTS (SELECT 1 FROM auth_sessions)"),
        ("recovery codes", "SELECT EXISTS (SELECT 1 FROM recovery_codes)"),
    )),
    # Remover o default de dono não descarta dado algum: apenas volta a exigir o dono em
    # cada escrita.
    "0012": ((), ()),
    # A configuração de modelo é recadastrável pelo usuário; não há dado autoral aqui.
    "0013": ((), ()),
    "0014": (("document_bookmarks",), (
        ("document bookmarks", "SELECT EXISTS (SELECT 1 FROM document_bookmarks)"),
    )),
    # Afirmação e aresta aceitas são conhecimento do usuário: não são reconstruíveis a partir
    # de nada, então um downgrade não pode descartá-las. O critério é o aceite, não a origem —
    # uma sugestão de modelo que o usuário aceitou também é conhecimento dele. Sugestão não
    # aceita é inferência e sai com a tabela.
    "0015": (("claims", "claim_relations"), (
        ("accepted claims", "SELECT EXISTS (SELECT 1 FROM claims WHERE status = 'accepted')"),
        ("accepted relations", "SELECT EXISTS (SELECT 1 FROM claim_relations WHERE status = 'accepted')"),
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
