#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

if [[ $# -gt 1 || ( $# -eq 1 && "${1}" != "--migrate-only" ) ]]; then
  printf 'Uso: %s [--migrate-only]\n' "$0" >&2
  exit 2
fi
mode=${1:-start}

for executable in docker python3 uv; do
  command -v "$executable" > /dev/null || { printf 'Comando necessário ausente: %s\n' "$executable" >&2; exit 1; }
done

local_database_url='postgresql+asyncpg://notely:notely@localhost:5432/notely'
if [[ -n "${NOTELY_DATABASE_URL:-}" && "$NOTELY_DATABASE_URL" != "$local_database_url" ]]; then
  printf 'Este script usa o banco Docker local notely; NOTELY_DATABASE_URL aponta para outro banco.\n' >&2
  exit 1
fi
export NOTELY_DATABASE_URL="$local_database_url"

if [[ "$mode" == start ]]; then
  for executable in npm; do
    command -v "$executable" > /dev/null || { printf 'Comando necessário ausente: %s\n' "$executable" >&2; exit 1; }
  done
  python3 - <<'PY'
import socket
for port in (8000, 5173):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(f"Porta {port} em uso. Encerre a instância atual antes de usar scripts/dev.sh.")
    finally:
        sock.close()
PY
fi

if [[ "$mode" == --migrate-only ]]; then
  docker compose up -d --wait postgres
else
  docker compose up -d --wait postgres minio clamav
fi
uv sync --project backend --dev --frozen
backend/.venv/bin/python scripts/migrate.py

if [[ "$mode" == --migrate-only ]]; then
  exit 0
fi

if [[ ! -x frontend/node_modules/.bin/vite ]]; then
  npm --prefix frontend ci
fi

pids=()
cleanup() {
  if (( ${#pids[@]} > 0 )); then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

backend/.venv/bin/uvicorn notely.api.app:app --reload &
pids+=("$!")
backend/.venv/bin/python -m notely.workers.timescale &
pids+=("$!")
backend/.venv/bin/python -m notely.workers.corpus --backfill &
pids+=("$!")
backend/.venv/bin/python -m notely.workers.curation &
pids+=("$!")
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1 --strictPort) &
pids+=("$!")

printf 'Notely iniciado: Reader http://localhost:5173 | API http://localhost:8000/docs\n'
printf 'Ctrl+C encerra API, frontend e workers; os contêineres Docker continuam ativos.\n'
wait -n "${pids[@]}"
printf 'Um processo da aplicação terminou; encerrando os demais.\n' >&2
exit 1
