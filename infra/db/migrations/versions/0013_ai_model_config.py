"""0013 ai model config.

Revision ID: 0013
Revises: 0012

Guarda a configuração de modelo do usuário (BYOK) com a chave cifrada em repouso.
"""

from migration_sql import execute_sql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql("0013_ai_model_config.up.sql")


def downgrade() -> None:
    execute_sql("0013_ai_model_config.down.sql")
