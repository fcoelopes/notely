BEGIN;

-- Cobertura de páginas visualizadas é dado de domínio, independente da trilha temporal.
-- Não há FK para study_session_documents: retirar e reanexar um PDF preserva o progresso.
CREATE TABLE study_session_viewed_pages (
    study_session_id uuid NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number > 0),
    first_viewed_at timestamptz NOT NULL,
    PRIMARY KEY (study_session_id, document_id, page_number)
);

CREATE INDEX study_session_viewed_pages_document_idx
    ON study_session_viewed_pages (document_id, page_number);

COMMIT;
