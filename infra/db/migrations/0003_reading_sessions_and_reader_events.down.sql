BEGIN;

DROP INDEX IF EXISTS reader_events_annotation_idx;
DROP INDEX IF EXISTS reader_events_session_idx;
DROP INDEX IF EXISTS reader_events_document_idx;
DROP TABLE IF EXISTS reader_events;

DROP INDEX IF EXISTS annotations_reading_session_idx;
DROP INDEX IF EXISTS annotations_passage_idx;
ALTER TABLE annotations DROP CONSTRAINT IF EXISTS annotations_passage_id_check;
ALTER TABLE annotations DROP COLUMN IF EXISTS passage_id_version;
ALTER TABLE annotations DROP COLUMN IF EXISTS passage_id;
ALTER TABLE annotations DROP COLUMN IF EXISTS reading_session_id;

DROP INDEX IF EXISTS reading_sessions_open_idx;
DROP INDEX IF EXISTS reading_sessions_document_idx;
DROP TABLE IF EXISTS reading_sessions;

-- Reverter esta migration remove a trilha temporal por completo.
DROP EXTENSION IF EXISTS timescaledb;

COMMIT;
