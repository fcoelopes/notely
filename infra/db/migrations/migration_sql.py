"""Run the original transactional SQL files inside Alembic's transaction."""

from pathlib import Path

from alembic import op
import sqlparse

SQL_DIR = Path(__file__).resolve().parent


def execute_sql(filename: str) -> None:
    statements = sqlparse.split((SQL_DIR / filename).read_text(encoding="utf-8"))
    if len(statements) < 2 or statements[0].strip().upper() != "BEGIN;" or statements[-1].strip().upper() != "COMMIT;":
        raise ValueError(f"Expected a transactional SQL migration: {filename}")
    connection = op.get_bind()
    for statement in statements[1:-1]:
        connection.exec_driver_sql(statement)
