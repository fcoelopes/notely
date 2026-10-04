#!/usr/bin/env python3
"""Restore a local pg_dump archive into a new Docker database, never over an existing one."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from migrate import ROOT, psql


def docker(*args: str, input_file=None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", *args],
        stdin=input_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=ROOT, check=False,
    )


def checked(*args: str, input_file=None) -> bytes:
    result = docker(*args, input_file=input_file)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"{' '.join(args)} failed")
    return result.stdout


def restore(archive: Path, database: str, verify_only: bool = False) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", database):
        raise RuntimeError(f"Invalid database name: {database}")
    if not archive.is_file():
        raise RuntimeError(f"Backup not found: {archive}")
    with archive.open("rb") as source:
        listing = checked("pg_restore", "-l", input_file=source).decode(errors="replace")
    uses_timescale = any("EXTENSION - timescaledb " in line for line in listing.splitlines())
    if psql("postgres", f"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = '{database}');") == "t":
        raise RuntimeError(f"Database already exists: {database}")
    checked("createdb", "-U", "notely", "-T", "template0", database)
    completed = False
    try:
        if uses_timescale:
            psql(database, "CREATE EXTENSION IF NOT EXISTS timescaledb;")
            psql(database, "SELECT timescaledb_pre_restore();")
        try:
            with archive.open("rb") as source:
                checked("pg_restore", "--exit-on-error", "--single-transaction", "--no-owner",
                        "--no-acl", "-U", "notely", "-d", database, input_file=source)
        finally:
            if uses_timescale:
                psql(database, "SELECT timescaledb_post_restore();")
        completed = True
        revision = psql(database, "SELECT version_num FROM alembic_version;") if psql(
            database, "SELECT to_regclass('public.alembic_version') IS NOT NULL;"
        ) == "t" else "legacy"
        print(f"Backup restored to {database}; Alembic revision: {revision}", flush=True)
    finally:
        if verify_only or not completed:
            dropped = docker("dropdb", "-U", "notely", database)
            if dropped.returncode:
                raise RuntimeError(
                    f"Temporary database {database} could not be removed: "
                    + dropped.stderr.decode(errors="replace").strip()
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="pg_dump custom-format archive")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--database", help="new local Docker database to keep")
    destination.add_argument("--verify-only", action="store_true", help="restore into and remove a temporary database")
    args = parser.parse_args()
    database = args.database or f"notely_restore_check_{uuid4().hex[:12]}"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", database):
        parser.error("database must be a simple PostgreSQL identifier")
    try:
        restore(args.archive, database, args.verify_only)
    except (RuntimeError, OSError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
