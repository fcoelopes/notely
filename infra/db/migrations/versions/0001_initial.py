"""0001 initial.

Revision ID: 0001
Revises: base
"""

from migration_sql import execute_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0001_initial.up.sql")


def downgrade() -> None:
    execute_sql("0001_initial.down.sql")
