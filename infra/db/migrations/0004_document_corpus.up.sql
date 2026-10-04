BEGIN;

-- Texto extraído é uma projeção reconstruível do PDF armazenado, não uma anotação.
CREATE TABLE document_corpus_index (
    document_id uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    source_sha256 varchar(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    extractor_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('ready', 'empty', 'failed')),
    actual_page_count integer CHECK (actual_page_count IS NULL OR actual_page_count > 0),
    last_error text,
    indexed_at timestamptz NOT NULL
);

CREATE TABLE document_corpus_pages (
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number > 0),
    text_content text NOT NULL,
    content_sha256 varchar(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    extractor_version text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', text_content)) STORED,
    PRIMARY KEY (document_id, page_number)
);

CREATE INDEX document_corpus_pages_search_idx ON document_corpus_pages USING gin (search_vector);

COMMIT;
