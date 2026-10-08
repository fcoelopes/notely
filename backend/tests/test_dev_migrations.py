"""Alembic upgrades fresh and legacy databases without altering partial schemas."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate.py"
ROLLBACK = ROOT / "scripts" / "rollback.py"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


# Head atual do histórico: os testes derivam a revisão esperada em vez de fixá-la, para
# que uma migration nova não exija reescrevê-los.
HEAD = _script().get_current_head()


def parent_of(revision: str) -> str:
    """Revisão imediatamente anterior, já que o rollback desce um passo por vez."""
    parent = _script().get_revision(revision).down_revision
    assert isinstance(parent, str), f"{revision} has no linear parent revision"
    return parent


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
    assert f"Alembic revision {HEAD}: current" in first.stdout
    second = migrate(database, tmp_path)
    assert second.returncode == 0, second.stderr
    assert f"Alembic revision {HEAD}: current" in second.stdout
    assert list(tmp_path.glob("*.dump")) == []
    result = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                    "select version_num from alembic_version")
    assert result.stdout.strip() == HEAD

    # Regressão: o grafo do AGE cria um schema com o nome do usuário (`notely`), e o
    # `search_path` default `"$user", public` desviaria DDL não qualificado para lá. As
    # tabelas de domínio precisam ficar em `public`.
    misplaced = docker(
        "psql", "-U", "notely", "-d", database, "-At", "-c",
        "select n.nspname || '.' || c.relname from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where c.relname in ('documents', 'annotations', 'outbox_events', "
        "'outbox_deliveries', 'study_sessions', 'reader_events') "
        "and n.nspname <> 'public'",
    )
    assert misplaced.stdout.strip() == ""


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
    assert f"Alembic revision {HEAD}: current" in result.stdout
    assert len(list(tmp_path.glob("*.dump"))) == 1
    version = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                     "select version_num from alembic_version")
    assert version.stdout.strip() == HEAD
    preserved = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                       f"select title from documents where id = '{document_id}'")
    assert preserved.stdout.strip() == "Preserve me"


def test_alembic_downgrade_and_upgrade(database: str, tmp_path: Path) -> None:
    assert migrate(database, tmp_path).returncode == 0
    # Um passo a partir do head. O conteúdo do down de cada revisão é coberto pelos seus
    # próprios testes; aqui validamos o ciclo com prévia, backup e re-upgrade.
    previous = parent_of(HEAD)
    preview = subprocess.run(
        [sys.executable, str(ROLLBACK), "--database", database, "--target", previous],
        text=True, capture_output=True, cwd=ROOT, check=False,
    )
    assert preview.returncode == 0, preview.stderr
    assert "Preview only" in preview.stdout
    assert list(tmp_path.glob("*.dump")) == []
    downgraded = subprocess.run(
        [sys.executable, str(ROLLBACK), "--database", database, "--target", previous,
         "--backup-dir", str(tmp_path), "--execute"],
        text=True, capture_output=True, cwd=ROOT, check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    assert "Backup and full restore verified" in downgraded.stdout
    assert "Rollback complete" in downgraded.stdout
    stepped = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                     "select version_num from alembic_version")
    assert stepped.stdout.strip() == previous
    upgraded = migrate(database, tmp_path)
    assert upgraded.returncode == 0, upgraded.stderr
    assert len(list(tmp_path.glob("*.dump"))) == 2
    restored = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                      "select version_num from alembic_version")
    assert restored.stdout.strip() == HEAD


def test_partial_schema_is_not_modified(database: str, tmp_path: Path) -> None:
    created = docker("psql", "-U", "notely", "-d", database, "-c", "create table documents (id integer)")
    assert created.returncode == 0, created.stderr
    result = migrate(database, tmp_path)
    assert result.returncode == 1
    assert "partial schema" in result.stderr
    table = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                   "select to_regclass('annotations') is null")
    assert table.stdout.strip() == "t"


def test_downgrade_cannot_discard_reading_progress(database: str, tmp_path: Path) -> None:
    assert migrate(database, tmp_path).returncode == 0
    document_id = str(uuid4())
    session_id = str(uuid4())
    inserted = docker("psql", "-v", "ON_ERROR_STOP=1", "-U", "notely", "-d", database,
                      input=("INSERT INTO documents (id, sha256, title, filename, page_count, "
                             "storage_uri, created_at, updated_at) VALUES "
                             f"('{document_id}', '{'b' * 64}', 'Protected', 'test.pdf', 1, "
                             "'test://pdf', now(), now());"
                             "INSERT INTO study_sessions (id, created_at, updated_at) VALUES "
                             f"('{session_id}', now(), now());"
                             "INSERT INTO study_session_viewed_pages "
                             "(study_session_id, document_id, page_number, first_viewed_at) VALUES "
                             f"('{session_id}', '{document_id}', 1, now());"))
    assert inserted.returncode == 0, inserted.stderr
    # Desce direto até a revisão que protege o progresso. Usar o script de rollback passo a
    # passo faria backup e restore completos por revisão, e o custo cresce a cada migration.
    environment = os.environ | {
        "NOTELY_DATABASE_URL": f"postgresql+asyncpg://notely:notely@localhost:5432/{database}"
    }
    stepped = subprocess.run(
        [str(ROOT / "backend" / ".venv" / "bin" / "alembic"), "-c", "alembic.ini",
         "downgrade", "0007"],
        text=True, capture_output=True, cwd=ROOT, env=environment, check=False,
    )
    assert stepped.returncode == 0, stepped.stderr
    dumps = len(list(tmp_path.glob("*.dump")))
    preview = subprocess.run(
        [sys.executable, str(ROLLBACK), "--database", database, "--target", "0006",
         "--backup-dir", str(tmp_path)],
        text=True, capture_output=True, cwd=ROOT, check=False,
    )
    assert preview.returncode == 0, preview.stderr
    assert "Protected data: reading progress" in preview.stdout
    refused = subprocess.run(
        [sys.executable, str(ROLLBACK), "--database", database, "--target", "0006",
         "--backup-dir", str(tmp_path), "--execute"],
        text=True, capture_output=True, cwd=ROOT, check=False,
    )
    assert refused.returncode == 1
    assert "would discard protected data" in refused.stderr
    # Nem a prévia nem a recusa criam backup novo.
    assert len(list(tmp_path.glob("*.dump"))) == dumps
    environment = os.environ | {
        "NOTELY_DATABASE_URL": f"postgresql+asyncpg://notely:notely@localhost:5432/{database}"
    }
    direct = subprocess.run(
        [str(ROOT / "backend" / ".venv" / "bin" / "alembic"), "-c", "alembic.ini",
         "downgrade", "0006"],
        text=True, capture_output=True, cwd=ROOT, env=environment, check=False,
    )
    assert direct.returncode != 0
    assert "would discard: reading progress" in direct.stderr
    preserved = docker("psql", "-U", "notely", "-d", database, "-At", "-c",
                       "select (select version_num from alembic_version), "
                       "(select count(*) from study_session_viewed_pages)")
    assert preserved.stdout.strip() == "0007|1"


def test_backup_restores_to_new_database_and_startup_can_use_it(database: str, tmp_path: Path) -> None:
    assert migrate(database, tmp_path).returncode == 0
    document_id = str(uuid4())
    inserted = docker("psql", "-v", "ON_ERROR_STOP=1", "-U", "notely", "-d", database,
                      input=("INSERT INTO documents (id, sha256, title, filename, page_count, "
                             "storage_uri, created_at, updated_at) VALUES "
                             f"('{document_id}', '{'c' * 64}', 'Recovered', 'test.pdf', 1, "
                             "'test://pdf', now(), now());"))
    assert inserted.returncode == 0, inserted.stderr
    archive = tmp_path / "recovery.dump"
    with archive.open("wb") as output:
        dumped = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-Fc", "-U",
             "notely", database],
            stdout=output, stderr=subprocess.PIPE, cwd=ROOT, check=False,
        )
    assert dumped.returncode == 0, dumped.stderr.decode(errors="replace")
    recovered = f"notely_recovered_test_{uuid4().hex[:12]}"
    try:
        restored = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "restore_backup.py"), str(archive),
             "--database", recovered],
            text=True, capture_output=True, cwd=ROOT, check=False,
        )
        assert restored.returncode == 0, restored.stderr
        assert f"Alembic revision: {HEAD}" in restored.stdout
        refused = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "restore_backup.py"), str(archive),
             "--database", recovered],
            text=True, capture_output=True, cwd=ROOT, check=False,
        )
        assert refused.returncode == 1
        assert "Database already exists" in refused.stderr
        environment = os.environ.copy()
        environment.pop("NOTELY_DATABASE_URL", None)
        started = subprocess.run(
            ["bash", "scripts/dev.sh", "--migrate-only", "--database", recovered],
            text=True, capture_output=True, cwd=ROOT, env=environment, check=False,
        )
        assert started.returncode == 0, started.stderr
        assert f"Alembic revision {HEAD}: current" in started.stdout
        document = docker("psql", "-U", "notely", "-d", recovered, "-At", "-c",
                          f"select title from documents where id = '{document_id}'")
        assert document.stdout.strip() == "Recovered"
    finally:
        dropped = docker("dropdb", "-U", "notely", recovered)
        assert dropped.returncode == 0, dropped.stderr
