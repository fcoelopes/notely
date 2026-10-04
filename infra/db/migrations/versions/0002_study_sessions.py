"""0002 study sessions.

Revision ID: 0002
Revises: 0001
"""

from migration_sql import execute_sql

revision = "0002"
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0002_study_sessions.up.sql")


def downgrade() -> None:
    execute_sql("0002_study_sessions.down.sql")
