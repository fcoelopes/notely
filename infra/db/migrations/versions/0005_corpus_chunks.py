"""0005 corpus chunks.

Revision ID: 0005
Revises: 0004
"""

from migration_sql import execute_sql

revision = "0005"
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0005_corpus_chunks.up.sql")


def downgrade() -> None:
    execute_sql("0005_corpus_chunks.down.sql")
