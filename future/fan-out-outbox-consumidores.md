# Plano de implementação — fan-out do outbox por consumidor

**Estado:** implementado; aguardando revisão

**Issue:** [#9 — Fan-out do outbox por consumidor](https://github.com/fcoelopes/notely/issues/9)

## Intenção

Corrigir o Transactional Outbox para que **mais de um consumidor** receba o mesmo evento. Hoje o `claim` filtra `processed_at IS NULL` e o `mark_processed` grava `processed_at` na linha compartilhada do evento, o que impõe **um consumidor por `event_type`**. O `SPECTEC` §11 prevê o contrário: quatro projeções independentes a partir do mesmo `annotation.created`.

## Problema

Dois workers passaram a reivindicar os mesmos tipos de evento:

| Worker | Tipos reivindicados |
| --- | --- |
| `corpus` | `document.created`, `document.corpus.rebuild` |
| `timescale` | `reading.started`, `reading.ended`, `annotation.created`, `annotation.updated` |
| `graph` | `document.created`, `annotation.created` |

Como cada evento é entregue a um único consumidor, `document.created` é disputado por `corpus` e `graph`, e `annotation.created` por `timescale` e `graph`. Verificado empiricamente com a base real: depois de registrar um documento e uma anotação, o lote de `timescale` reivindica `annotation.created` e o de `graph` fica apenas com `document.created`; o grafo termina com `Document=1` e `Annotation=0`.

O efeito não é só a projeção de grafo incompleta: quando o worker de grafo vence a corrida, a **trilha de leitura no TimescaleDB** e a **indexação do corpus** perdem o mesmo evento. Trata-se de regressão de funcionalidade existente, não apenas de capacidade nova.

Os testes não capturavam isso porque cada teste de integração executa um único worker.

## Decisão

- Nova tabela `outbox_deliveries`, com chave `(event_id, consumer)`, contendo o estado de entrega por consumidor: `processed_at`, `claimed_at`, `attempt_count`, `available_at` e `last_error`.
- `claim`, `mark_processed` e `mark_failed` passam a exigir o nome do consumidor. O claim materializa a entrega de eventos novos para aquele consumidor e trava as linhas de entrega com `FOR UPDATE SKIP LOCKED`, mantendo o comportamento de retry e backoff, agora por consumidor.
- Cada worker declara um nome estável de consumidor: `timescale`, `corpus`, `curation`, `graph` e, na fase B, `concepts`.
- A migration adota o histórico: todo evento já processado entra como entregue a **todos** os consumidores existentes. Consumidores novos começam do zero e usam backfill explícito (`--rebuild`, `--backfill`), preservando a convenção atual e evitando reprocessar anos de eventos — decisivo quando o consumidor fizer chamada paga a modelo.
- As colunas de estado em `outbox_events` (`processed_at`, `claimed_at`, `attempt_count`, `last_error`) permanecem para diagnóstico e são mantidas em sincronia agregada, mas a decisão de entrega passa a ser por consumidor.

## Persistência e integridade

- Migration com a tabela, a chave primária `(event_id, consumer)`, FK para `outbox_events` com `ON DELETE CASCADE` e um índice parcial para entregas pendentes.
- A adoção do histórico entra na própria migration, dentro da transação.
- Política de rollback: `outbox_deliveries` é estado de processamento derivado do log de eventos; o `down` pode recriá-lo a partir de `outbox_events`, então a revisão não bloqueia por dados. Eventos ainda pendentes voltam a ficar disponíveis para o consumidor histórico.

## Ordem

1. Migration, modelo `OutboxDeliveryRow` e adoção do histórico.
2. Rework de `SqlAlchemyOutboxStore` (claim, mark_processed, mark_failed por consumidor).
3. Atualização dos quatro workers para declarar o consumidor.
4. Teste de regressão: dois consumidores recebem o mesmo evento, com retry e backoff independentes.
5. Atualização dos testes existentes e do head esperado nas migrations de teste.

## Critérios de aceite

- Dois consumidores com o mesmo `event_type` recebem cada um o mesmo evento; a projeção de um não impede a do outro.
- Registrar documento e anotação produz, com `timescale` e `graph` rodando em sequência: trilha temporal completa **e** grafo com `Document=1` e `Annotation=1`.
- Falha de um consumidor não consome o evento para os demais, e o retry de cada consumidor é independente.
- Eventos já processados antes da migration não são reprojetados por nenhum consumidor.
- Reprocessar o mesmo evento por qualquer consumidor permanece idempotente na projeção.
- `uv run pytest` verde, com teste de regressão cobrindo o conflito.

## Riscos e rollback

- É uma mudança de semântica num padrão central: todos os consumidores passam a depender da tabela de entregas. A adoção do histórico precisa estar correta, ou consumidores reprocessam eventos antigos em massa.
- O estado de retry deixa de ser compartilhado: dois consumidores podem falhar de forma independente para o mesmo evento, o que é o comportamento desejado, mas exige revisar mensagens de erro e métricas operacionais.
- Rollback: o `down` remove `outbox_deliveries` e o código anterior volta a decidir por `outbox_events.processed_at`. Como o log de eventos permanece intacto, não há perda de dado autoral.

## Validação da implementação

- `uv run pytest` no backend: 90 testes passaram, incluindo `test_graph_and_timeline_both_receive_the_same_event`, que reproduz o conflito original e falha se a entrega voltar a ser compartilhada.
- Verificação de ponta a ponta na base real: com o documento e a anotação registrados, o lote de `timescale` projeta a trilha temporal e o lote de `graph` reivindica **os dois** eventos e projeta documento e anotação (`Document=1`, `Annotation=1`, `HAS_ANNOTATION=1`). Antes da correção o grafo terminava com `Annotation=0`.
- Migration `0009` aplicada no banco local `notely`: tabela em `public`, 88 entregas adotadas do histórico (22 eventos processados × 4 consumidores) e grafo preservado (7 documentos, 11 anotações, 11 arestas).
- Migrations validadas como `up → down → up` em banco descartável.

### Correção adicional encontrada na validação

A cadeia de migrations criava a tabela `outbox_deliveries` no schema errado, **com a versão registrada como aplicada**. Causa: a `0008` faz `SET search_path = ag_catalog, ...` para o DDL do AGE e o `SET` é estado de sessão; como `create_graph('notely')` cria um schema `notely` e o usuário do banco também é `notely`, o `search_path` default `"$user", public` passou a incluir o schema do grafo, e o DDL não qualificado das revisões seguintes foi para `ag_catalog` ou `notely`. Corrigido em `migration_sql.py` (as migrations fixam `search_path = public`), com a `0008` restaurando o `search_path` ao final e um teste que exige as tabelas de domínio em `public`. Detalhes em [ADR 0004](../docs/adr/0004-outbox-delivery-per-consumer.md).
