# ADR 0003 — Projeção de grafo no Apache AGE (primeira entrega)

**Status:** Aceito
**Data:** 2026-10-07

## Contexto

O PRD, o `SPECTEC` e o README definem o Apache AGE como a projeção derivada do conhecimento, mas até este corte nada estava implementado: a Fase 2 do `SPECTEC` registrava explicitamente "pgvector e AGE seguem pendentes". O conhecimento do usuário vivia apenas em tabelas relacionais.

As restrições do repositório determinam o desenho: AGE é representação derivada e reconstruível, PostgreSQL é a fonte de verdade, nenhuma informação essencial pode existir somente no grafo, e a projeção não pode participar do caminho crítico da leitura — ela é acionada pelo Transactional Outbox e processada por worker idempotente.

Três restrições técnicas foram descobertas na implementação:

- Não existe imagem oficial que combine TimescaleDB e AGE. A imagem `timescale/timescaledb` é Alpine (musl), então o `age.so` pré-compilado do `apache/age` (glibc/Debian) não carrega; o AGE precisa ser compilado na própria imagem.
- A extensão exige `shared_preload_libraries`, então a lista de preload passa a ser `timescaledb,age`.
- A função do AGE tem a assinatura `cypher(graph_name name, query_string cstring, params agtype)`. O segundo argumento é `cstring`, o que obriga a consulta Cypher a ser um literal dollar-quoted no SQL, e o terceiro precisa ser um bind parameter — nenhuma das duas coisas é intercambiável.

## Decisão

- **Escopo da fase A:** nós `Document`, `Annotation` e `Question` (quando `annotation.type = question`), e a aresta `HAS_ANNOTATION`. Nós `Concept`/`Claim`/`ExternalSource` e as arestas `REFERS_TO`, `QUESTIONS`, `SUPPORTS`, `CONTRADICTS`, `RELATED_TO` e `ANSWERED_BY` ficam para uma fase posterior, que depende de um provider de extração de conceitos ainda inexistente. A Fase A entrega o AGE funcionando de ponta a ponta sem antecipar enriquecimento por modelo.
- **Imagem própria** em `infra/db/Dockerfile`, a partir de `timescale/timescaledb:2.30.2-pg17`, compilando AGE 1.7.0 (tag `PG17/v1.7.0-rc0`) na mesma base Alpine. O bitcode LLVM é desativado (`with_llvm=no`) porque a imagem do PostgreSQL não traz `clang` e a projeção não depende de JIT.
- **Migration `0008`** cria a extensão e o grafo (`create_graph`, `create_vlabel`, `create_elabel`). Todo o DDL do AGE roda em autocommit, porque `create_graph` e a criação de labels não executam dentro de bloco de transação, e as funções dependem de `ag_catalog` no `search_path`. O `search_path` é restaurado ao final do bloco: ele é estado de sessão, e as revisões seguintes ficariam com o DDL não qualificado desviado para o schema do grafo — ver [ADR 0004](0004-outbox-delivery-per-consumer.md). O `down` remove o grafo e a extensão; como projeção reconstruível, a revisão não bloqueia rollback por dados.
- **Idempotência por `source_id`.** Todo nó e aresta guarda `source_id` (o UUID do registro de origem) e o usa como chave do `MERGE`. Arestas ligam as referências dos dois registros de origem. Reprocessar o mesmo evento não cria duplicata.
- **Worker dedicado** (`workers/graph.py`), espelhando o worker de TimescaleDB: reivindica apenas `document.created` e `annotation.created`, lê o estado autoral do PostgreSQL como fonte dos dados projetados e usa backoff com `attempt_count`. `--rebuild` trunca o grafo e reprojeta documentos e anotações não removidas.
- **Adapter isolado** (`db/graph.py`): monta a consulta Cypher apenas com constantes do módulo e passa todos os valores de domínio por bind parameter, nunca por interpolação. Define `search_path` para `ag_catalog`, exigência do AGE para resolver `cypher` e `agtype`.

## Consequências

- O grafo é reconstruível: apagar o grafo e rodar `--rebuild` reproduz o estado a partir do PostgreSQL. O teste de integração cobre projeção, escolha de label, idempotência sob retry e rebuild completo.
- Nenhum dado essencial vive no AGE; o grafo pode ser descartado a qualquer momento sem perda do dado autoral.
- O worker de grafo é apenas mais um consumidor do outbox: eventos destinados a outros consumidores permanecem pendentes para quem é responsável por eles.
- Dados anteriores a esta entrega não têm evento de grafo pendente no outbox (já consumidos pelos workers existentes), então a projeção inicial do acervo existente exige `--rebuild`. Novas anotações passam a ser projetadas pelo worker em execução.
- Limitação conhecida: não existem eventos `annotation.updated` nem `annotation.deleted` hoje, porque editar e excluir anotações ainda não é um fluxo do produto. Quando existirem, precisarão projetar remoção. Até lá, o `--rebuild` é a rede de segurança contra descompasso.
- A imagem de desenvolvimento é maior e o primeiro build compila o AGE (cerca de 30 s), depois cacheado. A criação da extensão falha de forma explícita se a imagem não trouxer o AGE, do mesmo modo que a `0003` falha sem TimescaleDB.
