# Notely API

O backend persiste domínio e outbox no PostgreSQL. PDFs enviados ao endpoint de upload são validados, examinados pelo ClamAV e somente então armazenados no MinIO.

## Executar localmente

Requer Python 3.12+, `uv`, Docker e memória suficiente para o ClamAV.

Na raiz do repositório:

```bash
./scripts/dev.sh
```

O comando sobe PostgreSQL, MinIO e ClamAV, sincroniza as dependências, aplica as revisões pendentes com Alembic e inicia API, Reader e os cinco workers (TimescaleDB, corpus, curadoria, grafo e conceitos). Use `Ctrl+C` para encerrar os processos da aplicação; os contêineres continuam ativos. Se a API ou o Reader já ocupam as portas locais, encerre a instância anterior antes de iniciar outra.

Para aplicar migrations sem iniciar outra instância da aplicação:

```bash
./scripts/dev.sh --migrate-only
```

O PostgreSQL do ambiente local é construído de [`infra/db/Dockerfile`](../infra/db/Dockerfile) como `notely-postgres:age-1.7.0-pg17`, com TimescaleDB e Apache AGE carregados em `shared_preload_libraries`: a trilha temporal de leitura vive em TimescaleDB e o grafo em AGE, e as migrations falham de forma explícita se as extensões não estiverem disponíveis. O primeiro build dessa imagem compila o AGE e pode demorar. Quando alguma imagem do projeto precisa ser construída, o Compose anuncia `Building` para todas as que têm seção `build`, inclusive a do Postgres; se a imagem do AGE já existe, ela é reaproveitada sem alteração e as execuções seguintes não anunciam nada.

### Atualizar um banco local existente

As revisões Alembic ficam em `infra/db/migrations/versions/`. Os arquivos SQL `0001` a `0007` são preservados e executados pelas revisões correspondentes. Na primeira execução sobre um banco anterior ao Alembic, `./scripts/dev.sh` confere os objetos dessas sete migrations, recusa esquemas parciais ou com lacunas, verifica um backup em `.local/backups/` e registra a última revisão aplicada. Depois usa `alembic upgrade head`. Um banco já versionado recebe backup antes de qualquer upgrade pendente. Os marcadores em `scripts/migrate.py` servem somente para reconhecer os sete esquemas legados; revisões novas não precisam deles.

Para inspecionar o estado ou criar uma revisão manual, na raiz do repositório:

```bash
backend/.venv/bin/alembic -c alembic.ini current
backend/.venv/bin/alembic -c alembic.ini history
backend/.venv/bin/alembic -c alembic.ini revision -m "descricao da mudanca"
```

Edite `upgrade()` e `downgrade()` da revisão gerada antes de aplicá-la e registre sua política de rollback. O Alembic lê `NOTELY_DATABASE_URL` para comandos diretos; o script de desenvolvimento usa o banco Docker local. Para atualizar sem iniciar outra instância, use `./scripts/dev.sh --migrate-only`. Para recuperar um banco local restaurado, use `./scripts/dev.sh --database NOME`. O procedimento de prévia, rollback e restauração está em [docs/rollback.md](../docs/rollback.md).

Sem a migration `0006`, a API falha ao salvar destaques e dúvidas porque falta `annotations.study_session_id`. Os workers de corpus e curadoria são iniciados junto com a aplicação; sem eles, dúvidas salvas ficam pendentes. O worker de corpus também enfileira PDFs antigos sem índice ao iniciar.

O primeiro build do MinIO compila a release fixada em [`infra/minio/Dockerfile`](../infra/minio/Dockerfile) e pode demorar. A console local fica em `http://localhost:9001`.

Configurações e credenciais de desenvolvimento estão em [`.env.example`](../.env.example), na raiz do repositório. Copie com `cp .env.example .env` e não reutilize esses valores em produção.

Duas variáveis são obrigatórias para cifrar segredos em repouso: `NOTELY_AUTH_SECRET_KEY`, que protege o segredo do segundo fator, e `NOTELY_AI_SECRET_KEY`, que protege a chave de modelo do usuário. Sem elas a aplicação sobe e funciona normalmente, mas ativar o segundo fator e salvar a chave de modelo respondem `503` (`AuthConfigurationError` e `AssistantConfigurationError`) em vez de gravar o segredo em claro. Cada uma precisa de uma chave Fernet distinta, base64 urlsafe de 32 bytes. Gere cada uma uma vez e guarde o valor no `.env`:

```bash
backend/.venv/bin/python -c "from notely.core.security import SecretCipher; print(SecretCipher.generate_key())"
```

A API lê o `.env` do diretório em que é iniciada, e o `./scripts/dev.sh` inicia a partir da raiz: um `.env` dentro de `backend/` seria ignorado ao usar o script. Trocar uma dessas chaves torna ilegíveis os segredos já gravados com ela, e o segundo fator ou a chave de modelo precisam ser cadastrados de novo.

