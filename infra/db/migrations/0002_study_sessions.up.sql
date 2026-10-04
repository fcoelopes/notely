BEGIN;

-- Sessões de estudo agrupam documentos que o usuário está lendo sobre o mesmo tema.
-- O tema é dado autoral: pode ser escrito pelo usuário ou aceito a partir de uma
-- sugestão da IA, e a origem fica registrada.
CREATE TABLE study_sessions (
    id uuid PRIMARY KEY,
    theme text CHECK (theme IS NULL OR length(btrim(theme)) > 0),
    theme_origin text CHECK (theme_origin IS NULL OR theme_origin IN ('user', 'ai_suggestion')),
    theme_updated_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK ((theme IS NULL) = (theme_origin IS NULL)),
    CHECK ((theme IS NULL) = (theme_updated_at IS NULL))
);

CREATE TABLE study_session_documents (
    id uuid PRIMARY KEY,
    study_session_id uuid NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position integer NOT NULL CHECK (position >= 0),
    added_at timestamptz NOT NULL,
    UNIQUE (study_session_id, document_id)
);

CREATE INDEX study_session_documents_order_idx
    ON study_session_documents (study_session_id, position, added_at);

-- Sugestões da IA nunca entram no domínio autoral sem aceite explícito.
CREATE TABLE ai_suggestions (
    id uuid PRIMARY KEY,
    suggestion_type text NOT NULL CHECK (length(btrim(suggestion_type)) > 0),
    subject_type text NOT NULL CHECK (length(btrim(subject_type)) > 0),
    subject_id uuid NOT NULL,
    payload jsonb NOT NULL CHECK (payload <> '{}'::jsonb),
    provider text NOT NULL CHECK (length(btrim(provider)) > 0),
    model text NOT NULL CHECK (length(btrim(model)) > 0),
    status text NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_at timestamptz NOT NULL,
    accepted_at timestamptz,
    rejected_at timestamptz,
    CHECK ((status = 'accepted') = (accepted_at IS NOT NULL)),
    CHECK ((status = 'rejected') = (rejected_at IS NOT NULL))
);

CREATE INDEX ai_suggestions_subject_idx
    ON ai_suggestions (subject_type, subject_id, created_at DESC);

-- Fatos de domínio repetíveis (por exemplo, trocar o tema mais de uma vez) precisam
-- de chave própria: a tripla agregado+evento deixa de ser suficiente. Fatos pontuais
-- continuam deduplicados pela tripla, que vira o valor padrão de dedupe_key.
ALTER TABLE outbox_events ADD COLUMN event_key text;
ALTER TABLE outbox_events ADD COLUMN dedupe_key text GENERATED ALWAYS AS (
    coalesce(event_key, aggregate_type || ':' || aggregate_id::text || ':' || event_type)
) STORED;
ALTER TABLE outbox_events DROP CONSTRAINT IF EXISTS outbox_events_aggregate_type_aggregate_id_event_type_key;
CREATE UNIQUE INDEX outbox_events_dedupe_key_idx ON outbox_events (dedupe_key);

COMMIT;
