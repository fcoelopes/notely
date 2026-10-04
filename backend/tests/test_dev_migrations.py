"""Alembic upgrades fresh and legacy databases without altering partial schemas."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate.py"


def docker(*args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", *args],
        input=input, text=True, capture_output=True, cwd=ROOT, check=False,
    )


@pytest.fixture
def database():
    name = f"notely_migrate_test_{uuid4().hex[:12]}"
    try:
        created = docker("createdb", "-U", "notely", name)
    except FileNotFoundError:
        pytest.skip("Docker is unavailable")
    if created.returncode:
        pytest.skip(f"PostgreSQL is unavailable: {created.stderr.strip()}")
    try:
        yield name
    finally:
        dropped = docker("dropdb", "-U", "notely", name)
        assert dropped.returncode == 0, dropped.stderr


def migrate(name: str, backup_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--database", name, "--backup-dir", str(backup_dir)],
        text=True, capture_output=True, cwd=ROOT, check=False,
    )


def test_new_database_is_migrated_once(database: str, tmp_path: Path) -> None:
    first = migrate(database, tmp_path)
    assert first.returncode == 0, first.stderr
    assert "Alembic revision 0007: current" in first.stdout
    second = migrate(database, tmp_path)
    assert second.returncode == 0, second.stderr
    assert "Alembic revision 0007: current" in second.stdout
    assert list(tmp_path.glob("*.dump")) == []
    result = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                    "select version_num from alembic_version")
    assert result.stdout.strip() == "0007"


def test_existing_database_receives_only_missing_migrations(database: str, tmp_path: Path) -> None:
    for path in sorted((ROOT / "infra" / "db" / "migrations").glob("000[1-3]*.up.sql")):
        applied = docker("psql", "-v", "ON_ERROR_STOP=1", "-U", "notely", "-d", database,
                         input=path.read_text())
        assert applied.returncode == 0, applied.stderr
    document_id = str(uuid4())
    inserted = docker("psql", "-v", "ON_ERROR_STOP=1", "-U", "notely", "-d", database,
                      input=("INSERT INTO documents (id, sha256, title, filename, page_count, "
                             "storage_uri, created_at, updated_at) VALUES "
                             f"('{document_id}', '{'a' * 64}', 'Preserve me', 'test.pdf', 1, "
                             "'test://pdf', now(), now());"))
    assert inserted.returncode == 0, inserted.stderr
    result = migrate(database, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Legacy schema registered at Alembic revision 0003" in result.stdout
    assert "Alembic revision 0007: current" in result.stdout
    assert len(list(tmp_path.glob("*.dump"))) == 1
    version = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                     "select version_num from alembic_version")
    assert version.stdout.strip() == "0007"
    preserved = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                       f"select title from documents where id = '{document_id}'")
    assert preserved.stdout.strip() == "Preserve me"


def test_alembic_downgrade_and_upgrade(database: str, tmp_path: Path) -> None:
    assert migrate(database, tmp_path).returncode == 0
    environment = os.environ | {
        "NOTELY_DATABASE_URL": f"postgresql+asyncpg://notely:notely@localhost:5432/{database}"
    }
    downgraded = subprocess.run(
        [str(ROOT / "backend" / ".venv" / "bin" / "alembic"), "-c", "alembic.ini",
         "downgrade", "0006"],
        text=True, capture_output=True, cwd=ROOT, env=environment, check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    missing = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                     "select to_regclass('study_session_viewed_pages') is null")
    assert missing.stdout.strip() == "t"
    upgraded = migrate(database, tmp_path)
    assert upgraded.returncode == 0, upgraded.stderr
    assert len(list(tmp_path.glob("*.dump"))) == 1
    restored = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                      "select version_num from alembic_version")
    assert restored.stdout.strip() == "0007"


def test_partial_schema_is_not_modified(database: str, tmp_path: Path) -> None:
    created = docker("psql", "-U", "notely", "-d", database, "-c", "create table documents (id integer)")
    assert created.returncode == 0, created.stderr
    result = migrate(database, tmp_path)
    assert result.returncode == 1
    assert "partial schema" in result.stderr
    table = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                   "select to_regclass('annotations') is null")
    assert table.stdout.strip() == "t"
