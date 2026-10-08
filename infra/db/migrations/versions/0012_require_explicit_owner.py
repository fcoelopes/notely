"""0012 require explicit owner.

Revision ID: 0012
Revises: 0011

Remove o default transitório de `user_id`: toda escrita passa a informar o dono, e um
esquecimento falha em vez de atribuir o registro ao dono provisório em silêncio.
"""

from migration_sql import execute_sql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0012_require_explicit_owner.up.sql")


def downgrade() -> None:
    execute_sql("0012_require_explicit_owner.down.sql")
