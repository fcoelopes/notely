"""0008 age graph projection.

Revision ID: 0008
Revises: 0007

Cria a extensão Apache AGE e o grafo `notely` com os labels iniciais da fase A
(Document, Annotation, Question, HAS_ANNOTATION). O DDL do AGE (create_graph,
create_vlabel, create_elabel) não roda dentro de bloco de transação, então toda
a revisão executa em autocommit.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

GRAPH_NAME = "notely"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS age")
        op.execute("LOAD 'age'")
        op.execute('SET search_path = ag_catalog, "$user", public')
        op.execute(f"SELECT create_graph('{GRAPH_NAME}')")
        op.execute(f"SELECT create_vlabel('{GRAPH_NAME}', 'Document')")
        op.execute(f"SELECT create_vlabel('{GRAPH_NAME}', 'Annotation')")
        op.execute(f"SELECT create_vlabel('{GRAPH_NAME}', 'Question')")
        op.execute(f"SELECT create_elabel('{GRAPH_NAME}', 'HAS_ANNOTATION')")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("LOAD 'age'")
        op.execute('SET search_path = ag_catalog, "$user", public')
        op.execute(f"SELECT drop_graph('{GRAPH_NAME}', true)")
        op.execute("DROP EXTENSION IF EXISTS age")
