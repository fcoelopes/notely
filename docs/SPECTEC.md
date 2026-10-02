# SPECTEC — Notely

**Versão:** 0.5  
**Status:** Draft técnico  
**Escopo:** fundação técnica do Notely Reader, persistência, eventos, grafo, busca semântica, multimodal e processamento assíncrono.

## 1. Objetivo técnico

Construir uma arquitetura em que a interação de leitura permaneça simples e responsiva, enquanto enriquecimentos semânticos, projeções de grafo, embeddings, multimodal e pesquisa externa acontecem fora do caminho crítico.

A fonte de verdade deve ser PostgreSQL. TimescaleDB, Apache AGE e pgvector complementam o mesmo domínio com responsabilidades específicas.

O Notely deve suportar ingestão integral do corpus sem confundir corpus indexado com conhecimento confirmado pelo usuário. Mapas mentais são artefatos autorais persistidos em PostgreSQL, não visualizações automáticas do AGE.

## 2. Arquitetura de alto nível

```text
Notely Reader
     │
     ▼
Notely API
     │
     ▼
PostgreSQL
 ├── domínio
 ├── outbox_events
 ├── TimescaleDB / reader_events
 ├── pgvector / embeddings
 └── Apache AGE / graph projection
     │
     ▼
Workers
 ├── event worker
 ├── embedding worker
 ├── graph worker
 ├── multimodal worker
 └── research worker
```

Integrações externas ficam atrás de providers/adapters.

## 3. Estrutura do repositório

```text
frontend/
backend/
  src/notely/
    api/
    core/
    db/
    workers/
    providers/
  tests/
infra/
  db/
    migrations/
docs/
.github/
AGENTS.md
README.md
```

Responsabilidades:

- `frontend/`: Reader e interação cliente;
- `api/`: HTTP e contratos de entrada/saída;
- `core/`: domínio e casos de uso;
- `db/`: persistência e adapters específicos de banco;
- `workers/`: processamento assíncrono;
- `providers/`: multimodal, web search e integrações externas;
- `infra/db/migrations/`: schema versionado;
- `docs/`: PRD, SPECTEC, ADRs e documentação.

## 4. Princípios arquiteturais

1. PostgreSQL é a fonte de verdade.
2. TimescaleDB armazena eventos temporais e atividade.
3. AGE é uma projeção reconstruível do conhecimento.
4. pgvector é uma representação semântica reconstruível.
5. Outbox transacional coordena efeitos assíncronos.
6. Workers devem ser idempotentes.
7. O frontend nunca deve escrever diretamente em AGE, TimescaleDB ou pgvector.
8. IA não participa da transação crítica de persistência da anotação.
9. Proveniência é obrigatória.
10. Providers externos devem ser substituíveis.
11. Conteúdo ingerido não é automaticamente conhecimento do usuário.
12. Mind maps são autorais; IA pode sugerir, mas não alterar mapas sem ação explícita do usuário.

## 5. Modelo de domínio inicial

### Document

Campos mínimos:

```text
id
sha256
title
filename
mime_type
page_count
storage_uri
created_at
updated_at
```

### DocumentContent

Representa o conteúdo extraído do documento para recuperação e contexto.

```text
id
document_id
page_number
chunk_index
text
content_hash
metadata_json
created_at
```

### StudySession

Sessão de estudo: documentos lidos em conjunto sobre o mesmo tema.

```text
id
theme?
theme_origin?        # user | ai_suggestion
theme_updated_at?
created_at
updated_at
```

O tema é autoral. `theme_origin` registra se o usuário o escreveu ou se ele veio de uma sugestão aceita explicitamente.

### StudySessionDocument

```text
id
study_session_id
document_id
position
added_at
```

A ligação não duplica conteúdo: o mesmo documento pode participar de várias sessões, porque a ingestão deduplica por `sha256`.

### MindMap

```text
id
title
description?
created_at
updated_at
```

### StudySession

Sessão de estudo: documentos lidos em conjunto sobre o mesmo tema.

```text
id
theme?
theme_origin?        # user | ai_suggestion
theme_updated_at?
created_at
updated_at
```