## Fluxo de upload

```text
multipart PDF
  → validação de tipo/tamanho + SHA-256
  → ClamAV INSTREAM
  → MinIO
  → PostgreSQL + outbox
```

Qualquer falha ou resposta desconhecida do ClamAV rejeita o upload. O limite padrão é 100 MiB.

## Marcador de página

O marcador manual por PDF e usuário fica em PostgreSQL, separado do progresso de
páginas visualizadas. `GET /api/document-bookmarks` lista os marcadores;
`PUT /api/documents/{document_id}/bookmark` recebe `{"page_number": 12}`;
`DELETE` no mesmo caminho remove o marcador. A migration `0014` protege os dados
num rollback. Veja [a regra do marcador](../docs/marcador-pagina.md).

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

A entrega é **por consumidor**: `outbox_deliveries` guarda o estado de cada worker para
cada evento, então o mesmo `annotation.created` alimenta a trilha temporal, o índice do
corpus e o grafo, cada um com retry independente. Um consumidor novo precisa ser adotado
pela migration que o introduz, para não reprojetar o acervo inteiro. Ver
[ADR 0004](../docs/adr/0004-outbox-delivery-per-consumer.md).

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

## Projeção de grafo no Apache AGE (issue #6)

A imagem local do PostgreSQL é construída de [`infra/db/Dockerfile`](../infra/db/Dockerfile):
sobre a base `timescale/timescaledb`, ela compila o Apache AGE 1.7.0 (PG17) e o servidor
passa a subir com `shared_preload_libraries=timescaledb,age`. Sem essa imagem, a migration
`0008` falha ao criar a extensão. O primeiro build compila o AGE e demora cerca de 30 s;
depois fica em cache.

A migration `0008` cria a extensão e o grafo `notely` com os labels `Document`,
`Annotation`, `Question` e `HAS_ANNOTATION`. O worker de grafo consome
`document.created` e `annotation.created` do outbox, lê o estado autoral do PostgreSQL e
projeta de forma idempotente:

```bash
uv run --project backend python -m notely.workers.graph --once      # um lote
uv run --project backend python -m notely.workers.graph             # em loop
uv run --project backend python -m notely.workers.graph --rebuild   # reconstrói do PostgreSQL
```

Documentos e anotações anteriores a esta entrega não têm evento pendente no outbox (já
consumidos pelos workers existentes), então a projeção inicial do acervo existente exige
`--rebuild`. Novas anotações são projetadas pelo worker em execução. O grafo é
reconstruível: apagá-lo e rodar `--rebuild` reproduz o estado a partir do PostgreSQL. Ver
[ADR 0003](../docs/adr/0003-age-graph-projection.md).

## Conceitos extraídos das anotações (issue #10)

O worker de conceitos consome `annotation.created`, extrai conceitos por um provider
configurável e grava as relações anotação → conceito em PostgreSQL como **inferência**
(`ai_inference` / `suggested`). Em seguida publica `concepts.extracted`, e o worker de
grafo projeta os nós `Concept` e as arestas `REFERS_TO`/`QUESTIONS`:

```bash
uv run --project backend python -m notely.workers.concepts --once
uv run --project backend python -m notely.workers.concepts
uv run --project backend python -m notely.workers.concepts --backfill --once
```

O provider padrão é determinístico e local (`heuristic`), sem rede. Para usar um modelo
com a própria chave (BYOK):

```bash
NOTELY_CONCEPT_PROVIDER=anthropic NOTELY_CONCEPT_MODEL=claude-... NOTELY_CONCEPT_API_KEY=... \
  uv run --project backend python -m notely.workers.concepts
```

Use `NOTELY_CONCEPT_PROVIDER=openai_compatible` com `NOTELY_CONCEPT_ENDPOINT` para
endpoints compatíveis. Anotações anteriores a esta entrega não são extraídas
automaticamente: use `--backfill`, que enfileira só as anotações ainda sem conceitos.

Nada é promovido a conhecimento autoral sem aceite explícito do usuário: as relações
nascem `suggested` e a promoção grava `accepted_at`. Ver
[ADR 0005](../docs/adr/0005-concept-extraction-as-inference.md).

## Afirmações e arestas semânticas no grafo (issue #21)

O `Claim` é conhecimento autoral: a pessoa lê um trecho, afirma uma proposição e declara se
aquele trecho a sustenta ou a contradiz. Aplica-se a migration `0015`, que cria `claims` e
`claim_relations` e os rótulos `Claim`, `SUPPORTS`, `CONTRADICTS`, `ANSWERED_BY` e
`RELATED_TO` no grafo. Todas as rotas exigem sessão ativa e são escopadas por dono:

