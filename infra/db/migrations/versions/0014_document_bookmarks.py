"""0014 document bookmarks.

Revision ID: 0014
Revises: 0013

Guarda a página marcada manualmente por documento e usuário.
"""

from migration_sql import execute_sql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0014_document_bookmarks.up.sql")


def downgrade() -> None:
    execute_sql("0014_document_bookmarks.down.sql")