O tema é autoral. `theme_origin` registra se o usuário o escreveu ou se ele veio de uma sugestão aceita explicitamente.

### StudySessionDocument

```text
id
study_session_id
document_id
position
added_at
```

A ligação não duplica conteúdo: o mesmo documento pode participar de várias sessões, porque a ingestão deduplica por `sha256`.

### MindMapNode

```text
id
mind_map_id
label
node_type
x
y
style_json?
created_at
updated_at
```

`node_type` pode representar `free_text`, `document_ref`, `annotation_ref`, `question_ref`, `concept_ref`, `claim_ref` ou `external_source_ref`.

### StudySession

Sessão de estudo: documentos lidos em conjunto sobre o mesmo tema.

```text
id
theme?
theme_origin?        # user | ai_suggestion
theme_updated_at?
created_at
updated_at
```

O tema é autoral. `theme_origin` registra se o usuário o escreveu ou se ele veio de uma sugestão aceita explicitamente.

### StudySessionDocument

```text
id
study_session_id
document_id
position
added_at
```

A ligação não duplica conteúdo: o mesmo documento pode participar de várias sessões, porque a ingestão deduplica por `sha256`.

### MindMapEdge

```text
id
mind_map_id
source_node_id
target_node_id
label?
edge_type?
created_at
updated_at
```

### StudySession

Sessão de estudo: documentos lidos em conjunto sobre o mesmo tema.

```text
id
theme?
theme_origin?        # user | ai_suggestion
theme_updated_at?
created_at
updated_at
```

O tema é autoral. `theme_origin` registra se o usuário o escreveu ou se ele veio de uma sugestão aceita explicitamente.

### StudySessionDocument

```text
id
study_session_id
document_id
position
added_at
```

A ligação não duplica conteúdo: o mesmo documento pode participar de várias sessões, porque a ingestão deduplica por `sha256`.

### MindMapNodeRef

```text
id
mind_map_node_id
ref_type
ref_id
created_at
```

Essa referência liga um nó autoral a entidades existentes sem copiar o conteúdo de origem.

### AISuggestion

Sugestões da IA devem permanecer separadas da autoria do usuário.

```text
id
suggestion_type
subject_type
subject_id
payload_json
provider
model
status
created_at
accepted_at?
rejected_at?
```

`status` deve distinguir ao menos `pending`, `accepted` e `rejected`.

`subject_type`/`subject_id` apontam para o alvo da sugestão. O primeiro uso é `study_session` com `suggestion_type = study_session_theme`: a sugestão fica pendente e só entra no tema da sessão quando o usuário aceita, momento em que `accepted_at` é preenchido.

### Annotation

```text
id
document_id
page_number
type
quote
comment
position_json
source
author_type
created_at
updated_at
deleted_at?
```

Tipos iniciais:

```text
highlight
note
question
important
disagreement
relation
```

### Question

```text
id
annotation_id?
document_id
page_number?
question_text
status
created_at
answered_at?
```

### Inference

```text
id
subject_type
subject_id
provider
model
inference_type
content_json
provenance_json
created_at
```

### ExternalSource

```text
id
url
title
provider
retrieved_at
metadata_json
```

## 6. Transactional Outbox

Toda mutação de domínio que exigir processamento assíncrono deve gravar o evento correspondente na mesma transação.

Exemplo:

```sql
BEGIN;

INSERT INTO annotations (...);

INSERT INTO outbox_events (
  aggregate_type,
  aggregate_id,
  event_type,
  payload,
  created_at
) VALUES (...);

COMMIT;
```

### outbox_events

Campos sugeridos:

```text
id uuid
aggregate_type text
aggregate_id uuid
event_type text
payload jsonb
created_at timestamptz
available_at timestamptz
claimed_at timestamptz?
processed_at timestamptz?
attempt_count integer
last_error text?
```

### Requisitos

- claim seguro entre workers concorrentes;
- retry com backoff;
- idempotência;
- dead-letter ou estado terminal após limite configurável;
- observabilidade de fila;
- reprocessamento administrativo futuro.

`outbox_events` não substitui `reader_events`.

## 7. TimescaleDB

A tabela/hypertable `reader_events` representa a trilha temporal da atividade.

Exemplos:

