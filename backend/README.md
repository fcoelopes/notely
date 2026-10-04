# Notely API

O backend persiste domínio e outbox no PostgreSQL. PDFs enviados ao endpoint de upload são validados, examinados pelo ClamAV e somente então armazenados no MinIO.

## Executar localmente

Requer Python 3.12+, `uv`, Docker e memória suficiente para o ClamAV.

Na raiz do repositório:

```bash
./scripts/dev.sh
```

O comando sobe PostgreSQL, MinIO e ClamAV, sincroniza as dependências, aplica as revisões pendentes com Alembic e inicia API, Reader e os três workers (TimescaleDB, corpus e curadoria). Use `Ctrl+C` para encerrar os processos da aplicação; os contêineres continuam ativos. Se a API ou o Reader já ocupam as portas locais, encerre a instância anterior antes de iniciar outra.

Para aplicar migrations sem iniciar outra instância da aplicação:

```bash
./scripts/dev.sh --migrate-only
```

O PostgreSQL do ambiente local é a imagem `timescale/timescaledb` com `shared_preload_libraries=timescaledb`: a trilha temporal de leitura vive em TimescaleDB, e a migration falha de forma explícita se a extensão não estiver disponível.

### Atualizar um banco local existente

As revisões Alembic ficam em `infra/db/migrations/versions/`. Os arquivos SQL `0001` a `0007` são preservados e executados pelas revisões correspondentes. Na primeira execução sobre um banco anterior ao Alembic, `./scripts/dev.sh` confere os objetos dessas sete migrations, recusa esquemas parciais ou com lacunas, verifica um backup em `.local/backups/` e registra a última revisão aplicada. Depois usa `alembic upgrade head`. Um banco já versionado recebe backup antes de qualquer upgrade pendente. Os marcadores em `scripts/migrate.py` servem somente para reconhecer os sete esquemas legados; revisões novas não precisam deles.

Para inspecionar o estado ou criar uma revisão manual, na raiz do repositório:

```bash
backend/.venv/bin/alembic -c alembic.ini current
backend/.venv/bin/alembic -c alembic.ini history
backend/.venv/bin/alembic -c alembic.ini revision -m "descricao da mudanca"
```

Edite `upgrade()` e `downgrade()` da revisão gerada antes de aplicá-la. O Alembic lê `NOTELY_DATABASE_URL` para comandos diretos; o script de desenvolvimento fixa o banco Docker local. Para atualizar sem iniciar outra instância, use `./scripts/dev.sh --migrate-only`.

Sem a migration `0006`, a API falha ao salvar destaques e dúvidas porque falta `annotations.study_session_id`. Os workers de corpus e curadoria são iniciados junto com a aplicação; sem eles, dúvidas salvas ficam pendentes. O worker de corpus também enfileira PDFs antigos sem índice ao iniciar.

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
backend/.venv/bin/python scripts/migrate.py --database notely_test
```

Use `NOTELY_TEST_DATABASE_URL` para apontar para outro banco. Cada teste limpa as tabelas do
banco de teste antes de executar.

Os contratos HTTP ficam disponíveis em `http://localhost:8000/docs`.

## Índice textual dos PDFs (issue #2, primeira etapa)

Após aplicar `infra/db/migrations/0004_document_corpus.up.sql`, o worker de corpus
consome `document.created` do outbox, lê o PDF armazenado, verifica o SHA-256 e a
contagem real de páginas e grava o texto por página em PostgreSQL. O campo
`search_vector` permite busca lexical; a projeção pode ser reconstruída do PDF.
O upload continua sem aguardar extração. Documentos antigos podem ser enfileirados
pelo backfill idempotente:

```bash
uv run --project backend python -m notely.workers.corpus --backfill --once
uv run --project backend python -m notely.workers.corpus
```

O índice registra `ready`, `empty` (PDF sem texto extraível) ou `failed`. Arquivos
escaneados ainda precisam de OCR; nenhuma fonte de dúvida é exibida com base em
texto que não foi extraído. Os testes de integração usam `notely_test` com a
migration 0004 aplicada.

## Fontes para dúvidas da sessão (issue #2)

Aplicar as migrations `0005_corpus_chunks` e `0006_question_curation` antes de
iniciar os workers. A versão do extrator mudou para `text-v2-chunks`; reconstruir
os documentos indexados anteriormente:

```bash
uv run --project backend python -m notely.workers.corpus --backfill --once
uv run --project backend python -m notely.workers.corpus
uv run --project backend python -m notely.workers.curation
```

O Reader envia `study_session_id` ao criar uma dúvida. A API confirma a anotação
e grava o pedido e o evento de outbox na mesma transação. O worker de curadoria
procura apenas nos PDFs dessa sessão, verifica cada excerto contra o índice atual
e persiste até três fontes. O provider padrão é `lexical_search` local; o Reader
o identifica como busca lexical. Não há resposta automática nem pesquisa web.

`GET /api/study-sessions/{session_id}/questions/{annotation_id}/sources` informa
`pending`, `ready`, `no_source` ou `failed`, com fontes e disponibilidade atual.
`POST` no mesmo caminho acrescido de `/retry` reinicia um pedido `failed` ou
`no_source`. O worker usa retry com backoff enquanto o corpus está pendente; ao
esgotar as tentativas, registra `failed` sem alterar a dúvida.
