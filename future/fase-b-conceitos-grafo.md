# Plano de implementação — fase B: conceitos e arestas anotação → conceito

**Estado:** implementado; aguardando revisão

**Issue:** [#10 — Conceitos e arestas anotação → conceito no grafo (fase B)](https://github.com/fcoelopes/notely/issues/10)

## Intenção

Extrair conceitos das anotações do usuário com um modelo plugável e projetá-los no grafo como representação derivada, ligando cada anotação aos conceitos que ela referencia ou questiona. É o que responde "onde eu já marquei algo sobre este conceito?".

## Decisões de produto

- Conceitos extraídos entram no grafo **marcados como inferência** (`ai_inference`), sempre distinguíveis do conhecimento autoral.
- A promoção de uma relação sugerida a relação autoral exige **aceite explícito**; nada é aceito automaticamente (AGENTS.md §1).
- Escopo desta entrega: nós `Concept` e arestas `REFERS_TO` (anotação → conceito) e `QUESTIONS` (dúvida → conceito). Nós `Claim` e as arestas `SUPPORTS`/`CONTRADICTS`/`RELATED_TO`/`ANSWERED_BY` ficam para uma entrega seguinte.
- Provider plugável com chave do próprio usuário (BYOK), como já ocorre no provider de tema.

## Camada de provider

- `providers/chat_model.py`: protocolo `ChatModelProvider` com `complete(...)`, e adapters `OpenAICompatibleChatProvider` e `AnthropicChatProvider` (endpoint/modelo/chave por configuração). O padrão já existe em `providers/topic.py` e é a semente; a issue #8 generaliza o restante (assistente ancorado) sobre esta mesma camada, evitando duas abstrações concorrentes.
- `providers/concept_extraction.py`: protocolo `ConceptExtractionProvider` e duas implementações:
  - `HeuristicConceptExtractionProvider` — determinístico e sem rede, mantém o fluxo utilizável offline e testável;
  - `ModelConceptExtractionProvider` — usa um `ChatModelProvider` e um prompt que exige JSON e proíbe inventar conteúdo ausente da anotação.
- O provider é escolhido por configuração; sem configuração, permanece o heurístico.

## Domínio e persistência

- `concepts`: `id`, `name`, `normalized_name` (único), `created_at`.
- `annotation_concepts`: `annotation_id`, `concept_id`, `relation` (`refers_to` | `questions`), `provenance` (`ai_inference`), `provider`, `model`, `confidence`, `status` (`suggested` | `accepted` | `rejected`), `created_at`, `accepted_at`.
- PostgreSQL continua sendo a fonte de verdade; o AGE é apenas projeção. Cada relação guarda a referência ao registro de origem.
- Migration com as duas tabelas, com os labels do AGE (`Concept`, `REFERS_TO`, `QUESTIONS`) em autocommit, e com a **adoção do histórico** para o consumidor novo `concepts` — sem isso ele reprojetaria todo o acervo, com custo de modelo (ver [ADR 0004](../docs/adr/0004-outbox-delivery-per-consumer.md)).

## Pipeline

1. `annotation.created` é entregue ao consumidor `concepts` pelo fan-out do outbox.
2. O worker extrai conceitos pelo provider, normaliza os nomes, faz upsert em `concepts` e grava as relações como `suggested` com proveniência de inferência.
3. O worker emite `concepts.extracted` no outbox.
4. O worker de grafo consome esse evento e projeta os nós `Concept` e as arestas, carregando `provenance` no nó e na aresta.
5. Falha do provider não afeta a anotação nem a leitura: a entrega fica pendente com backoff e pode ser reprocessada.

O worker de grafo permanece o único escritor no AGE, mantendo a projeção derivada do PostgreSQL.

## Ordem

1. Camada de provider (chat e extração) com testes sem rede.
2. Migration, domínio e adoção do histórico do consumidor `concepts`.
3. Worker de conceitos e evento `concepts.extracted`.
4. Projeção de conceitos no worker de grafo.
5. Testes de integração, documentação e ADR.

## Critérios de aceite

- Criar uma anotação produz conceitos persistidos como `suggested` e com proveniência `ai_inference`, registrando provider e modelo.
- O grafo recebe nós `Concept` e as arestas `REFERS_TO`/`QUESTIONS` marcadas como inferência.
- Reprocessar a extração não duplica conceito nem aresta (normalização + `MERGE`).
- Falha do provider preserva a anotação e deixa a entrega pendente para retry.
- Nenhuma relação é promovida a autoral sem aceite explícito, e o estado permanece distinguível.
- `--rebuild` reproduz o mesmo grafo a partir do PostgreSQL, incluindo os conceitos.

## Riscos e rollback

- Custo: com provider pago, cada anotação consome tokens. O padrão heurístico é local e gratuito, e o adapter externo só é ativado por configuração explícita. O consumidor `concepts` começa do zero graças à adoção do histórico.
- Qualidade: conceitos extraídos podem ser fracos; é exatamente por isso que entram como inferência não autoral, com o usuário decidindo o que promover.
- Rollback: desligar o worker de conceitos interrompe a extração sem afetar anotações; as tabelas e a projeção no grafo são reconstruíveis.

## Fora de escopo

- Nós `Claim` e as demais arestas semânticas.
- Interface de aceite/rejeição de conceitos (o estado fica persistido e consultável; a tela entra depois).
- Embeddings e pgvector para recall semântico.

## Validação da implementação

- `uv run pytest` no backend: 105 testes passaram, incluindo 10 de unidade dos providers (sem rede) e 5 de integração do pipeline de conceitos: extração com proveniência, aresta `QUESTIONS` para dúvidas, idempotência sob reprocessamento, reexecução do backfill e independência da falha do provider em relação à anotação.
- Migration `0010` validada como `up → down → up` em banco descartável: o `down` remove as tabelas e os labels do AGE, e o `up` seguinte restaura ambos. O `down` é bloqueado quando existe relação aceita pelo usuário.
- Verificação na base real: `--backfill` extraiu conceitos para 11 anotações e o grafo passou a ter 44 conceitos, 24 arestas `REFERS_TO` e 30 `QUESTIONS`, todas `suggested`/`ai_inference`. A consulta "onde já marquei algo sobre este conceito?" retorna documento, tipo de anotação, página e a aresta com a proveniência.
- Anotações anteriores foram adotadas no histórico do consumidor `concepts` (11 entregas), de modo que nada foi extraído sem comando explícito.

## Limitações conhecidas

- O extrator heurístico é local e gratuito, mas a qualidade é limitada: ele ranqueia termos por frequência e não distingue conceito de verbo comum. A qualidade vem de configurar um provider de modelo (BYOK); o heurístico existe para manter o fluxo utilizável offline e testável.
- Reextrair com outro provider republica o evento de projeção, mas o grafo é atualizado de forma aditiva: arestas antigas da mesma anotação permanecem até um `--rebuild`, que reconcilia o grafo com o PostgreSQL.
- Não há agrupamento semântico: "ativos", "assets" e "ativo" são conceitos distintos. A fusão por similaridade depende de pgvector, que segue pendente.
- A interface de aceite/rejeição não existe nesta entrega; o estado está persistido e é consultável.