```text
document.opened
page.viewed
annotation.created
annotation.updated
question.created
multimodal.requested
multimodal.completed
research.requested
research.completed
graph.projected
embedding.generated
```

Campos sugeridos:

```text
time timestamptz
event_id uuid
event_type text
user_id uuid?
document_id uuid?
annotation_id uuid?
page_number integer?
metadata jsonb
```

TimescaleDB não deve ser usado como substituto das tabelas de domínio.

## 8. pgvector

Embeddings devem ficar em estrutura própria, versionada por provider/modelo.

Exemplo:

```text
embedding_records
- id
- subject_type
- subject_id
- provider
- model
- dimensions
- content_hash
- embedding vector(...)
- created_at
```

Regras:

- não acoplar o domínio a uma dimensão fixa;
- registrar modelo e versão;
- recalcular embeddings sem alterar o dado primário;
- usar hash do conteúdo para evitar recomputação desnecessária;
- resultados semânticos não viram relações explícitas automaticamente sem uma regra definida.

## 9. Apache AGE

AGE deve armazenar projeções de nós e relações derivadas do domínio.

Nós iniciais:

```text
Document
Annotation
Question
Concept
Claim
ExternalSource
```

Arestas iniciais:

```text
HAS_ANNOTATION
REFERS_TO
QUESTIONS
SUPPORTS
CONTRADICTS
RELATED_TO
ANSWERED_BY
```

Regras:

- nenhuma informação essencial existe somente no AGE;
- cada projeção guarda referência ao registro de origem;
- operações devem ser idempotentes;
- deve existir caminho para rebuild completo do grafo.

## 10. Ingestão do corpus

A ingestão de um PDF deve produzir uma representação pesquisável do conteúdo sem promover automaticamente tudo para o grafo.

Pipeline inicial:

```text
PDF
 │
 ├── hash + metadados
 ├── extração de texto/layout
 ├── páginas
 ├── chunks
 └── assets/figuras quando necessário
        │
        ▼
PostgreSQL
        │
        ├── documents
        └── document_contents
        │
        ▼
outbox
        │
        ├── embeddings → pgvector
        └── eventos → TimescaleDB
```

Regras:

- o documento integral pode ser indexado;
- chunks são corpus recuperável, não `USER_KNOWLEDGE`;
- ingestão não cria automaticamente relações autorais;
- extração automática de conceitos, quando existir, deve ser marcada como inferência;
- o AGE pode representar documentos e relações técnicas necessárias, mas não deve virar um espelho de todos os chunks;
- a promoção de uma relação sugerida para uma relação autoral exige ação do usuário.

## 11. Pipeline de annotation

```text
POST /api/annotations
       │
       ▼
validate command
       │
       ▼
transaction
  ├── insert annotation
  └── insert outbox_event(annotation.created)
       │
       ▼
commit
       │
       ▼
HTTP response
       │
       ▼
workers
  ├── append reader_event
  ├── generate embedding
  ├── extract/enrich concepts
  └── project AGE
```

Falha em qualquer worker não altera o fato de que a annotation já foi salva.

## 12. Multimodal Gateway

Interface conceitual:

```text
MultimodalProvider.analyze(request) -> MultimodalResult
```

Tipos iniciais:

```text
REGION_EXPLAIN
FIGURE_INTERPRET
TABLE_INTERPRET
EQUATION_EXPLAIN
SCAN_EXTRACT
FLATTENED_HIGHLIGHT_DETECT
VISUAL_CONTEXT_ENRICH
QUESTION_ANSWER
```

### MultimodalRequest

```text
request_id
task_type
document_id
page_number
page_image_uri | image_bytes
crop_region?
extracted_text?
selected_quote?
user_question?
nearby_concepts?
```

### MultimodalResult

```text
request_id
provider
model
task_type
answer?
extracted_text?
concepts[]
relations[]
visual_regions[]
confidence?
provenance
raw_metadata
```

O default é não enviar imagem ao modelo para todo highlight textual.

## 13. Provider multimodal

Implementar uma interface estável e providers intercambiáveis.

Configuração exemplo:

```text
MULTIMODAL_PROVIDER=qwen3_vl
MULTIMODAL_MODEL=<modelo>
MULTIMODAL_ENDPOINT=<endpoint>
```

