"""0011 identity.

Revision ID: 0011
Revises: 0010

Introduz contas de usuário e dono por registro. A senha e o MFA são definidos pelo
comando de bootstrap; a migration apenas garante o dono dos dados existentes.
"""

from migration_sql import execute_sql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0011_identity.up.sql")


def downgrade() -> None:
    execute_sql("0011_identity.down.sql")
