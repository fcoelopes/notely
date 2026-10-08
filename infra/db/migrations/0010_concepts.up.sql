BEGIN;

CREATE TABLE concepts (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    normalized_name text NOT NULL UNIQUE CHECK (length(btrim(normalized_name)) > 0),
    created_at timestamptz NOT NULL
);

-- Relação derivada de modelo: proveniência e status são obrigatórios e distinguem
-- inferência de conhecimento autoral. A promoção a autoral exige aceite explícito, então
-- `accepted_at` só existe quando o status é `accepted`.
CREATE TABLE annotation_concepts (
    id uuid PRIMARY KEY,
    annotation_id uuid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    concept_id uuid NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    relation text NOT NULL CHECK (relation IN ('refers_to', 'questions')),
    provenance text NOT NULL CHECK (provenance IN ('ai_inference', 'user_confirmed')),
    status text NOT NULL CHECK (status IN ('suggested', 'accepted', 'rejected')),
    provider text NOT NULL CHECK (length(btrim(provider)) > 0),
    model text NOT NULL CHECK (length(btrim(model)) > 0),
    confidence double precision CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at timestamptz NOT NULL,
    accepted_at timestamptz,
    UNIQUE (annotation_id, concept_id, relation),
    CHECK ((status = 'accepted') = (accepted_at IS NOT NULL))
);

CREATE INDEX annotation_concepts_concept_idx ON annotation_concepts (concept_id);
CREATE INDEX annotation_concepts_annotation_idx ON annotation_concepts (annotation_id);

-- Adoção do histórico para o consumidor novo `concepts`: sem isso ele extrairia conceitos
-- para todo o acervo já existente, com custo de modelo. O backfill é explícito, pelo
-- comando `--backfill` do worker de conceitos.
INSERT INTO outbox_deliveries (event_id, consumer, processed_at, attempt_count, available_at)
SELECT event.id, 'concepts', now(), 0, event.created_at
FROM outbox_events AS event
WHERE event.event_type = 'annotation.created';

COMMIT;