Qwen3-VL deve ser tratado como primeiro candidato de benchmark, não como dependência permanente.

O benchmark deve incluir:

- papers em duas colunas;
- tabelas;
- gráficos;
- diagramas;
- equações;
- scans;
- páginas com highlights;
- perguntas reais de leitura.

Métricas:

- fidelidade;
- OCR;
- compreensão de estrutura;
- groundedness;
- latência;
- VRAM/custo;
- estabilidade da saída estruturada.

## 14. Question Router

Fluxo:

```text
question.created
      │
      ▼
Context Resolver
  ├── quote
  ├── parágrafo
  ├── página
  ├── crop visual
  ├── legenda
  └── conceitos próximos
      │
      ▼
Question Router
  ├── textual local
  ├── multimodal
  └── research/web search
```

Pesquisa externa deve ser explícita no MVP.

## 15. Search/Research Gateway

Interface:

```text
SearchProvider.search(query, context) -> SearchResult[]
```

Cada resultado persistido deve registrar:

```text
url
title
provider
retrieved_at
metadata
```

A resposta deve manter vínculo com a `Question` original.

## 16. Importação de anotações

Pipeline:

```text
PDF
 ├── annotations nativas
 │      ↓
 │   normalize
 │
 ├── text layer
 │      ↓
 │   Reader normal
 │
 └── scan/highlight achatado
        ↓
     render page
        ↓
     multimodal/OCR
        ↓
     normalize
```

Deduplicação deve considerar, quando disponíveis:

- document hash;
- page;
- position;
- quote hash;
- source annotation id.

## 17. Sidecar e portabilidade

Formato inicial:

```text
<document>.annotations.json
```

Exemplo:

```json
{
  "schema_version": "1.0",
  "document_sha256": "...",
  "annotations": []
}
```

O sidecar contém dados primários de annotation. Grafo e embeddings não precisam estar nele.

## 18. API inicial

### Documents

```text
POST   /api/documents
GET    /api/documents/{id}
GET    /api/documents/{id}/annotations
POST   /api/documents/{id}/import-annotations
GET    /api/documents/{id}/export-annotations
```

### Annotations

```text
POST   /api/annotations
GET    /api/annotations/{id}
PATCH  /api/annotations/{id}
DELETE /api/annotations/{id}
```

### Questions

```text
POST   /api/questions
GET    /api/questions/{id}
POST   /api/questions/{id}/answer
POST   /api/questions/{id}/search
```

### Multimodal

```text
POST /api/multimodal/analyze-region
POST /api/multimodal/analyze-page
```

### Graph

```text
GET  /api/graph/annotations/{id}
GET  /api/graph/concepts/{id}
POST /api/graph/relations
```

### Mind maps

```text
POST   /api/mind-maps
GET    /api/mind-maps/{id}
PATCH  /api/mind-maps/{id}
DELETE /api/mind-maps/{id}
POST   /api/mind-maps/{id}/nodes
PATCH  /api/mind-maps/{id}/nodes/{node_id}
DELETE /api/mind-maps/{id}/nodes/{node_id}
POST   /api/mind-maps/{id}/edges
DELETE /api/mind-maps/{id}/edges/{edge_id}
GET    /api/mind-maps/{id}/suggestions
POST   /api/mind-maps/{id}/suggestions/{suggestion_id}/accept
POST   /api/mind-maps/{id}/suggestions/{suggestion_id}/reject
```

## 19. Mapas mentais

O mind map é persistido em PostgreSQL como dado autoral.

Separação obrigatória:

```text
mind_maps / nodes / edges
    = estrutura criada pelo usuário

Apache AGE
    = grafo de conhecimento derivado

ai_suggestions
    = propostas ainda não aceitas
```

O frontend pode consultar AGE e pgvector para apresentar itens candidatos, mas não deve inserir esses candidatos no mapa automaticamente.

Fluxo de sugestão:

```text
mapa aberto
   │
   ├── usuário pede sugestões
   │        ↓
   │   AGE + pgvector + corpus
   │        ↓
   │   ai_suggestions
   │        ↓
   │   usuário aceita/rejeita
   │
   └── somente ACCEPT
            ↓
       cria/edita node ou edge autoral
```

