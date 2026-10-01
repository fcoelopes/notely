# Notely API

O backend persiste domínio e outbox no PostgreSQL. PDFs enviados ao endpoint de upload são validados, examinados pelo ClamAV e somente então armazenados no MinIO.

## Executar localmente

Requer Python 3.12+, `uv`, Docker e memória suficiente para o ClamAV.

Na raiz do repositório:

```bash
docker compose up -d --build postgres minio clamav
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U notely -d notely < infra/db/migrations/0001_initial.up.sql
uv sync --project backend --dev
uv run --project backend uvicorn notely.api.app:app --reload
```

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

## Testar

```bash
uv run --project backend pytest
```

Os contratos HTTP ficam disponíveis em `http://localhost:8000/docs`.

