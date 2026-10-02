# ADR 0002 — Sessões de estudo e sugestões de tema

**Status:** Aceito  
**Data:** 2026-10-02

## Contexto

A leitura de estudo raramente acontece sobre um único arquivo: o usuário lê vários PDFs sobre o mesmo assunto e precisa que o sistema entenda o tema daquela sessão de leitura. Até este corte não existia no domínio nenhum conceito de sessão, coleção ou tema, e o Reader abria um documento por vez.

Duas restrições do repositório determinam o desenho:

- o Notely é reader-first: o sistema não decide pelo usuário, e inferência de modelo não pode ser confundida com dado autoral;
- PostgreSQL é a fonte de verdade e toda mutação de domínio que exigir processamento assíncrono deve gravar o evento de outbox na mesma transação.

## Decisão

- Nova entidade autoral `study_sessions`, com `theme`, `theme_origin` (`user` ou `ai_suggestion`) e `theme_updated_at`. O tema é dado do usuário; a origem registra como ele chegou ali.
- `study_session_documents` liga documentos a uma sessão com `position`, sem duplicar conteúdo. Ingestão continua deduplicando por SHA-256, então o mesmo PDF pode participar de várias sessões sem cópia extra.
- Sugestões de tema são persistidas em `ai_suggestions` com `provider`, `model`, `payload` e `status` (`pending`, `accepted`, `rejected`). Enquanto pendente, a sugestão não altera o tema. Aceitar é o único caminho pelo qual ela entra na sessão, e nesse momento o `theme_origin` passa a `ai_suggestion`; uma edição posterior do usuário volta para `user`.
- A geração da sugestão acontece fora de qualquer unidade de trabalho: o contexto é lido em uma transação de leitura, o provider é chamado, e só depois uma nova transação grava sugestão e evento. IA nunca participa da transação de domínio.
- O contexto enviado é o mínimo útil: títulos dos documentos da sessão e as anotações escritas pelo usuário, com limite de itens e de caracteres. O PDF inteiro não é enviado.
- Providers de tema ficam atrás de `TopicSuggestionProvider`. O padrão é heurístico, sem rede e determinístico, para que o fluxo funcione offline e em testes; um adapter compatível com `/chat/completions` é ativado por configuração (`NOTELY_TOPIC_PROVIDER=openai_compatible`).
- `outbox_events` ganha `event_key` e uma coluna gerada `dedupe_key`. Fatos pontuais continuam deduplicados pela tripla agregado+evento; fatos repetíveis, como trocar o tema mais de uma vez, recebem chave própria e cada mudança gera um evento.

## Consequências

- Uma sessão pode ser retomada: a biblioteca lista documentos já ingeridos e o PDF é servido por `GET /api/documents/{id}/content` a partir do MinIO.
- O tema nunca é sobrescrito por inferência: aceitar uma sugestão é um comando explícito e auditável, e a sugestão permanece registrada com provider e modelo.
- A sugestão neste corte é síncrona em relação ao clique do usuário, porque ainda não existe runtime de workers no repositório. Quando os consumidores de outbox existirem, a geração pode migrar para um worker lendo `ai_suggestion.requested` sem mudar o contrato de aceite nem o schema.
- O tema é texto livre; agrupamento semântico por embeddings (`document_contents` + pgvector, Fase 5) continua sendo o caminho para recuperação por similaridade e não foi antecipado aqui.
- O custo do tema sugerido depende do provider escolhido; o padrão heurístico é local e gratuito, e qualquer provider externo precisa ser configurado explicitamente.