```text
POST   /api/claims                          {annotation_id, text, relation} com relation em
                                            supports | contradicts; reaproveita a afirmação
                                            de mesmo texto e apenas acrescenta evidência
GET    /api/claims                          afirmações do usuário com a evidência
                                            (documento, página, trecho, relação e proveniência)
DELETE /api/claims/{claim_id}               exclusão suave e remoção do nó no grafo
POST   /api/questions/{annotation_id}/answers   {answer_annotation_id} cria ANSWERED_BY
POST   /api/concept-relations               {source_concept_id, target_concept_id} cria RELATED_TO
DELETE /api/claim-relations/{relation_id}   remove qualquer aresta semântica
```

A API grava a afirmação, a aresta e o evento de outbox **na mesma transação**
(`claim.created`, `claim.relation.created`, `claim.deleted`, `claim.relation.removed`). O
worker de grafo continua sendo o único escritor no AGE e projeta o nó `Claim` e as quatro
arestas a partir do estado do PostgreSQL:

```bash
uv run --project backend python -m notely.workers.graph --once
uv run --project backend python -m notely.workers.graph --rebuild
```

O nó carrega `source_id`, `text`, `provenance` e `status`; as arestas carregam `provenance` e
`status`. Uma afirmação feita pela API nasce `user_authored` e `accepted`, porque foi o próprio
usuário quem a escreveu. Aceitar uma sugestão de modelo não troca a origem: a linha continua
`ai_inference`, com `provider` e `model` preservados, e passa a `accepted` — `user_confirmed` não
é usado aqui, só em `annotation_concepts`. Reafirmar o mesmo texto reaproveita a afirmação sem
alterá-la. Documento, anotação, conceito ou afirmação de outra conta respondem como inexistentes.
Remover uma afirmação tira o nó e as arestas do grafo; a linha permanece no PostgreSQL com
`deleted_at` e o `--rebuild` a ignora. Ver
[ADR 0008](../docs/adr/0008-claims-and-semantic-edges.md).

## Contas, sessão e segundo fator (issue #7)

Todas as rotas de domínio exigem sessão ativa: sem cookie válido a API responde `401`
(`/health` continua público). Não há tela de cadastro; as contas nascem por linha de
comando:

```bash
backend/.venv/bin/python -m notely.cli bootstrap --email seu@email   # define a senha do dono
backend/.venv/bin/python -m notely.cli create-user --email pessoa@email
backend/.venv/bin/python -m notely.cli set-password --email seu@email
backend/.venv/bin/python -m notely.cli list-users
```

Sem `--password`, o comando gera uma senha e a exibe uma única vez. O `bootstrap` adota o
dono provisório criado pela migration, então o acervo existente continua da mesma pessoa.

O segundo fator é TOTP (aplicativo autenticador) e ativar exige a chave de cifra do
segredo, `NOTELY_AUTH_SECRET_KEY`, configurada no `.env`. Gere o valor uma única vez e
guarde-o: uma chave nova a cada sessão tornaria ilegível o segredo já gravado.

Fluxo HTTP: `POST /api/auth/login` e, com segundo fator ativo, `POST /api/auth/mfa` ou
`POST /api/auth/recovery`; `GET /api/auth/me`; `POST /api/auth/logout`;
`POST /api/auth/mfa/enroll`, `/confirm` e `/disable`. A sessão vai em cookie `HttpOnly`.
Cada registro tem dono, aplicado pelo escopo da unidade de trabalho. Ver
[ADR 0006](../docs/adr/0006-user-accounts-and-ownership.md).

## Assistente de estudo com o modelo do usuário (issue #8)

O assistente usa a chave do próprio usuário (BYOK) e responde ancorado no corpus da
sessão e nas anotações dele. A chave é cifrada em repouso com `NOTELY_AI_SECRET_KEY`,
definida no `.env`, e nunca é devolvida pela API.

```text
GET    /api/ai-config              consulta a configuração (informa has_key, nunca a chave)
PUT    /api/ai-config              define provider, modelo, chave e base URL
DELETE /api/ai-config              limpa a configuração
POST   /api/study-assistant/chat   pergunta; com study_session_id a resposta é ancorada
```

`POST /api/study-assistant/chat` recebe `{message, study_session_id?, document_id?}`.
Sem `study_session_id` é chat livre, e a resposta traz `grounded: false` e nenhuma fonte.
Com sessão, a resposta cita documento e página e informa `provider` e `model`.

A resposta é inferência de modelo e **não é persistida**: ela não vira anotação nem
conhecimento autoral. O contexto enviado é o mínimo necessário — o documento integral não
sai daqui. Ver [ADR 0007](../docs/adr/0007-study-assistant-with-user-model.md).
