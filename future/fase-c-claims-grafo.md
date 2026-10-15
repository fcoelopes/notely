# Plano de implementação — fase C: afirmações e arestas semânticas no grafo

**Estado:** implementado, revisado por auditoria independente e na `main` (`f842a58` e as correções de `6c41f6d`). O recurso **ainda não foi usado fora dos testes**: a tabela `claims` está vazia na base real, porque falta a interface que permitiria afirmar pela leitura.

**Issue:** [#21 — Afirmações e arestas semânticas no grafo (fase C)](https://github.com/fcoelopes/notely/issues/21)

## Intenção

Permitir que o leitor afirme algo a partir do que leu e veja isso ligado ao material que o
sustenta. O `Claim` é conhecimento autoral: a pessoa lê um trecho, afirma uma proposição e
declara se aquele trecho a sustenta ou a contradiz. É o que responde "o que eu afirmo, e
onde está a evidência?".

## Decisões de produto

- **O Claim é autoral.** Nasce de uma anotação escolhida pelo usuário e é gravado com
  proveniência autoral. A IA pode sugerir, nunca inserir: uma sugestão de modelo entra como
  `suggested` e só se torna autoral por aceite explícito (AGENTS.md §1 e §6).
- **Semântica das arestas**, decidida com o usuário nesta curadoria:
  - `SUPPORTS` e `CONTRADICTS`: anotação → Claim. A evidência é sempre o trecho que a pessoa
    leu, e a proveniência da leitura permanece na aresta.
  - `ANSWERED_BY`: anotação de dúvida → anotação que responde. Usa os nós `Question` e
    `Annotation` que já existem.
  - `RELATED_TO`: conceito ↔ conceito. Associação afirmada pela pessoa entre dois conceitos
    que ela já vê no grafo.
- **Escopo desta entrega:** domínio, API e grafo. Sem interface no Reader, como na fase B.
- **Nenhuma chamada de modelo nesta entrega.** O caminho de sugestão por IA fica fora pelo
  mesmo motivo que travou a #8: não há provedor real validado, e implementar sem poder
  exercitar produziria código não verificado. O schema acomoda a sugestão
  (`ai_inference` + `suggested` + `provider` + `model` + `confidence`) e o aceite dela
  (`ai_inference` + `accepted`, com a origem preservada), de modo que acrescentá-lo depois não
  exige migration nova. O que **não** existe é `user_confirmed` nestas tabelas: o `CHECK` da
  `0015` só admite `user_authored` e `ai_inference`, e o domínio recusa o terceiro valor.

## Domínio e persistência

- `claims`: `id`, `user_id`, `text`, `normalized_text`, `provenance`
  (`user_authored` | `ai_inference`), `status` (`suggested` | `accepted` | `rejected`),
  `provider`, `model`, `confidence`, `created_at`, `accepted_at`, `updated_at`, `deleted_at`.
  - `UNIQUE (user_id, normalized_text)`: afirmar a mesma coisa duas vezes reaproveita o clam
    existente e acrescenta evidência, em vez de duplicar conhecimento.
  - `CHECK` exige `provider` e `model` quando a proveniência é `ai_inference`, e
    `(status = 'accepted') = (accepted_at IS NOT NULL)`, como em `annotation_concepts`.
- `claim_relations`: uma tabela para as quatro arestas, com colunas de ponta tipadas e
  chaves estrangeiras reais (sem referência polimórfica solta), e `CHECK` por relação:
  - `supports` | `contradicts`: `annotation_id` (origem) e `claim_id` (destino);
  - `answered_by`: `question_annotation_id` (origem) e `answer_annotation_id` (destino);
  - `related_to`: `source_concept_id` e `target_concept_id`.
  - Cada relação carrega `provenance`, `status`, `provider`, `model`, `confidence`,
    `created_at` e `accepted_at`, com `UNIQUE` no par ordenado para idempotência.
  - Exclusão em cascata: apagar anotação, claim ou conceito remove as arestas que o citam.
- Migration `0015`: as duas tabelas, os rótulos `Claim`, `SUPPORTS`, `CONTRADICTS`,
  `ANSWERED_BY` e `RELATED_TO` no AGE em autocommit, e guard de rollback: o `down` é
  bloqueado enquanto existir Claim autoral, porque afirmação do usuário não é reconstruível
  a partir de nada.
- Nenhuma adoção de histórico é necessária: o consumidor `graph` já existe, então os eventos
  novos criam entregas próprias pelo fan-out (ADR 0004).

## Pipeline

1. A API grava o Claim e a aresta, e publica o evento no outbox **na mesma transação**.
2. O worker de grafo consome o evento e projeta o nó `Claim` e a aresta correspondente.
3. Remover um Claim publica o evento de remoção; o worker retira o nó e as arestas.
4. Falha do worker não afeta o Claim: a projeção é assíncrona e a entrega fica pendente com
   backoff, como nas fases anteriores.
5. `--rebuild` reconstrói o grafo inteiro, incluindo claims e as quatro arestas.

O worker de grafo continua sendo o único escritor no AGE.

## API

Autenticada e escopada por dono, como as demais rotas:

- `POST /api/claims` — afirma a partir de uma anotação: `{annotation_id, text, relation}`,
  com `relation` em `supports` | `contradicts`. Reaproveita o Claim existente quando o texto
  normalizado coincide e apenas acrescenta a evidência.
- `GET /api/claims` — lista os claims do usuário com a evidência: documento, página, trecho e
  a aresta com sua proveniência.
- `DELETE /api/claims/{claim_id}` — remove o Claim e, em cascata, suas arestas.
- `POST /api/questions/{annotation_id}/answers` — liga uma anotação de dúvida à anotação que
  a responde (`ANSWERED_BY`).
- `POST /api/concept-relations` — associa dois conceitos do usuário (`RELATED_TO`).
- `DELETE /api/claim-relations/{relation_id}` — remove qualquer aresta semântica. O payload
  da remoção carrega as pontas, porque a aresta do AGE é identificada pelo par de `source_id`
  e não guarda o id do registro de origem.

A exclusão de um Claim é suave: `DELETE /api/claims/{claim_id}` grava `deleted_at` — a
afirmação sai do que o usuário vê e o `--rebuild` a ignora, mas o conhecimento autoral não é
apagado do banco, como exige o AGENTS.md §14.

## Ordem

1. Migration `0015` com tabelas, rótulos e guard de rollback.
2. Domínio, portas e persistência escopados por dono.
3. API com eventos de outbox na mesma transação.
4. Projeção no worker de grafo, incluindo remoção e `--rebuild`.
5. Testes de integração e de unidade.
6. Documentação: ADR, README do backend e SPECTEC.

## Critérios de aceite

- Afirmar a partir de uma anotação persiste o Claim autoral e produz o nó `Claim` e a aresta
  `SUPPORTS` ou `CONTRADICTS`, ambos distinguíveis de inferência.
- Afirmar o mesmo texto de novo não duplica o Claim e acrescenta a nova evidência.
- `ANSWERED_BY` liga a dúvida à anotação que a responde; `RELATED_TO` liga dois conceitos.
- Nada existe apenas no AGE: cada nó carrega `source_id` do registro de origem, e cada aresta é determinada pelo par de pontas mais o rótulo — o mesmo critério das fases anteriores, cujas arestas também não guardam um id próprio.
- Reprocessar o mesmo evento não duplica nó nem aresta; `--rebuild` reproduz o grafo com as
  quatro arestas a partir do PostgreSQL.
- Remover o Claim retira o nó e as arestas do grafo.
- Claim de outra conta não é lido, alterado nem removido: a rota responde como inexistente.
- Falha do worker não afeta o Claim persistido.
- Migration validada em `up → down → up` em banco descartável, com o `down` recusado enquanto
  existir Claim autoral.
- O grafo deixa de ter qualquer aresta "sem origem no domínio": as quatro novas são
  rastreáveis ao registro que as originou.

## Riscos e rollback

- O risco principal é o grafo virar fonte de verdade por acidente: toda escrita continua no
  PostgreSQL e a API nunca escreve no AGE de forma síncrona.
- `RELATED_TO` entre conceitos pode ser afirmado de forma pouco criteriosa e poluir o grafo
  com associações fracas. Por isso a aresta nasce de ação explícita e é removível.
- Rollback: `alembic downgrade 0014` remove as tabelas e os rótulos, bloqueado enquanto
  houver Claim autoral. Desligar o worker de grafo mantém a projeção desatualizada sem
  perder nada; `--rebuild` reconcilia.

## Fora de escopo

- Interface no Reader para afirmar, listar e remover claims.
- Sugestão de claim por modelo (o schema acomoda, a chamada não entra sem provedor validado).
- Nó `ExternalSource` e as relações de busca externa, que dependem de ingestão por busca web.
- Embeddings e pgvector para recall semântico.

## Validação da implementação

- Suíte do backend: `196 passed, 5 warnings in 89.23s` (`pytest` contra `notely_test` em `0015`), 24 testes novos em relação aos 172 anteriores — 23 desta entrega e 1 de rollback. As 5 advertências são antigas (`DeprecationWarning` do alembic e `PytestReturnNotNoneWarning` em `test_api_auth.py`), e nenhum teste anterior deixou de passar. Depois das correções da revisão a suíte fechou em `199` testes, verde em banco isolado.
- Integração do pipeline de afirmações (`backend/tests/test_integration_claims.py`, 14 testes, PostgreSQL real + AGE): persistência autoral e projeção do nó e da aresta; reaproveitamento ao afirmar o mesmo texto com outra caixa e espaçamento; `CONTRADICTS` como aresta distinta; `ANSWERED_BY` entre dúvida e resposta; `RELATED_TO` entre dois conceitos do dono; idempotência sob reprocessamento das mesmas entregas; `--rebuild` reproduzindo nó e as quatro arestas e ignorando afirmação removida; remoção limpando nó e arestas; remoção de aresta preservando os nós; falha do worker preservando a afirmação com a entrega em backoff; e documento, anotação, conceito, afirmação e aresta de outra conta respondendo como inexistentes pelas rotas.
- Domínio sem banco (`backend/tests/test_claims.py`, 9 testes): normalização, exigência de `provider`/`model` só em inferência, `accepted_at` amarrado ao status, exatamente duas pontas por relação, pontas certas por tipo, extremos distintos e as três proveniências distinguíveis.
- Critérios de aceite verificados por leitura do grafo, não só por contagem: o nó `Claim` é conferido com `source_id`, `text`, `provenance` e `status`, e as arestas com `provenance` e `status` — nada existe apenas no AGE.
- Migration `0015` validada em banco descartável: o `up` cria as duas tabelas e os cinco rótulos, o `down` remove ambos e o `up` seguinte restaura tudo (verificado manualmente no ciclo `up → down → up`). O `down` é recusado com `RuntimeError: Downgrade 0015 would discard: user claims` quando existe afirmação autoral, e procede — removendo tabelas e rótulos — depois que a afirmação é apagada; isso é coberto por `test_downgrade_of_claims_guards_user_authored_rows`.
- `backend/tests/test_dev_migrations.py::test_downgrade_cannot_discard_document_bookmark` foi ajustado para derivar o alvo do histórico: o script de rollback desce um passo por vez, e a migration `0015` acima do marcador `0014` deslocou o alvo que antes era fixo. O ciclo `up → down → up` de `0015` pelo `scripts/migrate.py` não é exercitável no mesmo banco: o `migrate` retoma do último marcador legado aplicado (`0007`) e reaplicaria migrations já executadas, o que falharia nos rótulos do AGE. A verificação é feita por `alembic downgrade`/`upgrade` direto no banco descartável.

## Limitações conhecidas

- Não há interface no Reader: afirmar, listar e remover acontecem por HTTP. O estado está persistido e consultável.
- Nenhuma chamada de modelo nesta entrega. O caminho de sugestão de afirmação (`ai_inference`, `suggested`, `provider`, `model`, `confidence`) está previsto no schema e nos CHECKs, mas não há provider validado que o exercite.
- O reaproveitamento é por texto normalizado, não por significado: "a materialidade é julgada caso a caso" e "o julgamento da materialidade é caso a caso" são afirmações distintas, como os conceitos são sem pgvector.
- `RELATED_TO` é simétrico no significado e não no banco: o par é ordenado, então afirmar a associação na ordem inversa cria outra aresta. Unificar as duas exigiria uma regra de canonização de par, que não entrou aqui.
- A projeção da aresta usa `MATCH` nas pontas e não cria nó incompleto: se o nó da anotação ou do conceito ainda não foi projetado, a aresta só aparece na entrega seguinte ou em `--rebuild`. Reprocessar as entregas do grafo reconcilia.
- Reafirmar o mesmo texto reaproveita a afirmação **sem alterá-la**: texto, proveniência, provedor e modelo ficam como estavam. Como nada muda no PostgreSQL, nenhum evento é publicado e nada precisa ser reprojetado. Consequência: a redação de uma afirmação não pode ser corrigida nesta entrega, e reafirmar com a mesma evidência não republica a aresta — se a projeção daquela aresta falhou antes, a reconciliação é `--rebuild`, como no caso análogo do ADR 0005.
- A afirmação não guarda histórico de edição: `upsert` atualiza `text` e `updated_at` da afirmação existente, e o texto anterior não é preservado.
- As arestas do grafo não guardam o identificador do registro que as originou: a identidade delas é o par de pontas mais o rótulo, como já ocorre nas arestas das fases anteriores. O SPECTEC §9 pede referência ao registro de origem em cada projeção, e hoje isso vale para os nós. Uniformizar exigiria levar o id da relação até a projeção de todas as arestas, incluindo as anteriores a esta entrega.

## Correções após revisão independente

Uma auditoria cética do diff encontrou defeitos reais, corrigidos antes do registro final. O que
mudou em relação à primeira versão:

- **Reafirmar não reescreve mais a afirmação.** O `upsert` usava `ON CONFLICT DO UPDATE`, que
  reescrevia texto, proveniência, provedor e modelo: reafirmar o mesmo texto destruía a origem de
  uma sugestão aceita e, como nenhum evento era publicado, deixava o nó do grafo com o texto
  antigo enquanto o PostgreSQL já mostrava outro. Agora o conflito não altera a linha, e o
  `False` devolvido pelo upsert diz a quem chama que nada precisa ser projetado. O `created_at ==
  now` que inferia INSERT de UPDATE saiu junto. Coberto por
  `test_asserting_the_same_text_reuses_the_claim_and_adds_evidence`, que agora compara as duas
  camadas.
- **Afirmação sem evidência continua alcançável.** `GET /api/claims` só montava entradas dentro
  do laço de arestas, então remover a única evidência escondia a afirmação da listagem — dado
  autoral invisível. Coberto por `test_claim_without_evidence_stays_listed`.
- **Domínio e banco concordam sobre proveniência.** `Claim` e `ClaimRelation` recusam
  `user_confirmed`, que o `CHECK` da `0015` não admite; antes o domínio aceitava e o INSERT
  estourava. Coberto por `test_claims_refuse_the_provenance_the_tables_do_not_store`.
- **A guarda de rollback é por aceite, não por origem.** Antes bloqueava só `user_authored`:
  uma sugestão de modelo que o usuário aceitou seria descartada sem bloqueio, embora seja
  conhecimento dele. Coberto por `test_downgrade_of_claims_guards_accepted_rows`, que também
  verifica que uma sugestão não aceita não bloqueia.
- **A contagem do `--rebuild` só soma o que foi projetado**, e o fixture de
  `test_integration_graph.py` passou a limpar `claims`: a expectativa daquele teste era sensível
  a resíduo de outra execução, o que o tornava dependente da ordem.
- **Reafirmar depois da exclusão suave** ganhou teste
  (`test_asserting_the_same_text_after_removal_creates_a_new_claim`), que é a razão de o índice
  único ser parcial, e `RELATED_TO` passou a afirmar a idempotência no banco, não só a contagem
  do grafo.

Observação de ambiente, útil para quem for rodar a suíte: dois executores concorrentes contra o
mesmo banco de teste se destroem — os fixtures apagam linhas globais e truncam o grafo, e o
sintoma é `Entity could not be locked for updating` do AGE. A verificação final foi feita em
banco isolado.

Achados menores ainda abertos, registrados por honestidade: `AgeGraph.remove_annotation` e
`AgeGraph.count_claims` não têm chamador; `list_claim_relations` recebe `annotation_id` que
nenhum chamador usa; `create_claim`/`answer_question` levantam `DocumentNotFoundError` quando a
anotação não existe, nome que descreve mal o caso; e os fixtures de integração convertem
qualquer erro de preparação em `skip`.
