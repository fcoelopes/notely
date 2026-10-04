BEGIN;

ALTER TABLE annotations ADD COLUMN study_session_id uuid
    REFERENCES study_sessions(id) ON DELETE SET NULL;
CREATE INDEX annotations_study_session_idx ON annotations(study_session_id)
    WHERE study_session_id IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE question_curation_requests (
    id uuid PRIMARY KEY,
    annotation_id uuid NOT NULL UNIQUE REFERENCES annotations(id) ON DELETE CASCADE,
    study_session_id uuid NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('pending', 'ready', 'no_source', 'failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz
);
CREATE INDEX question_curation_requests_session_idx
    ON question_curation_requests(study_session_id, created_at DESC);

CREATE TABLE question_curated_sources (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES question_curation_requests(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number > 0),
    chunk_number integer NOT NULL CHECK (chunk_number >= 0),
    chunk_sha256 varchar(64) NOT NULL CHECK (chunk_sha256 ~ '^[0-9a-f]{64}$'),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL CHECK (end_offset > start_offset),
    excerpt text NOT NULL CHECK (length(excerpt) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    rank integer NOT NULL CHECK (rank BETWEEN 1 AND 3),
    provider text NOT NULL,
    model text NOT NULL,
    retrieval_version text NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE(request_id, document_id, page_number, chunk_number),
    UNIQUE(request_id, rank)
);

COMMIT;
