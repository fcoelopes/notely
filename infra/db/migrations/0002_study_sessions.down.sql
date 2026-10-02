BEGIN;

DROP INDEX IF EXISTS ai_suggestions_subject_idx;
DROP TABLE IF EXISTS ai_suggestions;

DROP INDEX IF EXISTS study_session_documents_order_idx;
DROP TABLE IF EXISTS study_session_documents;

DROP TABLE IF EXISTS study_sessions;

DROP INDEX IF EXISTS outbox_events_dedupe_key_idx;
ALTER TABLE outbox_events DROP COLUMN IF EXISTS dedupe_key;
ALTER TABLE outbox_events DROP COLUMN IF EXISTS event_key;
ALTER TABLE outbox_events
    ADD CONSTRAINT outbox_events_aggregate_type_aggregate_id_event_type_key
    UNIQUE (aggregate_type, aggregate_id, event_type);

COMMIT;
