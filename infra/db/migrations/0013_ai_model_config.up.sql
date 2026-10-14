BEGIN;

-- Configuração de modelo por usuário (BYOK). A chave é guardada cifrada: é credencial
-- com custo associado, não pode ficar em claro no banco.
CREATE TABLE ai_model_configs (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('openai_compatible', 'anthropic')),
    model text NOT NULL CHECK (length(btrim(model)) > 0),
    api_key_encrypted text,
    base_url text,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (user_id)
);

COMMIT;
