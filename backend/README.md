# Notely API

O backend persiste domínio e outbox no PostgreSQL. PDFs enviados ao endpoint de upload são validados, examinados pelo ClamAV e somente então armazenados no MinIO.

## Executar localmente

Requer Python 3.12+, `uv`, Docker e memória suficiente para o ClamAV.

Na raiz do repositório:

```bash
docker compose up -d --build postgres minio clamav
for migration in infra/db/migrations/*.up.sql; do
  docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U notely -d notely < "$migration"
done
uv sync --project backend --dev
uv run --project backend uvicorn notely.api.app:app --reload
```

O PostgreSQL do ambiente local é a imagem `timescale/timescaledb` com `shared_preload_libraries=timescaledb`: a trilha temporal de leitura vive em TimescaleDB, e a migration falha de forma explícita se a extensão não estiver disponível.

O primeiro build do MinIO compila a release fixada em [`infra/minio/Dockerfile`](../infra/minio/Dockerfile) e pode demorar. A console local fica em `http://localhost:9001`.

Configurações e credenciais de desenvolvimento estão em [`.env.example`](.env.example). Não as reutilize em produção.

## Fluxo de upload

```text
multipart PDF
  → validação de tipo/tamanho + SHA-256
  → ClamAV INSTREAM
  → MinIO
  → PostgreSQL + outbox
```

Qualquer falha ou resposta desconhecida do ClamAV rejeita o upload. O limite padrão é 100 MiB.

## Trilha temporal de leitura

O Reader abre uma sessão de leitura na primeira interação real com o documento e envia
`reading_session_id` em cada marcação. A API grava sessão/anotação e evento de outbox na
mesma transação; a projeção para TimescaleDB é assíncrona:

```bash
uv run --project backend python -m notely.workers.timescale --once   # um lote
uv run --project backend python -m notely.workers.timescale          # em loop
```

O worker reivindica apenas os eventos temporais (`reading.started`, `reading.ended`,
`annotation.created`, `annotation.updated`), grava em `reader_events` com
`ON CONFLICT (event_id, time)` e usa backoff exponencial com `attempt_count`/`available_at`.
Eventos de outros consumidores permanecem pendentes e não ocupam o lote.

## Testar

```bash
uv run --project backend pytest
```

Os testes de integração (`tests/test_integration_timescale.py`) exigem um banco de teste com
as migrations aplicadas e são pulados quando ele não existe:

```bash
docker compose exec -T postgres psql -U notely -d postgres -c "create database notely_test owner notely"
for migration in infra/db/migrations/*.up.sql; do
  docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U notely -d notely_test < "$migration"
done
```

Use `NOTELY_TEST_DATABASE_URL` para apontar para outro banco. Cada teste limpa as tabelas do
banco de teste antes de executar.

Os contratos HTTP ficam disponíveis em `http://localhost:8000/docs`.

