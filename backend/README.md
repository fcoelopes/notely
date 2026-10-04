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

### Atualizar um banco local existente

O loop de migrations acima serve para um banco **novo**. Os arquivos SQL não podem ser reaplicados sobre tabelas existentes. Ao atualizar o código, faça backup e aplique **somente as migrations ausentes**, em ordem. Para conferir as etapas recentes:

```bash
docker compose exec -T postgres psql -U notely -d notely -c "select to_regclass('public.document_corpus_index') as m0004, to_regclass('public.document_corpus_chunks') as m0005, to_regclass('public.question_curation_requests') as m0006, to_regclass('public.study_session_viewed_pages') as m0007"
docker compose exec -T postgres pg_dump -Fc -U notely notely > ../notely-backup.dump
# Exemplo: se 0004 a 0007 estiverem ausentes:
for number in 0004 0005 0006 0007; do
  migration=$(find infra/db/migrations -name "${number}_*.up.sql" -print -quit)
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U notely -d notely < "$migration"
done
```

Sem a migration `0006`, a API falha ao salvar destaques e dúvidas porque a coluna `annotations.study_session_id` não existe; consequentemente, nenhum pedido de curadoria é criado. Após as migrations, inicie os workers de corpus e curadoria em terminais separados. Sem eles, a dúvida pode ser salva, mas a busca de fontes permanece pendente. Use `--backfill --once` no worker de corpus para indexar PDFs já enviados.

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
