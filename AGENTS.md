# AGENTS.md

This file defines the operating rules for any coding agent working in the Notely repository.

## 1. Product intent

Notely is a reader-first knowledge system.

The user reads and decides what is relevant. The system captures annotations, questions, comments and other reading signals, preserves their provenance, enriches them asynchronously, and projects them into semantic and graph representations.

Do not turn Notely into a system that reads or decides for the user by default. Assistance must support the reading workflow rather than replace it.

Notely is reader-first, not reader-only. Full documents may be ingested and indexed for retrieval, but corpus content must not be treated as user-authored knowledge merely because it was ingested.

Mind maps are authored by the user. AI may suggest nodes or edges, but must not silently insert, reorganize, or represent an AI-generated map as the user's map.

## 2. Source of truth and architecture

Preserve these responsibilities:

- PostgreSQL is the source of truth for durable domain data.
- TimescaleDB stores temporal events and reading activity.
- Apache AGE stores graph projections and relationships.
- pgvector supports semantic retrieval and similarity search.
- Transactional Outbox is the required pattern for asynchronous processing triggered by domain changes.
- AGE and pgvector are derived representations. Important information must never exist only in either of them.
- Derived graph and vector state must be rebuildable from PostgreSQL.
- Workers must be idempotent.
- Multimodal, web search and external integrations belong behind provider interfaces.

Do not change these architectural responsibilities silently.

## 3. Repository boundaries

Use the existing structure:

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
```

Responsibilities:

- `frontend/`: Reader interface and client-side interaction.
- `backend/src/notely/api/`: API transport and request/response boundary.
- `backend/src/notely/core/`: domain rules and application behavior.
- `backend/src/notely/db/`: persistence and database-specific adapters.
- `backend/src/notely/workers/`: outbox consumers and asynchronous jobs.
- `backend/src/notely/providers/`: multimodal, search and external service adapters.
- `backend/tests/`: automated tests.
- `infra/db/migrations/`: database migrations.
- `docs/`: product, architecture and technical documentation.

Do not place domain rules inside API handlers, database adapters or provider implementations.

## 4. Before implementing

Before changing code:

1. Read the relevant issue.
2. Read the current README and relevant documentation under `docs/`.
3. Inspect the existing implementation before introducing a new pattern.
4. Identify whether the change affects domain data, temporal events, graph projection, semantic search, workers or external providers.
5. Reuse existing abstractions where they fit.

If the requested change conflicts with the documented architecture, stop and make the conflict explicit before implementing it.

## 5. Implementation principles

Prefer the smallest implementation that satisfies the requirement.

- Avoid premature abstractions.
- Avoid speculative modules, services or infrastructure.
- Keep changes focused and reviewable.
- Do not add dependencies without a concrete need.
- Preserve clear boundaries between domain, persistence and providers.
- Prefer explicit code over hidden framework behavior.
- Keep provenance attached to annotations, claims, relationships and model-generated enrichments.
- Distinguish user-created information from model-generated inference.
- Do not treat AI output as authoritative source data.
- Keep corpus data, user-authored data, derived graph data and AI suggestions distinguishable.
- Persist mind-map nodes, edges and layout as user-owned domain data in PostgreSQL.
- Never auto-accept AI suggestions into a user's mind map.
- Do not use emojis in repository templates, technical documentation, commit messages or generated project text unless explicitly requested.

## 6. Database changes

Any schema change must include a migration.

Database changes must preserve these rules:

- PostgreSQL domain tables remain authoritative.
- TimescaleDB hypertables are for time-oriented event data, not as a replacement for the domain model.
- AGE projections must be reproducible.
- pgvector embeddings must be reproducible and should record the model/version used.
- Outbox writes that correspond to a domain change must occur in the same database transaction as that change.
- Consumers must tolerate retries and duplicate delivery.

Do not make a graph or vector write part of the synchronous user interaction unless the requirement explicitly demands it and the architecture has been reviewed.

## 7. Reader interaction

Reading and annotation must remain responsive.

A user action such as creating a highlight should normally:

1. persist the domain change;
2. create the corresponding outbox event in the same transaction;
3. return control to the Reader;
4. allow workers to perform enrichment asynchronously.

Do not block annotation creation on multimodal inference, embeddings, graph projection or web search.

## 8. Multimodal and research behavior

Multimodal models are assistants to the reading process.

Use them when visual context matters, such as:

- figures;
- charts;
- tables;
- equations;
- diagrams;
- selected page regions;
- scanned content.

Prefer sending the minimum useful visual and textual context rather than the entire document.

Web search or research must remain distinguishable from document-derived context. Preserve source provenance for any external information added to the system.

## 9. Testing and validation

Every implementation must include an appropriate validation path.

At minimum:

- add or update automated tests for changed behavior;
- test failure paths when relevant;
- verify migrations when schema changes occur;
- verify idempotency for workers;
- verify that user data remains usable if asynchronous enrichment fails;
- verify that derived AGE/pgvector state can be rebuilt when those layers are changed.

Do not mark work complete only because the happy path runs locally.

## 10. Issues and pull requests

Use the repository templates under `.github/`.

Pull requests must:

- reference the related issue when one exists;
- explain what changed;
- explain how to test it;
- include evidence when useful;
- state concrete risks and rollback steps;
- mention migrations when applicable;
- identify asynchronous behavior when applicable;
- call out architectural changes explicitly.

Do not hide unrelated cleanup inside a feature or bug-fix PR.

## 11. Commits

Keep commits coherent and scoped.

Preferred commit prefixes:

- `feat:`
- `fix:`
- `refactor:`
- `test:`
- `docs:`
- `chore:`
- `ci:`

Commit messages should describe the change, not the activity performed.

## 12. Documentation

Update documentation whenever behavior, architecture or operational assumptions change.

Use an ADR or equivalent technical note when making a durable architectural decision that changes one of the established boundaries in this file.

Do not modify product intent or core architectural rules only in code.

## 13. Definition of done

A task is complete when:

- the requested behavior is implemented;
- tests and validation pass;
- migrations are present when required;
- asynchronous flows are safe to retry;
- provenance is preserved;
- documentation is updated when necessary;
- the implementation respects repository boundaries;
- no secrets or credentials were introduced;
- the diff contains no unrelated changes.

When there is tension between speed and preservation of user annotations or provenance, preservation of user data takes priority.
