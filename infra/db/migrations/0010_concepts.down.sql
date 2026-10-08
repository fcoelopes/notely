BEGIN;

-- A guarda de rollback roda antes: descartar relação aceita pelo usuário perderia dado
-- autoral. Relações apenas sugeridas são reconstruíveis a partir das anotações.
DROP TABLE IF EXISTS annotation_concepts;
DROP TABLE IF EXISTS concepts;

DELETE FROM outbox_deliveries WHERE consumer = 'concepts';

COMMIT;
