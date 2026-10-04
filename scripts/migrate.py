#!/usr/bin/env python3
"""Apply missing local PostgreSQL migrations after checking their schema markers.

The repository predates a migration ledger. Existing databases are identified by
objects created by each transactional migration; a partial or gapped schema stops
startup instead of risking duplicate DDL or overwriting user data.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "infra" / "db" / "migrations"


def table(name: str) -> str:
    return f"to_regclass('public.{name}') IS NOT NULL"


def column(table_name: str, name: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = '{table_name}' "
        f"AND column_name = '{name}')"
    )


MARKERS: dict[str, tuple[str, ...]] = {
    "0001_initial": (
        table("documents"), table("annotations"), table("outbox_events"),
    ),
    "0002_study_sessions": (
        table("study_sessions"), table("study_session_documents"),
        table("ai_suggestions"), column("outbox_events", "dedupe_key"),
        table("outbox_events_dedupe_key_idx"),
    ),
    "0003_reading_sessions_and_reader_events": (
        table("reading_sessions"), table("reader_events"),
        column("annotations", "reading_session_id"),
        column("annotations", "passage_id"),
        "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')",
    ),
    "0004_document_corpus": (
        table("document_corpus_index"), table("document_corpus_pages"),
        table("document_corpus_pages_search_idx"),
    ),
    "0005_corpus_chunks": (
        table("document_corpus_chunks"), table("document_corpus_chunks_search_idx"),
    ),
    "0006_question_curation": (
        column("annotations", "study_session_id"),
        table("question_curation_requests"), table("question_curated_sources"),
        table("question_curation_requests_session_idx"),
    ),
    "0007_reading_progress": (
        table("study_session_viewed_pages"),
        table("study_session_viewed_pages_document_idx"),
    ),
}


def psql(database: str, sql: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-X", "-q",
         "-v", "ON_ERROR_STOP=1", "-U", "notely", "-d", database, "-At"],
        input=sql, text=True, capture_output=True, cwd=ROOT, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "psql failed")
    return result.stdout.strip()


def marker_state(database: str, version: str) -> str:
    checks = MARKERS[version]
    result = psql(database, "SELECT " + ", ".join(checks) + ";")
    values = result.split("|")
    if len(values) != len(checks) or any(value not in {"t", "f"} for value in values):
        raise RuntimeError(f"Unexpected schema check for {version}: {result}")
    if all(value == "t" for value in values):
        return "applied"
    if all(value == "f" for value in values):
        return "missing"
    return "partial"


def migration_files() -> list[tuple[str, Path]]:
    files = sorted(MIGRATIONS.glob("*.up.sql"))
    versions = [path.name.removesuffix(".up.sql") for path in files]
    if not files or set(versions) != set(MARKERS):
        raise RuntimeError("Migration files and schema markers differ; update MARKERS before starting")
    numbers = [int(version.split("_", 1)[0]) for version in versions]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError("Migration sequence must be contiguous from 0001")
    return list(zip(versions, files, strict=True))


def backup(database: str, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    descriptor, name = tempfile.mkstemp(
        prefix=f"{database}-{timestamp}-", suffix=".dump", dir=backup_dir
    )
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            result = subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-Fc",
                 "-U", "notely", database],
                stdout=output, stderr=subprocess.PIPE, cwd=ROOT, check=False,
            )
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or "pg_dump failed")
        with path.open("rb") as source:
            checked = subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "pg_restore", "-l"],
                stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                cwd=ROOT, check=False,
            )
        if checked.returncode:
            raise RuntimeError(checked.stderr.decode(errors="replace").strip() or "backup validation failed")
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def migrate(database: str, backup_dir: Path) -> None:
    files = migration_files()
    states = [(version, path, marker_state(database, version)) for version, path in files]
    for version, _, state in states:
        if state == "partial":
            raise RuntimeError(f"Migration {version} has a partial schema; inspect it before continuing")
    missing_seen = False
    for version, _, state in states:
        if state == "missing":
            missing_seen = True
        elif missing_seen:
            raise RuntimeError(f"Migration {version} is applied after a missing migration; inspect schema")

    if any(state == "missing" for _, _, state in states) and any(
        state == "applied" for _, _, state in states
    ):
        saved = backup(database, backup_dir)
        print(f"Backup verified: {saved}", flush=True)

    for version, path, state in states:
        if state == "applied":
            print(f"{version}: already applied", flush=True)
            continue
        print(f"{version}: applying", flush=True)
        psql(database, path.read_text())
        if marker_state(database, version) != "applied":
            raise RuntimeError(f"Migration {version} finished without all expected objects")
    print("Migrations are current", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="notely", help="local PostgreSQL database name")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / ".local" / "backups")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", args.database):
        parser.error("database must be a simple PostgreSQL identifier")
    try:
        migrate(args.database, args.backup_dir)
    except (RuntimeError, OSError) as exc:
        print(f"Migration check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
