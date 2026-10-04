"""0003 reading sessions and reader events.

Revision ID: 0003
Revises: 0002
"""

from migration_sql import execute_sql

revision = "0003"
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0003_reading_sessions_and_reader_events.up.sql")


def downgrade() -> None:
    execute_sql("0003_reading_sessions_and_reader_events.down.sql")
