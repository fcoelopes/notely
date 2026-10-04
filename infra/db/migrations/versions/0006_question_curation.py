"""0006 question curation.

Revision ID: 0006
Revises: 0005
"""

from migration_sql import execute_sql

revision = "0006"
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0006_question_curation.up.sql")


def downgrade() -> None:
    execute_sql("0006_question_curation.down.sql")
