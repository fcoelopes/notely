# Plano de implementação — contas multi-usuário com login e MFA (TOTP)

**Estado:** em curadoria, não iniciado

**Issue:** [#7 — Contas multi-usuário com login e MFA (TOTP)](https://github.com/fcoelopes/notely/issues/7)

## Intenção

Adicionar contas de usuário com login por senha e segundo fator TOTP, isolando as notas de cada usuário. O Notely hoje não tem conceito de usuário: todas as tabelas de domínio são globais. Esta entrega introduz identidade e dono por registro, sem mudar o comportamento de leitura e anotação.

## Decisões de produto

- Multi-usuário: cada registro de domínio pertence a um usuário.
- MFA: TOTP (RFC 6238, app autenticador) como único segundo fator nesta entrega. Passkeys e e-mail/SMS ficam para depois.
- Sessões: opacas, no servidor (tabela própria), em cookie `HttpOnly`/`Secure`/`SameSite`. Evita rotação de segredo JWT e permite revogação imediata.
- Senha: hash `argon2id`. Segredo TOTP e códigos de recuperação armazenados de forma protegida (segredo TOTP cifrado com chave do servidor; códigos de recuperação apenas como hash).
- Bootstrap: o primeiro usuário é criado por um comando de bootstrap (ou registro aberto desligável por configuração). Registro aberto não é padrão.

## Persistência e integridade

- Tabela `users`: `id`, `email` (único, normalizado), `password_hash`, `totp_secret_encrypted` (nulo até ativar MFA), `totp_enabled`, `created_at`, `updated_at`, `deleted_at`.
- Tabela `sessions`: `id`, `user_id`, `token_hash`, `expires_at`, `created_at`, `revoked_at`.
- Tabela `recovery_codes`: `id`, `user_id`, `code_hash`, `used_at`, `created_at`.
- Tabela `login_attempts` (ou contador) para proteção contra força bruta, com lockout temporário.
- **Ownership**: adicionar `user_id NOT NULL` (FK para `users`) em todas as tabelas de domínio: `documents`, `annotations`, `study_sessions`, `study_session_documents`, `study_session_viewed_pages`, `reading_sessions`, `ai_suggestions`, `question_curation_requests`, `question_curated_sources`, `document_corpus_*`. Backfill para o usuário de bootstrap na migration.
- O `outbox_events` passa a carregar `user_id` (ou o aggregate resolve o dono), para que workers (TimescaleDB, corpus, curadoria e, futuramente, grafo) projetem por usuário.
- AGE e pgvector, quando existirem, herdam o isolamento por usuário (grafo por namespace do usuário ou propriedade de dono nos nós).

## Autenticação e MFA

- Fluxo de login em duas etapas:
  1. `email` + senha → valida `argon2id`. Se `totp_enabled`, responde `401` com `mfa_required` e emite um desafio curto (token pendente). Caso contrário, cria a sessão.
  2. Código TOTP (6 dígitos, janela ±1) → cria a sessão.
- Ativação de MFA: gerar segredo, exibir QR (`otpauth://`), exigir um código válido para confirmar e, então, emitir códigos de recuperação (10, uso único, exibidos uma única vez).
- Códigos de recuperação permitem concluir o login quando o TOTP não está disponível; cada um é consumido no primeiro uso.
- Sessão: cookie opaco; `GET /api/me`, `POST /api/auth/login`, `POST /api/auth/mfa`, `POST /api/auth/logout`, `POST /api/auth/mfa/enroll`, `POST /api/auth/mfa/confirm`, `POST /api/auth/mfa/recovery`.
- Toda rota existente passa a exigir autenticação e a filtrar por `user_id` do usuário autenticado.

## Backend

- Novas dependências: `argon2-cffi` (hash), `pyotp` (TOTP), `qrcode` (QR de ativação) e `cryptography` (cifragem do segredo TOTP com chave do servidor). Nenhum framework novo.
- Dependência FastAPI `get_current_user` que resolve a sessão do cookie e injeta o usuário; os handlers passam a usar o `user_id` em toda consulta e escrita.
- `services.py` e `uow.py`: cada operação recebe o `user_id` e filtra/valida o dono. Erros de acesso alheio viram `404` (não vazar existência).
- Workers lêem `user_id` do outbox e projetam por usuário.

## Frontend (Reader)

- Tela de login (e-mail + senha), tela de código TOTP quando exigido, fluxo de ativação de MFA com QR e códigos de recuperação, e logout.
- Guarda de sessão: redireciona para login quando a API responde `401`; cookie `HttpOnly` (o token nunca fica no JavaScript).
- O Reader mantém o comportamento atual depois de autenticado.

## Ordem

1. Migration de `users`, `sessions`, `recovery_codes` e coluna `user_id` em todo o domínio (backfill bootstrap).
2. Hash de senha, bootstrap do primeiro usuário e sessões opacas com cookie.
3. Gate de autenticação e isolamento por `user_id` em serviços, uow, API e workers.
4. TOTP: ativação, desafio no login e códigos de recuperação.
5. Telas de login/MFA no Reader e testes de UI.
6. Documentação e ADR (decisão de sessão opaca + TOTP + modelo de dono).

## Critérios de aceite

- Usuário A não lê nem altera dados do usuário B (verificado em API, workers e busca).
- Login por senha funciona; usuário com MFA ativa passa pela etapa de código; código inválido/antigo é recusado.
- Ativação de MFA só conclui após um código válido; códigos de recuperação funcionam uma única vez.
- Sessão expira e pode ser revogada no logout; cookie não expõe o token ao JavaScript.
- Rotas sem sessão retornam `401`; acessar recurso de outro usuário retorna `404`.
- Falha de força bruta aciona lockout temporário sem vazar se o e-mail existe.
- Migrations `up`/`down` validadas; dado pré-existente é atribuído ao usuário de bootstrap sem perda.

## Riscos e rollback

- É a mudança mais ampla do projeto: toca todas as tabelas, serviços e workers. A coluna `user_id` com backfill é o caminho crítico e deve ser entregue e validada antes do gate de autenticação.
- Segredo TOTP e códigos de recuperação são material sensível: cifrar em repouso e nunca logar. Em produção, exigir TLS (cookie `Secure`).
- Perder o acesso ao MFA sem código de recuperação bloqueia o usuário; prever recuperação e, para operador, um caminho de reset documentado.
- Rollback operacional: desativar o gate de autenticação por configuração reverte ao modo sem login sem apagar `users`/`sessions`. A coluna `user_id` permanece (não é revertida sem migração dedicada).

## Fora de escopo (fases posteriores)

- Passkeys/WebAuthn, e-mail/SMS, OAuth/social, convites e papéis/permissões.
- Criptografia de conteúdo do usuário (dados em repouso) além dos segredos de autenticação.
- Cobrança/planos e multi-tenant de operador.

## Validação da implementação

- Testes de unidade para hash, TOTP (RFC 6238 com vetores conhecidos), códigos de recuperação e sessões.
- Testes de integração de isolamento: dois usuários, criação e consulta cruzada, workers projetando por dono.
- Testes de API de `401`/`404` e do fluxo de MFA completo (senha → desafio → código → sessão).
- `uv run pytest` e `npm test` verdes; migrations validadas como `up → down → up` em banco descartável.
