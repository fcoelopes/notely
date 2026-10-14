"""0007 reading progress.

Revision ID: 0007
Revises: 0006
"""

from migration_sql import execute_sql

revision = "0007"
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0007_reading_progress.up.sql")


def downgrade() -> None:
    execute_sql("0007_reading_progress.down.sql")
