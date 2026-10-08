# ADR 0004 — Entrega do outbox por consumidor (fan-out)

**Status:** Aceito
**Data:** 2026-10-08

## Contexto

O `SPECTEC` §11 descreve várias projeções independentes a partir do mesmo `annotation.created`: trilha temporal, embeddings, conceitos e grafo. A implementação do outbox, porém, decidia a entrega por `outbox_events.processed_at`, o que impõe **um consumidor por `event_type`**.

Enquanto cada tipo de evento tinha um único consumidor, a diferença não aparecia. Com a entrada do worker de grafo passaram a existir dois donos para `document.created` (`corpus` e `graph`) e para `annotation.created` (`timescale` e `graph`). Verificado empiricamente com a base real: o lote de `timescale` reivindicava `annotation.created` e o de `graph` ficava apenas com `document.created`, deixando o grafo com `Document=1` e `Annotation=0`. Na corrida inversa, quem perdia era a trilha de leitura e a indexação do corpus — regressão de funcionalidade existente, não só projeção nova incompleta.

Na correção apareceu uma segunda armadilha, independente da primeira. A migration `0008` executa `SET search_path = ag_catalog, "$user", public` para o DDL do AGE. Como `SET` é **estado de sessão**, e a cadeia de migrations usa uma única conexão, o DDL não qualificado das revisões seguintes passou a resolver para o primeiro schema existente do `search_path`. O agravante: `create_graph('notely')` cria um schema `notely`, e o usuário do banco também é `notely`, então o `search_path` default `"$user", public` passou a incluir o schema do grafo. A `0009` criou a tabela em `ag_catalog` ou em `notely` conforme o estado do `search_path`, **com a versão registrada como aplicada** — uma falha silenciosa que atingiria toda migration futura.

## Decisão

- Nova tabela `outbox_deliveries`, com chave primária `(event_id, consumer)`, contendo `processed_at`, `claimed_at`, `attempt_count`, `available_at` e `last_error`. A decisão de entrega sai de `outbox_events` e passa a ser por consumidor.
- `claim`, `mark_processed` e `mark_failed` exigem o nome do consumidor. O claim materializa a entrega pendente do consumidor e trava **somente as linhas de entrega** (`FOR UPDATE SKIP LOCKED` sobre `outbox_deliveries`), permitindo que dois consumidores processem o mesmo evento em paralelo, com retry e backoff independentes.
- Nomes de consumidor centralizados em `db/outbox.py`, para que a migration de adoção do histórico e os workers não divirjam.
- A migration adota o histórico: eventos já processados entram como entregues a **todos** os consumidores existentes. Um consumidor novo precisa ser adotado pela migration que o introduz; caso contrário reprojetaria o acervo inteiro — custo relevante quando o consumidor chama modelo pago.
- As colunas de estado em `outbox_events` (`processed_at`, `claimed_at`, `attempt_count`, `last_error`) permanecem apenas como histórico anterior ao fan-out e não são mais lidas nem escritas.
- Migrations deixam de depender do `search_path` ambiente: `execute_sql` fixa `search_path = public` antes de aplicar cada arquivo, e revisões que precisam mexer no `search_path` (a `0008`) o restauram ao final do bloco autocommit.
- `env.py` passa a usar `transaction_per_migration=True`, configuração recomendada pelo Alembic quando existem migrations com `autocommit_block` — o bloco commita incondicionalmente a transação corrente.

## Consequências

- Dois ou mais consumidores recebem o mesmo evento. Medido de ponta a ponta: o lote de TimescaleDB projeta a trilha temporal e o lote de grafo projeta o documento **e** a anotação a partir dos mesmos eventos, na mesma execução.
- Regressão coberta por `test_graph_and_timeline_both_receive_the_same_event`, que falha se a entrega voltar a ser compartilhada.
- O vazamento de schema é coberto por teste em `test_dev_migrations.py`, que exige as tabelas de domínio em `public`.
- O retry deixa de ser compartilhado: um consumidor pode falhar repetidamente sem consumir nem atrasar o evento para os demais.
- Custo: uma tabela a mais e uma materialização por claim. O backfill do corpus (`enqueue_missing_documents`) passou a considerar pendente também o evento que ainda não tem entrega materializada, preservando a idempotência do enfileiramento.
- A fase B acrescenta o consumidor `concepts`; a migration que o introduzir deve adotar o histórico para ele.
