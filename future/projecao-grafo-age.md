# Plano de implementação — projeção de grafo no Apache AGE (primeira entrega)

**Estado:** fase A implementada; aguardando revisão

**Issue:** [#6 — Projeção de grafo no Apache AGE (primeira entrega)](https://github.com/fcoelopes/notely/issues/6)

## Intenção

Ligar o Apache AGE como representação derivada e reconstruível do conhecimento, começando pela projeção estrutural: nós `Document`, `Annotation` (e `Question`) e a aresta `HAS_ANNOTATION`. O objetivo desta primeira entrega é deixar o AGE funcionando de ponta a ponta — alimentado pelo outbox, idempotente e reconstruível — sem ainda "interligar conceitos", o que depende de entidades e providers que não existem hoje.

## Escopo desta entrega (fase A)

- Nós: `Document`, `Annotation`, `Question` (subtipo derivado de `annotation.type == question`).
- Aresta: `Document -[HAS_ANNOTATION]-> Annotation | Question`.
- Eventos consumidos: `document.created` e `annotation.created` (já emitidos em `core/services.py`).
- Fora desta entrega: `Concept`, `Claim`, `ExternalSource` e as arestas `REFERS_TO`, `QUESTIONS`, `SUPPORTS`, `CONTRADICTS`, `RELATED_TO`, `ANSWERED_BY`. Isso é a fase B, com plano próprio (ver "Fora de escopo").
- Endpoint público de leitura do grafo é opcional e pode entrar junto ou depois.

## Decisões de arquitetura

- AGE permanece projeção derivada; PostgreSQL continua fonte de verdade. Nenhuma informação essencial existe somente no AGE (AGENTS.md §2).
- Cada nó e aresta guarda `source_id` (UUID da tabela de origem, usado como chave do `MERGE`) e `source_type`. Arestas guardam a referência aos dois registros de origem.
- A escrita no grafo acontece somente pelo worker, a partir do outbox, nunca na transação síncrona da leitura (AGENTS.md §7 e §8).
- Idempotência via `MERGE` chaveado em `source_id` + caminho de `--rebuild` completo.
- Sem nova dependência Python: AGE é acessado por SQL (`cypher()`), com o `asyncpg` já presente.

## Infraestrutura

- Não existe imagem oficial que combine TimescaleDB e AGE. Criar um Dockerfile custom a partir de `timescale/timescaledb:2.30.2-pg17`, compilando a extensão `age`, com `shared_preload_libraries=timescaledb,age`. Este é o item de maior risco e deve ser validado primeiro.
- Migration `0008`:
  - `CREATE EXTENSION IF NOT EXISTS age;`
  - `LOAD 'age';`
  - `SET search_path = ag_catalog, "$user", public;`
  - `SELECT create_graph('notely');` e criação dos vertex labels (`Document`, `Annotation`, `Question`) e do edge label (`HAS_ANNOTATION`).
  - `create_graph` e a criação de labels não executam dentro de bloco de transação; a revisão Alembic precisa de autocommit (`op.get_context().autocommit_block()`), um desvio do padrão `BEGIN;/COMMIT;` do `migration_sql.py`.
  - Registrar a política de rollback conforme `infra/db/migrations/rollback_policy.py` e `docs/rollback.md`.

## Backend

- `db/graph.py`: adapter AGE com `upsert_node`, `upsert_edge`, `delete_node`, `truncate_graph`, `rebuild` e consulta de vizinhos. Cypher via SQL em `ag_catalog`.
- `workers/graph.py`: espelha `workers/timescale.py`. `PROJECTED_EVENT_TYPES = {"document.created", "annotation.created"}`. `project_event` traduz o payload em nós/aresta; modos `--once`, loop contínuo e `--rebuild` (trunca e reprojeta `documents` + `annotations`).
- `scripts/dev.sh`: adicionar o worker de grafo à lista de processos iniciados.
- (Opcional) `GET /api/graph/...` para vizinhos de um nó, seguindo o `SPECTEC` §Graph.

## Ordem

1. Spike da imagem AGE + TimescaleDB e migration `0008`, com validação manual do grafo.
2. `db/graph.py` e testes de unidade do mapeamento (evento → nós/aresta).
3. `workers/graph.py` (incremental e `--rebuild`).
4. Wiring em `scripts/dev.sh`, testes de integração, documentação e ADR.

## Critérios de aceite

- Criar um documento produz o nó `Document` no grafo.
- Criar um highlight/nota produz `Annotation` (ou `Question`) e a aresta `HAS_ANNOTATION` a partir do `Document`.
- Reprocessar o mesmo evento do outbox não duplica nós nem arestas.
- `--rebuild` reproduz um grafo idêntico ao obtido pelo consumo incremental.
- Apagar o grafo e reconstruí-lo a partir do PostgreSQL restaura tudo; nada essencial vive só no AGE.
- Falha do worker de grafo não afeta a persistência da anotação (o dado primário é salvo antes, na mesma transação que o outbox).
- Migrations `up`/`down` validadas em banco descartável; testes de integração seguem o padrão de `test_integration_timescale.py` (pulados quando o banco com AGE não existe).

## Riscos e rollback

- A imagem combinada TimescaleDB + AGE é o maior risco; validar com um spike antes de qualquer outro trabalho.
- `create_graph`/labels exigem autocommit, divergindo do `migration_sql.py` atual; tratar explicitamente na revisão Alembic.
- Não há eventos `annotation.updated`/`annotation.deleted` hoje (a edição/exclusão de anotação ainda não existe). Quando entrarem, precisarão emitir eventos e projetar remoção; até lá, o `--rebuild` é a rede de segurança contra descompasso.
- Rollback operacional: desligar o worker de grafo; o domínio e a leitura continuam funcionando. O grafo pode ser dropado e reconstruído a qualquer momento sem perda de dado primário.

## Fora de escopo (fase B, plano próprio)

- Nós `Concept`/`Claim`/`ExternalSource` e arestas `REFERS_TO`/`SUPPORTS`/`CONTRADICTS`/`RELATED_TO`/`ANSWERED_BY`.
- Provider de extração/enriquecimento (LLM) e worker de conceitos, com marcação `AI_INFERENCE` e aceite explícito antes de promover sugestão a relação autoral.
- Visualização do grafo no Reader e endpoints ricos de consulta.

## Validação da implementação

- `uv run pytest` no backend: 89 testes passaram, incluindo os quatro novos de `test_integration_graph.py` (projeção de documento e anotação, label `Question`, idempotência sob retry do outbox e rebuild completo a partir do PostgreSQL). `test_dev_migrations.py` foi atualizado para o novo head `0008`.
- Migration `0008` validada como `up → down → up` em banco descartável: o `up` cria extensão, grafo e labels; o `down` remove grafo e extensão; o `up` seguinte restaura. O `down` passa pela política de rollback sem bloqueio, por ser projeção reconstruível.
- Aplicada no banco local `notely` (`0007` → `0008`) com backup automático verificado antes do upgrade.
- `--rebuild` no banco real projetou 18 itens (7 documentos + 11 anotações) e a contagem permaneceu estável (`Document=7`, `Annotation=5`, `Question=6`, `HAS_ANNOTATION=11`) após um lote incremental, confirmando idempotência com dados reais.
- Imagem `notely-postgres:age-1.7.0-pg17` validada por smoke test: `CREATE EXTENSION age`, `create_graph`, `create_vlabel`/`create_elabel` e Cypher `MERGE`/`MATCH` com contagem correta.
