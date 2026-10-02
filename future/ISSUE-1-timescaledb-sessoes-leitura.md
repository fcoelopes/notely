# Plano de implementação — TimescaleDB, sessões de leitura e trilha temporal

Issue relacionada: https://github.com/fcoelopes/notely/issues/1

## Papel deste documento

Este arquivo é a **fonte de verdade técnica para a execução da Issue #1**.

A issue gerencia o ciclo da melhoria; este plano mantém o escopo técnico, decisões, riscos, ordem de implementação e validação. Alterações relevantes de abordagem devem ser registradas aqui para evitar divergência entre planejamento e execução.

## Objetivo

Implementar a primeira trilha temporal real do Notely.

O sistema deve persistir:

- data e hora de início da leitura;
- nome do arquivo lido;
- data e hora de cada marcação;
- tipo da marcação;
- página;
- identificador estável do trecho marcado;
- referência à annotation original persistida no PostgreSQL.

PostgreSQL continua sendo a fonte de verdade do domínio. TimescaleDB armazena a sequência temporal dos acontecimentos.

## Definição de marcação

No Notely, "marcação" não será uma nova entidade.

Uma marcação é uma `Annotation` com `type` explícito.

Tipos iniciais:

```text
highlight
note
question
important
disagreement
relation
```

O Reader deve informar o tipo da marcação criada.

## Decisão arquitetural

Separar:

```text
PostgreSQL
    = o que existe e qual é o estado atual

TimescaleDB
    = quando as coisas aconteceram
```

Portanto:

- `Annotation.created_at` continua sendo o timestamp canônico da criação da marcação;
- `ReadingSession.started_at` registra quando a leitura começou;
- `reader_events.time` registra a ocorrência temporal dos eventos derivados;
- TimescaleDB não substitui `annotations` nem `reading_sessions`.

## 1. Criar ReadingSession

Adicionar uma entidade de domínio `ReadingSession`.

Campos mínimos:

```text
id uuid
document_id uuid
filename_snapshot text
started_at timestamptz
ended_at timestamptz?
start_page integer?
end_page integer?
last_activity_at timestamptz
created_at timestamptz
updated_at timestamptz
```

### Regras

- a sessão começa quando o Reader realmente inicia a leitura;
- upload do documento não conta como início da leitura;
- `started_at` é obrigatório;
- `ended_at` pode ser nulo enquanto a sessão estiver aberta;
- `filename_snapshot` preserva o nome usado naquela sessão;
- `document_id` é a identidade canônica do documento.

## 2. Associar annotations à sessão

Adicionar em `annotations`:

```text
reading_session_id uuid?
passage_id text
```

`reading_session_id` deve ser opcional para suportar annotations importadas de PDFs antigos.

## 3. Definir passage_id

Não usar apenas coordenadas visuais como identidade do trecho.

O `passage_id` deve ser determinístico e derivado, sempre que possível, de:

```text
document_sha256
page_number
exact_quote_normalized
prefix
suffix
```

Gerar SHA-256 sobre uma representação canônica desses campos.

O algoritmo deve ser explicitamente versionado. Na versão inicial:

```text
passage_id_version = 1
namespace = "notely-passage:v1"
passage_id = sha256(namespace + canonical_anchor)
```

Uma futura alteração no algoritmo deve criar uma nova versão, sem reinterpretar silenciosamente identificadores históricos.

### Separação de responsabilidades

```text
passage_id
    = identidade estável do trecho

position_json
    = reconstrução visual da marcação
```

`position_json` deve continuar armazenando os seletores necessários ao Reader, como:

- página;
- retângulos/quads;
- posição textual;
- exact quote;
- prefix;
- suffix.

## 4. Habilitar TimescaleDB

Adicionar migration para habilitar a extensão TimescaleDB no ambiente suportado.

A migration deve falhar de forma clara se o ambiente não suportar a extensão.

## 5. Criar reader_events

Criar `reader_events` e convertê-la em hypertable.

Campos iniciais:

