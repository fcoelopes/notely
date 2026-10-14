"""0009 outbox deliveries.

Revision ID: 0009
Revises: 0008

Cria a entrega do outbox por consumidor, permitindo que mais de um worker projete o
mesmo evento com retry e backoff independentes.
"""

from migration_sql import execute_sql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0009_outbox_deliveries.up.sql")


def downgrade() -> None:
    execute_sql("0009_outbox_deliveries.down.sql")
