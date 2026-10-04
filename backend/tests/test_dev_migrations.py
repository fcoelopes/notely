"""O iniciador só aplica migrations ausentes e interrompe esquemas parciais."""

from __future__ import annotations

from pathlib import Path
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
    assert "0007_reading_progress: applying" in first.stdout
    second = migrate(database, tmp_path)
    assert second.returncode == 0, second.stderr
    assert "0001_initial: already applied" in second.stdout
    assert "0007_reading_progress: already applied" in second.stdout
    assert "applying" not in second.stdout
    result = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                    "select to_regclass('study_session_viewed_pages') is not null")
    assert result.stdout.strip() == "t"


def test_existing_database_receives_only_missing_migrations(database: str, tmp_path: Path) -> None:
    for path in sorted((ROOT / "infra" / "db" / "migrations").glob("000[1-3]*.up.sql")):
        applied = docker("psql", "-v", "ON_ERROR_STOP=1", "-U", "notely", "-d", database,
                         input=path.read_text())
        assert applied.returncode == 0, applied.stderr
    result = migrate(database, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "0003_reading_sessions_and_reader_events: already applied" in result.stdout
    assert "0004_document_corpus: applying" in result.stdout
    assert "0007_reading_progress: applying" in result.stdout
    assert len(list(tmp_path.glob("*.dump"))) == 1


def test_partial_schema_is_not_modified(database: str, tmp_path: Path) -> None:
    created = docker("psql", "-U", "notely", "-d", database, "-c", "create table documents (id integer)")
    assert created.returncode == 0, created.stderr
    result = migrate(database, tmp_path)
    assert result.returncode == 1
    assert "partial schema" in result.stderr
    table = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                   "select to_regclass('annotations') is null")
    assert table.stdout.strip() == "t"
