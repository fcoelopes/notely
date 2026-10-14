BEGIN;

-- Restaura o default transitório da 0011, para que o downgrade volte ao estado anterior.
DO $$
DECLARE
    target text;
    bootstrap_id uuid := '00000000-0000-0000-0000-000000000001';
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
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN user_id SET DEFAULT %L', target, bootstrap_id
        );
    END LOOP;
END $$;

COMMIT;
