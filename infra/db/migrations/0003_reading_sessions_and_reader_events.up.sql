BEGIN;

-- TimescaleDB é obrigatório para a trilha temporal de leitura. A migration falha de
-- forma explícita quando o servidor não traz a extensão ou não a pré-carrega.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION
            'TimescaleDB is required for the reading trail: % (use a server image that ships the timescaledb extension and preloads shared_preload_libraries=timescaledb)',
            SQLERRM;
    END;
END
$$;

-- Sessão de leitura: uma tentativa de leitura sobre um documento.
-- PostgreSQL guarda o estado atual; o tempo fica em reader_events.
CREATE TABLE reading_sessions (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    filename_snapshot text NOT NULL CHECK (length(btrim(filename_snapshot)) > 0),
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    start_page integer CHECK (start_page IS NULL OR start_page > 0),
    end_page integer CHECK (end_page IS NULL OR end_page > 0),
    last_activity_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (last_activity_at >= started_at)
);

CREATE INDEX reading_sessions_document_idx
    ON reading_sessions (document_id, started_at DESC);

CREATE INDEX reading_sessions_open_idx
    ON reading_sessions (last_activity_at DESC)
    WHERE ended_at IS NULL;

-- A marcação segue sendo a Annotation. Ela passa a registrar a sessão de leitura em
-- que nasceu (opcional, para importação) e a identidade estável do trecho.
ALTER TABLE annotations ADD COLUMN reading_session_id uuid
    REFERENCES reading_sessions(id) ON DELETE SET NULL;
ALTER TABLE annotations ADD COLUMN passage_id text;
ALTER TABLE annotations ADD COLUMN passage_id_version smallint NOT NULL DEFAULT 1
    CHECK (passage_id_version > 0);

-- Backfill: mesmas entradas do algoritmo versionado v1 (sha256 de namespace,
-- sha256 do documento, página, quote normalizada e prefix/suffix vazios, que é o que
-- existe em marcações anteriores a esta migration).
UPDATE annotations AS a
SET passage_id = encode(
        sha256(
            convert_to(
                'notely-passage:v1'
                || chr(10) || d.sha256
                || chr(10) || a.page_number::text
                || chr(10) || btrim(regexp_replace(a.quote, '\s+', ' ', 'g'))
                || chr(10) || ''
                || chr(10) || '',
                'UTF8'
            )
        ),
        'hex'
    )
FROM documents AS d
WHERE d.id = a.document_id
  AND a.passage_id IS NULL;

ALTER TABLE annotations ALTER COLUMN passage_id SET NOT NULL;
ALTER TABLE annotations ADD CONSTRAINT annotations_passage_id_check
    CHECK (passage_id ~ '^[0-9a-f]{64}$');

CREATE INDEX annotations_passage_idx
    ON annotations (passage_id)
    WHERE deleted_at IS NULL;

CREATE INDEX annotations_reading_session_idx
    ON annotations (reading_session_id)
    WHERE reading_session_id IS NOT NULL;

-- Trilha temporal. Não guarda o filename: o contexto histórico é resolvido por
-- session_id -> reading_sessions.filename_snapshot.
CREATE TABLE reader_events (
    time timestamptz NOT NULL,
    event_id uuid NOT NULL,
    event_type text NOT NULL CHECK (length(btrim(event_type)) > 0),
    session_id uuid,
    document_id uuid NOT NULL,
    annotation_id uuid,
    annotation_type text,
    passage_id text,
    passage_id_version smallint,
    page_number integer,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (event_id, time)
);

-- O worker grava com ON CONFLICT (event_id, time): reprocessar o mesmo evento do
-- outbox não duplica a linha temporal.
SELECT create_hypertable('reader_events', 'time', chunk_time_interval => INTERVAL '7 days');

CREATE INDEX reader_events_document_idx ON reader_events (document_id, time DESC);
CREATE INDEX reader_events_session_idx ON reader_events (session_id, time DESC);
CREATE INDEX reader_events_annotation_idx ON reader_events (annotation_id, time DESC);

COMMIT;
