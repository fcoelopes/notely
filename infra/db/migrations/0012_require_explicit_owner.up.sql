BEGIN;

-- Remove o default transitório criado na 0011. Agora que toda escrita informa o dono, um
-- esquecimento deve falhar de forma explícita em vez de atribuir o registro ao dono
-- provisório em silêncio.
DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'documents',
        'annotations',
        'reading_sessions',
        'study_sessions',
        'study_session_documents',
        'study_session_viewed_pages',
        'ai_suggestions',
        'question_curation_requests',
        'question_curated_sources',
        'concepts',
        'outbox_events'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ALTER COLUMN user_id DROP DEFAULT', target);
    END LOOP;
END $$;

COMMIT;
