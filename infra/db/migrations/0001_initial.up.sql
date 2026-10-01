BEGIN;

CREATE TABLE documents (
    id uuid PRIMARY KEY,
    sha256 varchar(64) NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    filename text NOT NULL CHECK (length(btrim(filename)) > 0),
    mime_type text NOT NULL DEFAULT 'application/pdf',
    page_count integer NOT NULL CHECK (page_count > 0),
    storage_uri text NOT NULL CHECK (length(btrim(storage_uri)) > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE annotations (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number > 0),
    type text NOT NULL CHECK (type IN ('highlight', 'note', 'question', 'important', 'disagreement', 'relation')),
    quote text NOT NULL CHECK (length(btrim(quote)) > 0),
    comment text,
    position_json jsonb NOT NULL CHECK (position_json <> '{}'::jsonb),
    source text NOT NULL CHECK (source IN ('user_selection', 'native_pdf', 'import')),
    author_type text NOT NULL CHECK (author_type = 'user'),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz
);

CREATE INDEX annotations_document_page_idx
    ON annotations (document_id, page_number)
    WHERE deleted_at IS NULL;

CREATE TABLE outbox_events (
    id uuid PRIMARY KEY,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    available_at timestamptz NOT NULL,
    claimed_at timestamptz,
    processed_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error text,
    UNIQUE (aggregate_type, aggregate_id, event_type)
);

CREATE INDEX outbox_events_pending_idx
    ON outbox_events (available_at, created_at)
    WHERE processed_at IS NULL;

COMMIT;

