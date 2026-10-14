BEGIN;

CREATE TABLE users (
    id uuid PRIMARY KEY,
    email text NOT NULL CHECK (length(btrim(email)) > 0),
    normalized_email text NOT NULL UNIQUE CHECK (length(btrim(normalized_email)) > 0),
    password_hash text,
    totp_secret_encrypted text,
    totp_enabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz,
    CHECK (NOT totp_enabled OR totp_secret_encrypted IS NOT NULL)
);

CREATE TABLE auth_sessions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE CHECK (length(btrim(token_hash)) > 0),
    stage text NOT NULL CHECK (stage IN ('mfa_pending', 'active')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    revoked_at timestamptz
);

CREATE INDEX auth_sessions_user_idx ON auth_sessions (user_id);

CREATE TABLE recovery_codes (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash text NOT NULL CHECK (length(btrim(code_hash)) > 0),
    created_at timestamptz NOT NULL,
    used_at timestamptz,
    UNIQUE (user_id, code_hash)
);

CREATE INDEX recovery_codes_user_idx ON recovery_codes (user_id);

-- Tentativas de login ficam registradas para limitar força bruta sem depender de estado
-- em memória do processo.
CREATE TABLE login_attempts (
    id uuid PRIMARY KEY,
    normalized_email text NOT NULL,
    succeeded boolean NOT NULL,
    attempted_at timestamptz NOT NULL
);

CREATE INDEX login_attempts_email_idx ON login_attempts (normalized_email, attempted_at DESC);

-- Dono provisório: a migration garante dono aos dados que já existem. A senha é definida
-- pelo comando de bootstrap e ninguém entra enquanto ela não existir.
INSERT INTO users (id, email, normalized_email, totp_enabled, created_at, updated_at)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'owner@notely.local',
    'owner@notely.local',
    false,
    now(),
    now()
);

-- Dono por registro. Tabelas puramente derivadas (páginas e chunks do corpus, eventos de
-- leitura, relação anotação → conceito) não recebem coluna própria: o dono é resolvido
-- pelo registro de origem.
--
-- O default é transitório: ele existe para que a aplicação continue funcionando enquanto
-- cada escrita passa a informar o dono explicitamente. Quando todas informarem, o default
-- deve ser removido numa revisão seguinte.
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
            'ALTER TABLE %I ADD COLUMN user_id uuid NOT NULL DEFAULT %L',
            target,
            bootstrap_id
        );
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE',
            target,
            target || '_user_fk'
        );
        EXECUTE format('CREATE INDEX %I ON %I (user_id)', target || '_user_idx', target);
    END LOOP;
END $$;

-- Conceito pertence ao usuário: o mesmo nome pode existir para pessoas diferentes.
ALTER TABLE concepts DROP CONSTRAINT IF EXISTS concepts_normalized_name_key;
ALTER TABLE concepts ADD CONSTRAINT concepts_user_normalized_key UNIQUE (user_id, normalized_name);

COMMIT;
