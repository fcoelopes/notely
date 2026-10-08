BEGIN;

-- A configuração é recadastrável pelo usuário; o `down` não descarta dado autoral.
DROP TABLE IF EXISTS ai_model_configs;

COMMIT;