```text
time timestamptz NOT NULL
event_id uuid NOT NULL
event_type text NOT NULL
session_id uuid?
document_id uuid NOT NULL
annotation_id uuid?
annotation_type text?
passage_id text?
passage_id_version smallint?
page_number integer?
metadata jsonb NOT NULL DEFAULT '{}'
```

### Eventos iniciais

```text
reading.started
reading.ended
annotation.created
annotation.updated
```

Exemplo:

```text
2026-10-02 09:03:12  reading.started
  session_id = ...
  document_id = ...

2026-10-02 09:07:03  annotation.created
  tipo = question
  página = 12
  passage_id = 65f8...
  passage_id_version = 1
  annotation_id = ...
```

## 6. Usar Transactional Outbox

Nenhuma escrita temporal deve sair diretamente do frontend para o TimescaleDB.

Fluxo:

```text
Reader
  ↓
API
  ↓
PostgreSQL transaction
  ├── reading_session / annotation
  └── outbox_event
  ↓
commit
  ↓
worker
  ↓
reader_events / TimescaleDB
```

Eventos de domínio iniciais:

```text
reading.started
reading.ended
annotation.created
annotation.updated
```

## 7. Implementar worker Timescale

Responsabilidades:

- consumir eventos do outbox;
- transformar payload em `reader_events`;
- garantir idempotência por `event_id`;
- suportar retry com backoff;
- registrar falhas;
- não duplicar eventos em reprocessamento.

## 8. Integrar o Reader

No MVP:

- iniciar sessão na primeira interação real após o documento estar pronto;
- enviar `reading_session_id` em novas annotations;
- enviar `annotation_type`;
- enviar dados de anchor suficientes para construir/validar `passage_id`;
- atualizar `last_activity_at`;
- encerrar sessão por ação explícita, fechamento best-effort ou timeout.

O timeout deve ser configurável.

## 9. API mínima

```text
POST  /api/reading-sessions
PATCH /api/reading-sessions/{id}
POST  /api/annotations
```

`POST /api/annotations` deve aceitar:

```text
reading_session_id
annotation_type
page_number
quote
position
anchor data
```

## 10. Migrations

Criar migrations para:

1. habilitar TimescaleDB;
2. criar `reading_sessions`;
3. alterar `annotations`;
4. criar `reader_events`;
5. converter `reader_events` em hypertable;
6. criar índices e constraints;
7. fornecer rollback correspondente.

## 11. Testes

### Domínio

- criação de `ReadingSession`;
- `started_at` obrigatório;
- annotation com e sem sessão;
- geração determinística de `passage_id`;
- persistência de `passage_id_version`;
- compatibilidade entre identificadores históricos e futuras versões do algoritmo;
- mudança de coordenadas sem mudança indevida de `passage_id`, quando o anchor textual permanece igual.

### Integração

- sessão + outbox na mesma transação;
- annotation + outbox na mesma transação;
- outbox → TimescaleDB;
- retry sem duplicação;
- annotation importada sem sessão;
- fechamento e reabertura do PDF preservando identificação do trecho.

### E2E

```text
abrir PDF
  ↓
iniciar leitura
  ↓
criar question
  ↓
persistir annotation
  ↓
gerar evento temporal
  ↓
reabrir PDF
  ↓
question continua no mesmo trecho
```

## 12. Critérios de aceite

- [ ] TimescaleDB está habilitado no ambiente de desenvolvimento.
- [ ] Existe `reading_sessions` no PostgreSQL.
- [ ] Existe `reader_events` como hypertable.
- [ ] Iniciar leitura cria sessão com `started_at`.
- [ ] O nome do arquivo fica registrado como snapshot da sessão.
- [ ] `reader_events` não duplica o nome do arquivo; o contexto histórico é resolvido por `session_id`.
- [ ] Criar uma marcação registra `created_at`, tipo, página, `passage_id` e `passage_id_version`.
- [ ] A annotation aponta para a sessão quando aplicável.
- [ ] `annotation.created` chega ao TimescaleDB via Outbox.
- [ ] O evento temporal referencia a annotation original.
- [ ] Retry do worker não duplica eventos.
- [ ] Annotation importada sem sessão continua válida.
- [ ] Fechar e reabrir o PDF preserva as marcações e seus identificadores.
- [ ] Existem testes de integração PostgreSQL + Outbox + TimescaleDB.

