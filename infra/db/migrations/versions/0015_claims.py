"""0015 claims.

Revision ID: 0015
Revises: 0014

Cria as tabelas de afirmações e de arestas semânticas, e os rótulos correspondentes no grafo.
"""

from alembic import op

from migration_sql import execute_sql
from rollback_policy import protect_downgrade

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

GRAPH_NAME = "notely"
VERTEX_LABELS = ("Claim",)
EDGE_LABELS = ("SUPPORTS", "CONTRADICTS", "ANSWERED_BY", "RELATED_TO")


def upgrade() -> None:
    execute_sql("0015_claims.up.sql")
    # Labels do AGE não são transacionais, como no 0008 e no 0010; o `search_path` é
    # restaurado ao final porque é estado de sessão.
    with op.get_context().autocommit_block():
        op.execute("LOAD 'age'")
        op.execute('SET search_path = ag_catalog, "$user", public')
        for label in VERTEX_LABELS:
            op.execute(f"SELECT create_vlabel('{GRAPH_NAME}', '{label}')")
        for label in EDGE_LABELS:
            op.execute(f"SELECT create_elabel('{GRAPH_NAME}', '{label}')")
        op.execute("RESET search_path")


def downgrade() -> None:
    # A guarda roda antes de qualquer passo destrutivo: os labels seriam removidos antes das
    # tabelas, e uma recusa depois disso deixaria o grafo sem os labels.
    protect_downgrade(revision)
    with op.get_context().autocommit_block():
        op.execute("LOAD 'age'")
        op.execute('SET search_path = ag_catalog, "$user", public')
        # O `drop_label` do AGE 1.7 não aceita `force`, então a projeção é limpa antes.
        # O grafo é derivado e reconstruível a partir do PostgreSQL.
        op.execute(
            f"SELECT * FROM cypher('{GRAPH_NAME}', "
            f"$notely$ MATCH (c:{VERTEX_LABELS[0]}) DETACH DELETE c $notely$) AS (r agtype)"
        )
        for label in (*EDGE_LABELS, *VERTEX_LABELS):
            op.execute(f"SELECT drop_label('{GRAPH_NAME}', '{label}')")
        op.execute("RESET search_path")
    execute_sql("0015_claims.down.sql")
