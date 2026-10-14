"""0004 document corpus.

Revision ID: 0004
Revises: 0003
"""

from migration_sql import execute_sql

revision = "0004"
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0004_document_corpus.up.sql")


def downgrade() -> None:
    execute_sql("0004_document_corpus.down.sql")
