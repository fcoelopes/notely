#!/usr/bin/env python3
"""Preview or apply one guarded Alembic downgrade on the local Docker database."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError

from migrate import ROOT, backup, psql
from restore_backup import restore

sys.path.insert(0, str(ROOT / "infra" / "db" / "migrations"))
from rollback_policy import DOWNGRADE_GUARDS  # noqa: E402


def rollback(database: str, backup_dir: Path, target: str | None, execute: bool) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = f"postgresql+asyncpg://notely:notely@localhost:5432/{database}"
    script = ScriptDirectory.from_config(config)
    if psql(database, "SELECT to_regclass('public.alembic_version') IS NOT NULL;") != "t":
        raise RuntimeError("Database has no Alembic revision; run scripts/migrate.py first")
    current = psql(database, "SELECT version_num FROM alembic_version;")
    try:
        revision = script.get_revision(current) if current else None
    except ResolutionError as exc:
        raise RuntimeError(f"Unknown Alembic revision: {current}") from exc
    if revision is None:
        raise RuntimeError(f"Unknown Alembic revision: {current or '(empty)'}")
    previous = revision.down_revision
    if not isinstance(previous, (str, type(None))):
        raise RuntimeError("Rollback supports only a linear, one-revision history")
    expected = previous or "base"
    if target is not None and target != expected:
        raise RuntimeError(f"Only one revision at a time: {current} -> {expected}")

    print(f"Rollback plan: {current} -> {expected}", flush=True)
    if current not in DOWNGRADE_GUARDS:
        raise RuntimeError(f"Revision {current} has no rollback policy")
    _, checks = DOWNGRADE_GUARDS[current]
    blocked = [label for label, query in checks if psql(database, query + ";") == "t"]
    if blocked:
        print(f"Protected data: {', '.join(blocked)}", flush=True)
    if current in {"0004", "0005"}:
        print("Corpus projection will be removed; it must be rebuilt after an upgrade", flush=True)
    if not execute:
        print("Preview only. Pass --execute to back up and apply this single downgrade.", flush=True)
        return
    if blocked:
        raise RuntimeError("Downgrade refused because it would discard protected data")
    connections = int(psql(database, "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend';"))
    if connections:
        raise RuntimeError(f"Stop API and workers before rollback ({connections} other database connection(s))")
    saved = backup(database, backup_dir)
    restore(saved, f"notely_restore_check_{uuid4().hex[:12]}", verify_only=True)
    print(f"Backup and full restore verified: {saved}", flush=True)
    try:
        command.downgrade(config, expected)
    except Exception:
        print(f"Downgrade failed. Pre-rollback backup: {saved}", file=sys.stderr, flush=True)
        raise
    after = psql(database, "SELECT version_num FROM alembic_version;")
    if after != (previous or ""):
        raise RuntimeError(f"Unexpected revision after downgrade: {after or '(empty)'}; backup: {saved}")
    print(f"Rollback complete: {current} -> {expected}; backup: {saved}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="notely", help="local Docker PostgreSQL database")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / ".local" / "backups")
    parser.add_argument("--target", help="expected parent revision; only one step is permitted")
    parser.add_argument("--execute", action="store_true", help="apply after backup and guards")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", args.database):
        parser.error("database must be a simple PostgreSQL identifier")
    try:
        rollback(args.database, args.backup_dir, args.target, args.execute)
    except (RuntimeError, OSError) as exc:
        print(f"Rollback refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
