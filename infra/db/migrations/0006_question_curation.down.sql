BEGIN;
DROP TABLE question_curated_sources;
DROP TABLE question_curation_requests;
DROP INDEX annotations_study_session_idx;
ALTER TABLE annotations DROP COLUMN study_session_id;
COMMIT;