As operações de mapa devem preservar layout, rótulos e relações definidos pelo usuário.

## 20. Atualização do frontend

Para eventos assíncronos, começar com SSE:

```text
annotation.enriched
graph.updated
question.answer.ready
search.completed
multimodal.completed
job.failed
```

WebSocket só deve ser introduzido quando houver necessidade bidirecional persistente real.

## 21. Segurança e privacidade

- não enviar PDF inteiro a provider externo por padrão;
- registrar qual provider recebeu cada conteúdo;
- permitir provider local;
- evitar conteúdo do documento em logs operacionais;
- separar escopo por usuário desde o início se houver multiusuário;
- permitir desativar pesquisa externa por documento/projeto;
- armazenar apenas o crop necessário quando possível;
- nunca armazenar credenciais no repositório.

## 22. Observabilidade

Métricas mínimas:

```text
notely.annotation.persist.latency
notely.annotation.persist.errors
notely.outbox.pending
notely.outbox.processing.duration
notely.outbox.retries
notely.outbox.failed
notely.embedding.duration
notely.embedding.errors
notely.semantic.search.duration
notely.graph.projection.duration
notely.graph.projection.errors
notely.multimodal.duration
notely.multimodal.errors
notely.search.duration
notely.search.errors
notely.import.annotations.detected
notely.import.annotations.duplicates
```

## 23. Testes

### Unitários

- position anchors;
- quote hashing;
- annotation normalization;
- outbox retry/idempotency;
- embedding content hashing;
- graph mapping;
- multimodal request construction;
- provenance validation.

### Integração

- annotation + outbox na mesma transação;
- outbox → TimescaleDB;
- outbox → pgvector;
- outbox → AGE;
- retry sem duplicação;
- VLM falha sem perder annotation;
- AGE falha e reprocessa;
- embedding falha e reprocessa;
- importação de annotation nativa;
- importação visual de highlight achatado.

### E2E

1. abrir PDF;
2. criar highlight;
3. fechar e reabrir;
4. confirmar persistência;
5. selecionar figura;
6. perguntar;
7. receber resposta multimodal;
8. verificar proveniência;
9. consultar relação no grafo.

## 24. Estratégia de implementação

### Fase 1

PDF.js → annotation → PostgreSQL + Outbox → reload.

### Fase 2

Workers → reader_events + pgvector + AGE.

### Fase 3

Seleção visual → provider multimodal → resposta + proveniência.

### Fase 4

Importação de annotations e sidecar.

### Fase 5

Ingestão integral do corpus → chunks → embeddings → busca semântica.

### Fase 6

Mind maps autorais → nós/arestas/layout → referências a documentos e anotações → sugestões separadas.

### Fase 7

Search/Research Gateway.

### Fase 7.5

Sessões de estudo: tema autoral, múltiplos documentos por sessão, biblioteca e download do PDF armazenado, sugestões de tema registradas como `AISuggestion` pendente com aceite explícito. Ver `docs/adr/0002-study-sessions-and-theme-suggestions.md`.

### Fase 8

Adapters opcionais, incluindo Clarc.

## 25. Definition of Done do primeiro MVP

- Notely funciona sem Clarc;
- PDF abre e navega;
- highlights, notas e dúvidas persistem;
- positions são reconstruídas;
- annotation e outbox_event entram na mesma transação;
- workers executam com retry e idempotência;
- reader_events são gerados;
- pgvector suporta embedding versionado e busca semântica;
- AGE recebe projeções reconstruíveis;
- região visual pode ser enviada ao multimodal;
- provider/modelo/proveniência ficam registrados;
- falha de IA não perde dado primário;
- annotation nativa pode ser importada;
- sidecar JSON funciona;
- Web Search possui gateway próprio;
- corpus integral pode ser ingerido e pesquisado sem promoção automática a conhecimento autoral;
- mind maps persistem nós, arestas, layout e referências do usuário;
- sugestões de IA permanecem separadas até aceite explícito;
- testes E2E cobrem leitura, anotação, ingestão, enriquecimento, grafo e mapa autoral.
