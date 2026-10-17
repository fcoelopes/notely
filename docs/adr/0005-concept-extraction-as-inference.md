# ADR 0005 — Conceitos extraídos por modelo como inferência não autoral

**Status:** Aceito
**Data:** 2026-10-08
**Atualização:** 2026-10-17 — o provider de extração passou a ser resolvido por dono da anotação, usando a configuração de modelo do usuário (issue #23).

## Contexto

A fase A do grafo projetou apenas estrutura: documento → anotação. A pergunta que motiva o produto — "onde eu já marquei algo sobre este conceito?" — exige ligar anotações a conceitos, e isso não existe no domínio: não há entidade de conceito nem qualquer provider de extração.

Duas restrições do repositório determinam o desenho:

- o Notely é reader-first e a IA não decide pelo usuário: inferência de modelo não pode ser confundida com conhecimento autoral, e nada entra no mapa autoral do usuário sem aceite explícito (AGENTS.md §1);
- PostgreSQL é a fonte de verdade e o AGE é projeção derivada e reconstruível, nunca o único lugar de uma informação.

O `SPECTEC` §10 já antecipava a regra: extração automática de conceitos, quando existir, deve ser marcada como inferência, e a promoção de uma relação sugerida a autoral exige ação do usuário.

## Decisão

- **Escopo:** nós `Concept` e as arestas `REFERS_TO` (anotação → conceito) e `QUESTIONS` (dúvida → conceito). Nós `Claim` e as arestas `SUPPORTS`/`CONTRADICTS`/`RELATED_TO`/`ANSWERED_BY` ficam para uma entrega seguinte, porque exigem extração de afirmações, uma capacidade distinta.
- **Persistência em PostgreSQL:** `concepts` (nome e `normalized_name` único) e `annotation_concepts`, com `relation`, `provenance`, `status`, `provider`, `model`, `confidence`, `created_at` e `accepted_at`. Cada relação guarda a referência ao registro de origem e o modelo que a produziu.
- **Proveniência explícita:** toda relação nasce `provenance = ai_inference` e `status = suggested`. A promoção a autoral é um aceite explícito que grava `accepted_at`. Um `CHECK` no banco amarra `status = accepted` a `accepted_at IS NOT NULL`, para que o estado não possa ser mentido. `provider` e `model` registram quem extraiu de fato: com a chave do dono, `ModelConceptExtractionProvider` grava `<provider do dono>:concepts` e o modelo dele, não um rótulo genérico.
- **Provider resolvido por dono da anotação:** `ConceptExtractionProvider` devolve conceitos, não relações — quem decide se a aresta é `refers_to` ou `questions` é o domínio, a partir do tipo da anotação. A precedência é: a **configuração de modelo do dono da anotação** (`ai_model_configs`, a mesma do assistente do ADR 0007, com a chave cifrada decifrada por `NOTELY_AI_SECRET_KEY`) → o provider do **servidor** (`NOTELY_CONCEPT_PROVIDER`, `NOTELY_CONCEPT_MODEL`, `NOTELY_CONCEPT_API_KEY`, `NOTELY_CONCEPT_ENDPOINT`) → o extrator determinístico, sem rede, que mantém o fluxo utilizável offline e testável. É uma configuração, dois usos: a chave que o usuário cadastra para o assistente passa a extrair os conceitos dele, sem um segundo cadastro. A sugestão de tema continua vindo de variáveis de ambiente.
- **Camada de chat compartilhada:** `providers/chat_model.py` concentra `ChatModelProvider` e os adapters OpenAI-compatível e Anthropic. É a mesma camada que a issue #8 generaliza para o assistente ancorado, evitando duas abstrações concorrentes.
- **Pipeline assíncrono em duas etapas:** o consumidor `concepts` recebe `annotation.created` (fan-out do ADR 0004), extrai e persiste no PostgreSQL, e publica `concepts.extracted`; o **worker de grafo permanece o único escritor no AGE** e projeta nós e arestas a partir do estado persistido.
- **Idempotência e custo:** reprocessar a mesma anotação não chama o provider de novo quando já existem relações, e a gravação usa upsert por `normalized_name` mais `ON CONFLICT` na relação. O evento publicado tem chave de deduplicação única por anotação.
- **Adoção do histórico:** a migration do consumidor novo marca os eventos já existentes como entregues, para não disparar extração sobre todo o acervo com custo de modelo. O backfill é explícito (`--backfill` no worker de conceitos).

## Consequências

- O grafo passa a responder "onde eu já marquei algo sobre este conceito", com a inferência distinguível: `provenance` e `status` ficam na aresta do grafo e nas colunas do PostgreSQL.
- Nenhum conceito sugerido se apresenta como conhecimento do usuário. Promover a autoral exige uma ação explícita que ainda não tem interface; o estado já está persistido e é consultável.
- O worker de grafo continua sendo o único escritor no AGE, e a projeção permanece reconstruível: `--rebuild` reproduz conceitos e arestas a partir do PostgreSQL.
- Falha do provider não afeta a anotação: a entrega fica pendente com backoff e pode ser reprocessada.
- O custo por anotação existe quando um provider externo é configurado — o do servidor ou, por BYOK, o do próprio dono. O padrão local continua gratuito, e a extração é idempotente: reprocessar não chama o modelo de novo quando a relação já existe.
- A proveniência rastreia o caminho seguido: cada relação diz se foi extraída pelo modelo do dono, pelo provider do servidor ou pelo heurístico.
- **Degradação declarada:** se a chave do servidor que decifra estiver ausente ou não decifrar a chave do dono, o worker registra um aviso e cai para o provider do servidor ou para o heurístico, em vez de deixar a entrega presa em retry por uma configuração de ambiente. Falha do modelo do dono em si (chave inválida, sem cota, provedor fora) é outra coisa: preserva a anotação e deixa a entrega pendente com backoff, como já era o comportamento do consumidor `concepts`.
- Reextrair com outro provider republica o evento de projeção, mas a atualização do grafo é aditiva: as arestas anteriores da mesma anotação permanecem até um `--rebuild`, que reconcilia o grafo com o PostgreSQL. Reconciliar por anotação a cada extração é um caminho possível, não implementado aqui.
- A busca semântica por similaridade (pgvector) continua pendente: hoje o agrupamento por conceito é exato, pela chave normalizada, então variações como "ativos" e "assets" permanecem distintas.
