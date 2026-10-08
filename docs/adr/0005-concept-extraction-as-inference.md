# ADR 0005 — Conceitos extraídos por modelo como inferência não autoral

**Status:** Aceito
**Data:** 2026-10-08

## Contexto

A fase A do grafo projetou apenas estrutura: documento → anotação. A pergunta que motiva o produto — "onde eu já marquei algo sobre este conceito?" — exige ligar anotações a conceitos, e isso não existe no domínio: não há entidade de conceito nem qualquer provider de extração.

Duas restrições do repositório determinam o desenho:

- o Notely é reader-first e a IA não decide pelo usuário: inferência de modelo não pode ser confundida com conhecimento autoral, e nada entra no mapa autoral do usuário sem aceite explícito (AGENTS.md §1);
- PostgreSQL é a fonte de verdade e o AGE é projeção derivada e reconstruível, nunca o único lugar de uma informação.

O `SPECTEC` §10 já antecipava a regra: extração automática de conceitos, quando existir, deve ser marcada como inferência, e a promoção de uma relação sugerida a autoral exige ação do usuário.

## Decisão

- **Escopo:** nós `Concept` e as arestas `REFERS_TO` (anotação → conceito) e `QUESTIONS` (dúvida → conceito). Nós `Claim` e as arestas `SUPPORTS`/`CONTRADICTS`/`RELATED_TO`/`ANSWERED_BY` ficam para uma entrega seguinte, porque exigem extração de afirmações, uma capacidade distinta.
- **Persistência em PostgreSQL:** `concepts` (nome e `normalized_name` único) e `annotation_concepts`, com `relation`, `provenance`, `status`, `provider`, `model`, `confidence`, `created_at` e `accepted_at`. Cada relação guarda a referência ao registro de origem e o modelo que a produziu.
- **Proveniência explícita:** toda relação nasce `provenance = ai_inference` e `status = suggested`. A promoção a autoral é um aceite explícito que grava `accepted_at`. Um `CHECK` no banco amarra `status = accepted` a `accepted_at IS NOT NULL`, para que o estado não possa ser mentido.
- **Provider plugável atrás de interface:** `ConceptExtractionProvider` devolve conceitos, não relações — quem decide se a aresta é `refers_to` ou `questions` é o domínio, a partir do tipo da anotação. O padrão é um extrator determinístico, sem rede, que mantém o fluxo utilizável offline e testável; um adapter de modelo usa a chave do próprio usuário (BYOK).
- **Camada de chat compartilhada:** `providers/chat_model.py` concentra `ChatModelProvider` e os adapters OpenAI-compatível e Anthropic. É a mesma camada que a issue #8 generaliza para o assistente ancorado, evitando duas abstrações concorrentes.
- **Pipeline assíncrono em duas etapas:** o consumidor `concepts` recebe `annotation.created` (fan-out do ADR 0004), extrai e persiste no PostgreSQL, e publica `concepts.extracted`; o **worker de grafo permanece o único escritor no AGE** e projeta nós e arestas a partir do estado persistido.
- **Idempotência e custo:** reprocessar a mesma anotação não chama o provider de novo quando já existem relações, e a gravação usa upsert por `normalized_name` mais `ON CONFLICT` na relação. O evento publicado tem chave de deduplicação única por anotação.
- **Adoção do histórico:** a migration do consumidor novo marca os eventos já existentes como entregues, para não disparar extração sobre todo o acervo com custo de modelo. O backfill é explícito (`--backfill` no worker de conceitos).

## Consequências

- O grafo passa a responder "onde eu já marquei algo sobre este conceito", com a inferência distinguível: `provenance` e `status` ficam na aresta do grafo e nas colunas do PostgreSQL.
- Nenhum conceito sugerido se apresenta como conhecimento do usuário. Promover a autoral exige uma ação explícita que ainda não tem interface; o estado já está persistido e é consultável.
- O worker de grafo continua sendo o único escritor no AGE, e a projeção permanece reconstruível: `--rebuild` reproduz conceitos e arestas a partir do PostgreSQL.
- Falha do provider não afeta a anotação: a entrega fica pendente com backoff e pode ser reprocessada.
- O custo por anotação existe apenas quando um provider externo é configurado; o padrão é local e gratuito.
- Reextrair com outro provider republica o evento de projeção, mas a atualização do grafo é aditiva: as arestas anteriores da mesma anotação permanecem até um `--rebuild`, que reconcilia o grafo com o PostgreSQL. Reconciliar por anotação a cada extração é um caminho possível, não implementado aqui.
- A busca semântica por similaridade (pgvector) continua pendente: hoje o agrupamento por conceito é exato, pela chave normalizada, então variações como "ativos" e "assets" permanecem distintas.
