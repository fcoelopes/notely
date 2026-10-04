BEGIN;

-- Trechos de corpus são uma projeção reconstruível das páginas extraídas.
CREATE TABLE document_corpus_chunks (
    document_id uuid NOT NULL,
    page_number integer NOT NULL,
    chunk_number integer NOT NULL CHECK (chunk_number >= 0),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL CHECK (end_offset > start_offset),
    text_content text NOT NULL CHECK (length(text_content) > 0),
    content_sha256 varchar(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', text_content)) STORED,
    PRIMARY KEY (document_id, page_number, chunk_number),
    FOREIGN KEY (document_id, page_number)
        REFERENCES document_corpus_pages(document_id, page_number) ON DELETE CASCADE
);

CREATE INDEX document_corpus_chunks_search_idx
    ON document_corpus_chunks USING gin (search_vector);

COMMIT;
