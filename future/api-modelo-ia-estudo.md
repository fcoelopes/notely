# Plano de implementação — API de modelo de IA para estudo (BYOK, Claude/GPT)

**Estado:** em curadoria, não iniciado

**Issue:** [#8 — API de modelo de IA para estudo (BYOK, Claude/GPT)](https://github.com/fcoelopes/notely/issues/8)

## Intenção

Permitir que o usuário plugue o próprio modelo de IA (Claude ou GPT, com chave BYOK) para um assistente de estudo. O primeiro uso é ancorado nas notas e no corpus do próprio usuário, com proveniência; o chat livre entra na sequência.

## Decisões de produto

- BYOK: o usuário configura provider (`openai`/`anthropic`), modelo e a própria chave. A chave é cifrada em repouso.
- Primeiro uso: assistente ancorado (RAG) sobre o corpus e as anotações da sessão/documento, citando as fontes. Depois, chat livre (mesmo endpoint, sem grounding).
- A saída do modelo é `MODEL_GENERATED`/`AI_INFERENCE`, distinta de dado autoral, e nunca vira conhecimento do usuário automaticamente (AGENTS.md §1 e §6).
- O custo fica com o usuário (BYOK); a resposta informa `provider` e `model` usados.

## Provider

- Protocolo `ChatModelProvider` (generaliza o padrão já existente em `providers/topic.py`): `async def complete(messages, *, system, max_tokens) -> ChatCompletion`.
- Adapters: `OpenAIChatProvider` (Chat Completions, endpoint configurável) e `AnthropicChatProvider` (Messages API). Ambos BYOK, com chamada HTTP fora da transação de domínio.
- `OpenAICompatibleTopicSuggestionProvider` já existe e é a semente; os providers de tema e curadoria podem migrar para a mesma abstração depois, sem unificação prematura (AGENTS.md §6).
- Streaming (SSE) é desejável, mas opcional na primeira versão.

## Persistência

- Tabela `ai_model_configs` por usuário: `user_id`, `provider`, `model`, `api_key_encrypted`, `base_url` (opcional), `enabled`, `created_at`, `updated_at`. Configuração nomeada, uma ativa por vez.
- Histórico do assistente, se persistido, separa mensagens do usuário das respostas do modelo com proveniência (`provider`, `model`, `sources`). Pode entrar depois.

## API

- `POST /api/study-assistant/chat` com corpo `{message, study_session_id?, document_id?}`:
  - Recupera contexto ancorado reusando `db/corpus_search.py` (busca lexical por sessão) e as anotações do usuário na sessão/documento; para perguntas globais, busca no corpus.
  - Monta o prompt com o contexto citável mínimo e chama o modelo BYOK.
  - Responde `{answer, provider, model, sources: [{document_id, page, excerpt, passage_id}]}`.
- Mesmo endpoint sem `study_session_id`/`document_id` = chat livre.
- `GET`/`PUT /api/ai-config` para provider/modelo/chave; a chave nunca é devolvida pela API.
- A chamada ao modelo é interativa (o usuário espera a resposta), mas nunca bloqueia a persistência de anotações nem a leitura (AGENTS.md §8).

## Ordem

1. `ChatModelProvider` + adapters OpenAI/Anthropic, com testes usando respostas HTTP fixas (sem rede real).
2. Migration `ai_model_configs` + endpoints de configuração com chave cifrada.
3. Assistente ancorado: recuperação de contexto (reuso de `corpus_search.py`) + prompt + resposta com fontes.
4. Chat livre (mesmo endpoint, sem grounding).
5. (Opcional) streaming SSE e histórico persistido com proveniência.

## Critérios de aceite

- O usuário configura Claude ou GPT com a própria chave e faz uma pergunta ancorada; a resposta cita documentos/páginas reais do corpus.
- Sem configuração ativa, o endpoint retorna erro claro; a chave nunca é exposta pela API.
- Respostas do modelo são marcadas como inferência e não se tornam anotação ou conhecimento autoral sem ação explícita do usuário.
- Falha do provider não afeta a leitura nem a persistência de anotações.
- A chave é cifrada em repouso (chave do servidor via env) e nunca logada.

## Riscos e rollback

- A chave BYOK é material sensível: cifrar em repouso e nunca logar. Por padrão, não enviar o documento integral para fora; enviar somente o contexto mínimo relevante (PRD §18).
- O grounding depende do índice lexical; PDFs sem texto extraível não contribuem até OCR/multimodal.
- Custo e latência são do usuário; limitar tokens de contexto e tamanho da resposta.
- Rollback operacional: desativar o endpoint por configuração; leitura e anotações não dependem dele.

## Fora de escopo

- Extração automática de conceitos/claims e projeção no grafo (fase B do AGE, plano próprio em `future/projecao-grafo-age.md`).
- Embeddings/pgvector (melhora o recall; fase 5 do `SPECTEC`).
- Multimodal e web search (gateways próprios no roadmap).

## Validação da implementação

- Testes de unidade dos adapters com fixtures HTTP; testes de integração do endpoint ancorado com corpus indexado; testes de não-vazamento da chave e de que a saída do modelo não vira dado autoral.
- `uv run pytest` e `npm test` verdes; migration `up → down → up` validada em banco descartável.
