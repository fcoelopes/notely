BEGIN;

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
        EXECUTE format('DROP INDEX IF EXISTS %I', target || '_user_idx');
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', target, target || '_user_fk');
        EXECUTE format('ALTER TABLE %I DROP COLUMN IF EXISTS user_id', target);
    END LOOP;
END $$;

ALTER TABLE concepts DROP CONSTRAINT IF EXISTS concepts_user_normalized_key;
ALTER TABLE concepts ADD CONSTRAINT concepts_normalized_name_key UNIQUE (normalized_name);

DROP TABLE IF EXISTS login_attempts;
DROP TABLE IF EXISTS recovery_codes;
DROP TABLE IF EXISTS auth_sessions;
DROP TABLE IF EXISTS users;

COMMIT;
