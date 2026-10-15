# ADR 0008 — Afirmações autorais e arestas semânticas no grafo

**Status:** Aceito
**Data:** 2026-10-15

## Contexto

As fases A e B do grafo projetaram estrutura e inferência: documento → anotação, e anotação → conceito com proveniência explícita. O grafo sabia onde o usuário marcou algo, mas não o que ele **afirma**. A pergunta que motiva esta entrega — "o que eu afirmo, e onde está a evidência?" — exige duas coisas que não existiam no domínio: uma afirmação autoral e arestas que liguem essa afirmação ao material que a sustenta.

Duas restrições do repositório determinam o desenho:

- o Notely é reader-first e a IA não decide pelo usuário: a afirmação nasce do que ele escreveu, e uma sugestão de modelo nunca se apresenta como conhecimento autoral (AGENTS.md §1 e §6);
- PostgreSQL é a fonte de verdade e o AGE é projeção derivada e reconstruível; o worker de grafo é o único escritor no AGE.

O `SPECTEC` §24 registrava os nós `Claim` e as arestas `SUPPORTS`/`CONTRADICTS`/`RELATED_TO`/`ANSWERED_BY` como pendentes.

## Decisão

- **Escopo:** nó `Claim` e as quatro arestas semânticas: `SUPPORTS` e `CONTRADICTS` (anotação → afirmação), `ANSWERED_BY` (dúvida → anotação que responde) e `RELATED_TO` (conceito ↔ conceito). Sem interface no Reader nesta entrega.
- **A afirmação é autoral.** `claims` guarda `text`, `normalized_text`, `provenance`, `status`, `provider`, `model`, `confidence`, `created_at`, `updated_at`, `accepted_at` e `deleted_at`. A rota de afirmação grava `user_authored` e `accepted` com `accepted_at`: foi o próprio usuário quem escreveu. O caminho de sugestão por modelo continua previsto no schema (`ai_inference` + `suggested` + `provider` + `model`) e não tem chamada de modelo nesta entrega.
- **Proveniência: duas chegam a estas tabelas.** `RelationProvenance` já tinha `ai_inference` e `user_confirmed` (conceitos) e recebe `user_authored`. `USER_AUTHORED` é o que o usuário **produziu**; `ai_inference` é o que um modelo produziu, e aceitar uma sugestão **não** troca a origem: a linha continua `ai_inference`, com `provider` e `model` preservados, e passa a `accepted`. Por isso `user_confirmed` é recusado pelo domínio nas afirmações — o `CHECK` da `0015` só admite `user_authored` e `ai_inference` —, enquanto continua valendo em `annotation_concepts`, cuja migration o permite. Um `CHECK` amarra `provider`/`model` a `ai_inference`, e o domínio rejeita `provider` em registro autoral, porque aceitá-lo tornaria a proveniência ambígua.
- **Reafirmar não reescreve.** Repetir o mesmo texto normalizado reaproveita a linha existente **sem alterá-la**: texto, proveniência, provedor e modelo ficam como estavam. É o que preserva a origem de uma sugestão aceita e o que garante que o grafo não fique com um texto obsoleto — se nada mudou no PostgreSQL, nada precisa ser reprojetado. Corrigir a redação de uma afirmação é uma ação que esta entrega não tem.
- **Uma tabela para as quatro arestas, com pontas tipadas.** `claim_relations` tem colunas de ponta com chave estrangeira real (`annotation_id`, `claim_id`, `question_annotation_id`, `answer_annotation_id`, `source_concept_id`, `target_concept_id`), em vez de uma referência polimórfica solta. `CHECK`s exigem exatamente duas pontas preenchidas, as pontas certas por tipo e extremos distintos; a exclusão é em cascata, então apagar anotação, afirmação ou conceito remove a aresta que o citava. `UNIQUE NULLS NOT DISTINCT` sobre o par ordenado dá idempotência a repetir a mesma aresta.
- **Reaproveitamento em vez de duplicação.** `UNIQUE (user_id, normalized_text) WHERE deleted_at IS NULL` faz com que afirmar o mesmo texto de novo reaproveite a afirmação e apenas acrescente evidência. A normalização é domínio (`normalize_claim_text`), não API nem adapter. A exclusão é suave, e por isso a unicidade é parcial.
- **Regras de negócio no serviço, não no handler.** A anotação precisa existir **e pertencer ao dono** (documento/anotação de outra conta responde como inexistente); `RELATED_TO` exige dois conceitos do dono; `ANSWERED_BY` exige que a origem seja do tipo `question`. As pontas são validadas na unidade de trabalho, que já é escopada por dono — o escopo vem de `self._user_id`, nunca do chamador.
- **Pipeline assíncrono, sem escrita síncrona no AGE.** A API grava afirmação e aresta e publica o evento no outbox **na mesma transação** (`claim.created`, `claim.relation.created`, `claim.deleted`, `claim.relation.removed`); o worker de grafo consome e projeta com `MERGE`. Nenhuma chamada de modelo acontece nesta entrega.
- **A projeção da aresta garante as pontas.** O nó `Claim` é `MERGE`ado antes de qualquer aresta que o cite, e a projeção da aresta confirma no PostgreSQL que as pontas existem e não foram removidas. Um `MATCH` sem nó correspondente não escreve aresta parcial: `--rebuild` reconcilia.
- **Remoção.** `DELETE /api/claims/{claim_id}` grava `deleted_at` e publica `claim.deleted`; o worker roda `DETACH DELETE` no nó, que leva as arestas junto. `DELETE /api/claim-relations/{relation_id}` publica as pontas no payload, porque a aresta do AGE não guarda o id do registro — é o par de `source_id` que a identifica.
- **Repetição idempotente não publica evento.** Quando o upsert devolve a linha já existente, nada mudou no PostgreSQL e não há o que projetar. Eventos de fato pontual são deduplicados por agregado + tipo no próprio outbox, então publicar de novo seria rejeitado de qualquer forma; a decisão explícita de não publicar mantém a semântica do outbox legível.
- **Rollback protegido.** O `down` da migration `0015` é bloqueado enquanto existir afirmação ou aresta autoral: afirmação do usuário não é reconstruível a partir de nada.

## Consequências

- O grafo responde "o que eu afirmo, e onde está a evidência?", com a asserção autoral distinguível de inferência no nó e na aresta (`provenance` e `status`).
- Nada existe só no AGE: cada nó carrega `source_id` apontando para o registro no PostgreSQL, e cada aresta é identificada pelo par de `source_id` das pontas mais o rótulo — o mesmo critério das arestas das fases anteriores, que também não guardam um id próprio. O `SPECTEC §9` pede referência à origem em cada projeção; hoje isso vale para os nós, e uniformizar as arestas exige levar o id da relação até a projeção de todas elas.
- `--rebuild` reconstrói afirmações e as quatro arestas a partir do PostgreSQL, ignorando afirmações com `deleted_at`.
- Falha do worker não afeta a afirmação: a entrega fica pendente com backoff, como nas fases anteriores.
- `RELATED_TO` pode poluir o grafo com associações fracas. Por isso a aresta nasce de ação explícita, é removível por rota própria e carrega proveniência autoral.
- Escrever aresta continua sendo ato do worker: nenhuma rota toca o AGE, e a leitura não depende da projeção ter acontecido.
- A afirmação é reaproveitada por texto normalizado, não por significado: variações de redação continuam sendo afirmações distintas, como os conceitos o são sem pgvector.
- Desligar o worker de grafo mantém a projeção desatualizada sem perder nada; `--rebuild` reconcilia.
