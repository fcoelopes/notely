BEGIN;

-- Afirmação autoral do leitor: a proposição que ele sustenta a partir do que leu. A evidência
-- não mora aqui, e sim em claim_relations, porque a mesma afirmação pode ser sustentada por
-- vários trechos.
CREATE TABLE claims (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text text NOT NULL CHECK (length(btrim(text)) > 0),
    normalized_text text NOT NULL CHECK (length(btrim(normalized_text)) > 0),
    provenance text NOT NULL CHECK (provenance IN ('user_authored', 'ai_inference')),
    status text NOT NULL CHECK (status IN ('suggested', 'accepted', 'rejected')),
    provider text,
    model text,
    confidence double precision CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    accepted_at timestamptz,
    deleted_at timestamptz,
    -- Sugestão de modelo registra quem sugeriu; afirmação autoral não tem provedor.
    CONSTRAINT claims_inference_source
        CHECK (provenance = 'user_authored' OR (provider IS NOT NULL AND model IS NOT NULL)),
    CONSTRAINT claims_accepted_at CHECK ((status = 'accepted') = (accepted_at IS NOT NULL))
);

CREATE INDEX claims_user_idx ON claims (user_id);

-- Afirmar o mesmo texto de novo reaproveita a afirmação e apenas acrescenta evidência, em vez
-- de duplicar conhecimento. A exclusão é suave, então a unicidade é parcial.
CREATE UNIQUE INDEX claims_user_normalized_idx
    ON claims (user_id, normalized_text)
    WHERE deleted_at IS NULL;

-- Arestas semânticas. Cada relação usa exatamente um par de pontas, com chave estrangeira real
-- em vez de referência polimórfica solta: apagar anotação, afirmação ou conceito remove a
-- aresta que o citava.
CREATE TABLE claim_relations (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relation text NOT NULL CHECK (relation IN ('supports', 'contradicts', 'answered_by', 'related_to')),
    annotation_id uuid REFERENCES annotations(id) ON DELETE CASCADE,
    claim_id uuid REFERENCES claims(id) ON DELETE CASCADE,
    question_annotation_id uuid REFERENCES annotations(id) ON DELETE CASCADE,
    answer_annotation_id uuid REFERENCES annotations(id) ON DELETE CASCADE,
    source_concept_id uuid REFERENCES concepts(id) ON DELETE CASCADE,
    target_concept_id uuid REFERENCES concepts(id) ON DELETE CASCADE,
    provenance text NOT NULL CHECK (provenance IN ('user_authored', 'ai_inference')),
    status text NOT NULL CHECK (status IN ('suggested', 'accepted', 'rejected')),
    provider text,
    model text,
    confidence double precision CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at timestamptz NOT NULL,
    accepted_at timestamptz,
    CONSTRAINT claim_relations_endpoints CHECK (
        num_nonnulls(
            annotation_id, claim_id, question_annotation_id,
            answer_annotation_id, source_concept_id, target_concept_id
        ) = 2
    ),
    CONSTRAINT claim_relations_supports_columns CHECK (
        relation <> 'supports' OR (annotation_id IS NOT NULL AND claim_id IS NOT NULL)
    ),
    CONSTRAINT claim_relations_contradicts_columns CHECK (
        relation <> 'contradicts' OR (annotation_id IS NOT NULL AND claim_id IS NOT NULL)
    ),
    CONSTRAINT claim_relations_answered_by_columns CHECK (
        relation <> 'answered_by'
        OR (question_annotation_id IS NOT NULL AND answer_annotation_id IS NOT NULL)
    ),
    CONSTRAINT claim_relations_related_to_columns CHECK (
        relation <> 'related_to'
        OR (source_concept_id IS NOT NULL AND target_concept_id IS NOT NULL)
    ),
    -- Dúvida não responde a si mesma; conceito não se associa consigo mesmo.
    CONSTRAINT claim_relations_distinct_endpoints CHECK (
        (question_annotation_id IS NULL OR question_annotation_id <> answer_annotation_id)
        AND (source_concept_id IS NULL OR source_concept_id <> target_concept_id)
    ),
    CONSTRAINT claim_relations_inference_source CHECK (
        provenance = 'user_authored' OR (provider IS NOT NULL AND model IS NOT NULL)
    ),
    CONSTRAINT claim_relations_accepted_at CHECK ((status = 'accepted') = (accepted_at IS NOT NULL)),
    -- Idempotência: repetir a mesma aresta não cria linha nova. `NULLS NOT DISTINCT` é
    -- necessário porque as colunas que a relação não usa ficam nulas.
    CONSTRAINT claim_relations_pair_key UNIQUE NULLS NOT DISTINCT (
        user_id, relation, annotation_id, claim_id,
        question_annotation_id, answer_annotation_id,
        source_concept_id, target_concept_id
    )
);

CREATE INDEX claim_relations_user_idx ON claim_relations (user_id);
CREATE INDEX claim_relations_claim_idx ON claim_relations (claim_id);
CREATE INDEX claim_relations_annotation_idx ON claim_relations (annotation_id);
CREATE INDEX claim_relations_question_idx ON claim_relations (question_annotation_id);
CREATE INDEX claim_relations_source_concept_idx ON claim_relations (source_concept_id);

COMMIT;