## 13. Fora do escopo

Telemetria de navegação fica fora desta entrega e será tratada em um plano separado em `future/telemetria-navegacao-leitura.md`.

Não implementar nesta entrega:

- `page.viewed`, `reading.paused` e `reading.resumed`;
- cálculo sofisticado de tempo líquido de leitura;
- dashboards;
- heatmaps;
- ranking de documentos;
- análise de produtividade;
- AGE;
- embeddings;
- mind maps.

## 14. Riscos

### Sessão superestimada

Abrir um PDF não prova que houve leitura ativa.

Mitigação: iniciar na primeira interação real e manter `last_activity_at`.

### passage_id frágil

Coordenadas visuais mudam com zoom, viewport e renderização.

Mitigação: identidade textual estável + seletores visuais separados.

### Duplicidade temporal

Retry do worker pode criar eventos repetidos.

Mitigação: `event_id` único e consumer idempotente.

### Filename divergente

O arquivo pode ser renomeado no futuro.

Mitigação: `reading_sessions.filename_snapshot` registra o contexto histórico; `document_id` mantém a identidade canônica. O filename não é repetido em `reader_events`.

### Evolução do passage_id

O algoritmo de anchoring pode melhorar no futuro.

Mitigação: persistir `passage_id_version` e tratar cada versão como contrato imutável.

## 15. Ordem recomendada de implementação

```text
1. domínio ReadingSession + passage_id
2. migrations PostgreSQL
3. migration TimescaleDB
4. API de sessão
5. annotation vinculada à sessão
6. eventos Outbox
7. worker Timescale
8. integração Reader
9. testes
10. atualização do SPECTEC
```

## Decisão final

Persistir a sessão como entidade de domínio no PostgreSQL e a trilha temporal no TimescaleDB.

A annotation continua sendo a representação principal da marcação. O TimescaleDB registra quando ela aconteceu, em qual sessão, documento e trecho, sem se tornar a fonte primária do dado.

O nome do arquivo fica preservado em `reading_sessions.filename_snapshot`. Eventos temporais referenciam a sessão e o documento, evitando duplicar o filename em cada linha do TimescaleDB.

## 16. Registro de execução

Implementado em 2026-10-02. Ordem seguida conforme a seção 15.

### Entregue

```text
1. domínio ReadingSession + passage_id      backend/src/notely/core/{models,passage,services}.py
2. migrations PostgreSQL                    0003: reading_sessions, annotations, reader_events
3. migration TimescaleDB                    extensão + hypertable na mesma migration 0003
4. API de sessão                            POST /api/reading-sessions, PATCH /api/reading-sessions/{id}
5. annotation vinculada à sessão            POST /api/annotations aceita reading_session_id e anchor
6. eventos Outbox                           reading.started, reading.ended, annotation.created
7. worker Timescale                         backend/src/notely/workers/timescale.py
8. integração Reader                        frontend/src/lib/useReadingSession.ts
9. testes                                   unitários, integração real Postgres+Outbox+Timescale, E2E
10. SPECTEC                                 seções 5, 7, 19.5, 23 e 24 atualizadas
```

### Decisões tomadas durante a execução

- **Formato canônico do `passage_id`** explicitado: componentes unidos por `\n`
  (`namespace`, `document_sha256`, `page_number`, `exact_quote_normalized`, `prefix`, `suffix`).
  O backfill em SQL e o cálculo no domínio são conferidos por teste de integração, então os
  dois caminhos não podem divergir.
- **Claim restrito por tipo de evento**: o worker reivindica apenas eventos temporais. Sem
  isso, eventos de outros consumidores (`document.created`, `study_session.*`) ficariam
  pendentes ocupando o lote indefinidamente e poderiam travar eventos novos.
- **Payloads anteriores a esta entrega**: eventos `annotation.created` gravados antes da
  migration não carregam `occurred_at`, `passage_id` nem `reading_session_id`. Em vez de
  descartá-los, o worker completa o payload lendo a `Annotation` original, e usa o
  `created_at` do próprio outbox como hora do fato. O tempo da trilha continua sendo a hora
  da marcação, não a hora da projeção.
