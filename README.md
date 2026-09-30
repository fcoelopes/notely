# Notely

Read. Annotate. Connect.

Notely is a reader-first knowledge system: you read and annotate; the platform preserves those annotations, enriches them asynchronously, and builds a traceable knowledge graph.

## Initial structure

```text
frontend/                  # PDF reader UI
backend/
  src/notely/
    api/                   # HTTP/API boundary
    core/                  # domain and application rules
    db/                    # PostgreSQL/TimescaleDB/AGE/pgvector access
    workers/               # outbox consumers and async processing
    providers/             # multimodal, web search and external adapters
  tests/
infra/
  db/
    migrations/
docs/
```

PostgreSQL is the source of truth. TimescaleDB stores temporal events, Apache AGE projects the knowledge graph, pgvector supports semantic retrieval, and the transactional outbox coordinates asynchronous enrichment.
