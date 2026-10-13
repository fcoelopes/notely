BEGIN;

CREATE TABLE document_bookmarks (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, document_id)
);

CREATE INDEX document_bookmarks_document_idx ON document_bookmarks (document_id);

COMMIT;