- **`annotation.updated`**: o tipo é aceito e projetado pelo worker porque o plano o lista
  como evento inicial, mas nada o emite ainda — não existe endpoint de atualização de
  anotação, e criar um está fora do escopo desta entrega.
- **Timeout de leitura**: configurável no cliente por `VITE_READING_IDLE_TIMEOUT_MS`
  (padrão 20 min) e `VITE_READING_TOUCH_INTERVAL_MS` (padrão 30 s de throttle do
  `last_activity_at`). O encerramento best-effort usa `fetch` com `keepalive` no `pagehide`
  e em `visibilitychange`, e também ocorre ao fechar a aba do documento ou a sessão de estudo.
- **Relação com `StudySession`**: `ReadingSession` registra a leitura de um documento e
  `StudySession` agrupa documentos por tema; o plano é anterior à `StudySession`, então as
  duas entidades ficam independentes nesta entrega. Um vínculo opcional entre elas não foi
  criado, para não antecipar escopo.
- **Ambiente**: `compose.yaml` passou a usar `timescale/timescaledb:2.30.2-pg17` com
  `shared_preload_libraries=timescaledb` explícito. A migration falha com mensagem orientando
  o requisito quando a extensão não está disponível.
- **Testes de integração**: rodam contra um banco dedicado `notely_test` e são pulados quando
  ele não existe, para que a suíte continue executável em qualquer ambiente. O comando de
  criação está no `backend/README.md`.

### Evidências

```text
backend: 61 testes passando (inclui 6 de integração com PostgreSQL + Outbox + TimescaleDB)
frontend: 15 testes passando, build sem erros
worker CLI no banco de desenvolvimento:
  claimed: 4, projected: 4, failed: 0
trilha resultante (reader_events):
  reading.started    página 6
  annotation.created tipo question, página 6, passage_id + version 1, session_id e annotation_id
  reading.ended      página 9
  annotation.created de marcação anterior ao corte, projetada com o passage_id do backfill
navegador (build de produção, Chromium headless):
  upload do PDF: nenhuma chamada a /api/reading-sessions
  primeira seleção de texto: POST /api/reading-sessions {document_id, page_number: 1, filename}
  anotação: POST /api/annotations com reading_session_id e textQuoteSelector
  fechar a aba do documento: PATCH /api/reading-sessions/{id} {ended: true}
```

### Correções após a primeira validação

- **Navegação de páginas no Reader**: o estado de página era gravado por `document_id` e
  lido por id do vínculo da sessão (`activeDocument.id`), então a página nunca mudava. O
  sintoma aparecia em qualquer leitura de sessão, com ou sem anotação. Corrigido em
  `frontend/src/App.tsx`, com testes de regressão (navegar entre páginas, manter a página por
  documento e continuar navegando depois de salvar uma anotação) e verificação em navegador
  real: `1 / 3 → 2 / 3 → 3 / 3 → 2 / 3`, com a marcação criada no meio.

### Critérios de aceite

- [x] TimescaleDB está habilitado no ambiente de desenvolvimento.
- [x] Existe `reading_sessions` no PostgreSQL.
- [x] Existe `reader_events` como hypertable.
- [x] Iniciar leitura cria sessão com `started_at`.
- [x] O nome do arquivo fica registrado como snapshot da sessão.
- [x] `reader_events` não duplica o nome do arquivo; o contexto histórico é resolvido por `session_id`.
- [x] Criar uma marcação registra `created_at`, tipo, página, `passage_id` e `passage_id_version`.
- [x] A annotation aponta para a sessão quando aplicável.
- [x] `annotation.created` chega ao TimescaleDB via Outbox.
- [x] O evento temporal referencia a annotation original.
- [x] Retry do worker não duplica eventos.
- [x] Annotation importada sem sessão continua válida.
- [x] Fechar e reabrir o PDF preserva as marcações e seus identificadores.
- [x] Existem testes de integração PostgreSQL + Outbox + TimescaleDB.
