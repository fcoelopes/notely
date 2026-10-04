# Plano: curadoria de fontes para dúvidas em sessões de estudo

**Estado:** implementação em andamento

**Issue:** [#2 — Curadoria de fontes para dúvidas nas sessões de estudo](https://github.com/fcoelopes/notely/issues/2)

**Escopo:** dúvidas criadas no Reader durante uma sessão de estudo.

## Objetivo

Quando o usuário salvar uma marcação do tipo **Dúvida**, o Notely deverá procurar, nos PDFs daquela sessão de estudo, até três trechos que possam ajudar a investigá-la. Cada indicação mostrará **documento, página, excerto e motivo curto da relevância**. Um clique abrirá a página citada para conferência. A dúvida continuará disponível imediatamente, mesmo que a curadoria demore ou falhe.

A indicação é uma sugestão de leitura, não uma resposta à dúvida nem uma afirmação escrita pelo usuário. O sistema pode deixar explícito que **não encontrou fonte confiável**. “Sempre que criar” significa iniciar a tentativa de curadoria para cada dúvida elegível; não significa fabricar uma indicação quando o corpus não oferece evidência.

## Decisões de produto

- Escopo da busca: somente documentos vinculados à sessão em que a dúvida foi criada. Não usar a biblioteca inteira nem pesquisa web implicitamente.
- Fonte obrigatória: cada indicação deve apontar para um documento e uma página existentes, acompanhados de excerto conferível. Um motivo gerado por modelo nunca substitui a fonte.
- Ao avaliar fontes, considerar o texto da dúvida escrito pelo usuário, a citação marcada e o contexto próximo da página. Preferir uma página de outro documento quando ela for tão útil quanto uma da página de origem, sem excluir a própria página por regra fixa.
- Exibir as indicações como **“Fontes sugeridas”**. Não promovê-las a anotação, conhecimento autoral, relação de grafo ou mapa mental automaticamente. O usuário pode abrir, dispensar ou, em uma etapa posterior, salvar explicitamente uma fonte ligada à dúvida.
- Não gerar uma resposta completa por padrão. Responder, consultar a web e usar análise visual são ações separadas, com proveniência própria.
- Se nenhum trecho passar na verificação de relevância, mostrar “Nenhuma fonte útil encontrada nesta sessão”. Distinguir esse resultado de “Indexação em andamento” e de “Curadoria indisponível”.

## Base existente e lacunas

- `Annotation(type="question")` já guarda documento, página, trecho, comentário, posição, autoria e `passage_id`. `annotation.created` entra no outbox na mesma transação. O formulário do Reader exige texto para a dúvida.
- `study_session_documents` define quais PDFs pertencem a cada sessão. Uma `reading_session` mede atividade em um único documento e **não** identifica a sessão de estudo que delimita a busca.
- Os arquivos PDF estão no armazenamento de objetos. O backend ainda não possui extração persistente de texto por página, chunks de corpus, embeddings consultáveis ou um worker de curadoria. O fluxo atual de sugestão de tema usa `ai_suggestions`, mas não contém referências estruturadas a páginas.
- O outbox e o worker de TimescaleDB já mostram o padrão de entrega com retry e idempotência. AGE e pgvector permanecem projeções derivadas.

## Fluxo proposto

```text
Usuário salva Dúvida no Reader
    → PostgreSQL: annotation + vínculo à sessão de estudo + evento de outbox
    → API confirma a dúvida sem esperar IA
    → worker reúne trechos indexados dos documentos da sessão
    → recuperação lexical/semântica propõe candidatos
    → provider de curadoria avalia utilidade com contexto mínimo
    → backend valida documento, página e excerto; persiste indicações
    → Reader mostra estado e oferece prévia da página citada
```

### 1. Corpus por página, antes da curadoria

Criar uma projeção **reconstruível em PostgreSQL** do conteúdo extraído do PDF: documento, SHA-256 do arquivo, página, texto, chunks limitados à página, offsets ou âncoras do excerto, hash do conteúdo e versão do extrator. A página citada nunca dependerá só de uma posição em um índice vetorial. A extração deverá comparar a contagem real do PDF com `documents.page_count`, hoje recebida do cliente, e registrar discrepâncias.

O evento `document.created` acionará um worker de extração. Indexação lexical por texto/página oferece um primeiro caminho de recuperação. Embeddings versionados em pgvector podem ampliar o recall depois, mantendo texto e metadados primários em PostgreSQL e permitindo rebuild completo. Documentos já armazenados precisam de backfill idempotente. PDFs escaneados ou páginas sem texto terão estado de extração explícito; OCR/multimodal, atrás de provider, é uma etapa separada.

### 2. Contexto da dúvida e integridade da sessão

Adicionar `study_session_id` ao comando de criação da dúvida e persistir a associação de origem sem confundi-la com `reading_session_id`. Antes de gravar, validar que o documento marcado pertence à sessão. A mudança exige migração e mantém compatibilidade com anotações antigas sem sessão de estudo; estas não receberão curadoria automática retroativa sem uma escolha explícita de sessão.

Na mesma transação da anotação, criar um pedido de curadoria e um evento de outbox próprio, por exemplo `question.curation.requested`, com chave de deduplicação por dúvida e versão da solicitação. O evento `annotation.created` continua atendendo a telemetria e os demais consumidores. A requisição HTTP retorna assim que a transação confirmar.

### 3. Recuperação e avaliação

O worker espera a indexação dos documentos vinculados; enquanto ela estiver pendente, o pedido permanece em espera/retry com backoff. Construir a consulta com o texto da dúvida, citação e contexto próximo. Recuperar um número limitado de candidatos por busca lexical; acrescentar pgvector quando a projeção existir. Deduplicar trechos da mesma página e limitar tamanho de excerto e número de páginas enviados ao provider.

Um `SourceCurationProvider` substituível deve escolher ou ordenar **IDs de candidatos fornecidos pelo backend** e justificar brevemente. Ele não poderá criar IDs, títulos, números de página ou citações. Antes de persistir, o serviço confere novamente se a fonte está na sessão, se a página está no intervalo do documento e se o excerto corresponde ao texto extraído. Uma saída inválida é descartada. Quando a avaliação por IA falhar, conservar a dúvida e mostrar erro recuperável; um eventual resultado puramente lexical deve ser identificado como busca, não como curadoria de IA.

### 4. Persistência e ciclo de vida

Usar tabelas de domínio específicas para o pedido e suas fontes estruturadas, em vez de esconder referências apenas no JSON de `ai_suggestions`. O pedido registra `annotation_id`, `study_session_id`, versão, estado (`pending`, `ready`, `no_source`, `failed`), tentativas e datas. Cada fonte registra `document_id`, `page_number`, âncora/ID do chunk, excerto, motivo, posição no ranking, provider, modelo, versão de recuperação e data. Chaves únicas por pedido e fonte tornam o processamento idempotente. A decisão de usar tabelas específicas preserva a proveniência já exigida de `ai_suggestions`, sem forçar o ciclo `pending/accepted/rejected` de tema sobre uma indicação de leitura.

O documento e a página de origem da sugestão permanecem associados à dúvida. Ao retirar um PDF da sessão, impedir novas indicações a ele e sinalizar como indisponíveis as existentes; ao reprocessar, validar novamente a composição da sessão. A alteração da dúvida, quando houver edição, deverá versionar ou invalidar a curadoria anterior. Não apagar uma dúvida ou seus dados do usuário por falha do worker. Definir migrações `up`/`down` e política de retenção antes de implementar exclusão de fonte.

### 5. API e Reader

Expor um endpoint de leitura do estado e das fontes por sessão e dúvida, por exemplo `GET /api/study-sessions/{session_id}/questions/{annotation_id}/sources`; ele deve validar vínculo e escopo da sessão. Um comando separado poderá pedir nova tentativa após falha. Inicialmente o Reader pode consultar periodicamente enquanto o estado estiver `pending`; encerrar ao chegar a `ready`, `no_source` ou `failed`.

No painel de anotações, a dúvida terá um botão **“Ver fontes”** e um indicador discreto de estado. Ao abrir, o Reader pode dividir a área: PDF atual à esquerda e, à direita, cartão com título, página, excerto, motivo e prévia da página da fonte. Em tela estreita, usar painel recolhível. A prévia e a navegação usam o PDF já servido pela API, sem substituir a página atual até ação explícita do usuário. Fechar o painel devolve toda a largura à leitura. Mostrar provider/modelo e distinguir texto do documento de justificativa da IA.

## Ordem de implementação

1. Migrações, extração por página/chunks, estado de indexação e backfill dos PDFs existentes.
2. Recuperação lexical restrita à sessão, contratos de candidato e testes de relevância/proveniência. Adicionar pgvector apenas após o caminho textual funcionar, com rebuild documentado.
3. Associação da dúvida à sessão, pedido e evento de outbox na transação de criação; worker idempotente de curadoria.
4. Provider de avaliação, validação rígida dos candidatos e persistência dos resultados/estados.
5. Endpoint de leitura/retry e painel do Reader com prévia ou divisão de tela.
6. Avaliar OCR/multimodal para páginas sem texto e melhorar ranking a partir de feedback do usuário, sem alterar a autoria das dúvidas.

## Critérios de aceitação e validação

- Uma dúvida é salva e reabre normalmente mesmo com indexação, pgvector ou provider fora do ar; o pedido fica visível como pendente/falho e pode ser reprocessado.
- Para uma sessão com dois PDFs textuais, uma dúvida pode receber uma fonte do outro PDF; o cartão mostra o título correto, a página correta, um excerto existente e abre a página indicada.
- Nenhum resultado pode citar documento fora da sessão, página inexistente, excerto inexistente ou título inventado pelo modelo. Quando não há fonte útil, a UI mostra `no_source` sem fabricar resposta.
- Reentrega do evento, duas instâncias do worker e retry não criam pedidos ou fontes duplicados. Remover um documento da sessão não deixa link navegável apresentado como fonte atual.
- A extração e os embeddings podem ser reconstruídos do PDF armazenado e dos registros primários. Validar PDFs sem texto, arquivos corrompidos, divergência de contagem de páginas e falhas transitórias do provider.
- Testes de API, serviço, persistência e Reader cobrem estados `pending`, `ready`, `no_source` e `failed`, navegação por fonte, tela estreita e a continuidade da leitura durante o processamento.

## Fora deste corte

Resposta automática à dúvida, pesquisa web silenciosa, ingestão automática de uma fonte no mapa mental, autoaceite de relações e leitura multimodal de todos os documentos. Essas capacidades exigem decisões próprias de produto e proveniência.

## Riscos e rollback

O risco principal é uma indicação parecer confiável sem sustento no PDF. A mitigação é persistir e exibir somente referências verificadas contra o texto extraído, com estado explícito quando não houver evidência. Custos e latência ficam fora da ação síncrona do usuário, com limites para candidatos e contexto enviado. O Reader pode ocultar a funcionalidade por configuração; desligar o worker não afeta anotações já salvas. Recriar índice lexical/semântico a partir dos PDFs e registros em PostgreSQL; nunca usar AGE ou pgvector como única cópia das fontes.

## Progresso da implementação

A indexação por página da primeira etapa está implementada. As migrations `0005` e
`0006` acrescentam chunks com offsets conferíveis, vínculo da dúvida à sessão de
estudo, pedidos de curadoria e fontes estruturadas. O extrator passou para
`text-v2-chunks`: após aplicar as migrations, executar o backfill de corpus para
reconstruir os documentos indexados pela versão anterior. Pedidos aguardam essa
reindexação em vez de concluir prematuramente como `no_source`.

O caminho inicial de recuperação usa `tsvector` dos chunks e restringe a consulta
aos documentos vinculados à sessão. A busca considera o texto da dúvida, a citação
e o contexto próximo da página de origem; candidatos preservam documento, página,
offsets, texto e hash. O provider padrão é **busca lexical local**, identificado
como tal no Reader. Ele escolhe apenas IDs de candidatos retornados pelo banco.
O worker descarta saídas inválidas e confere novamente o vínculo, a versão do
índice, a página, o hash e o excerto antes de persistir até três fontes.

Criar uma dúvida na sessão grava anotação, pedido e evento
`question.curation.requested` na mesma transação. O worker publica estados
`pending`, `ready`, `no_source` e `failed`, faz retry com backoff e não impede que
a anotação seja usada quando corpus ou provider falham. A API expõe leitura do
estado e retry, e o Reader mostra as fontes, uma prévia da página e a navegação
explícita para o documento citado. Fontes de PDFs retirados da sessão aparecem
como indisponíveis.

A avaliação por modelo configurável, embeddings em pgvector e OCR/multimodal
para páginas sem texto continuam como próximas melhorias. Resultados do provider
lexical não são apresentados como inferência de IA. A política de retenção para
exclusão de fontes será definida antes de oferecer esse comando.
