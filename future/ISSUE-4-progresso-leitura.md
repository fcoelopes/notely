# Plano de implementação — progresso de leitura por arquivo e sessão

**Estado:** implementado na branch `feat/reading-progress`; aguardando revisão

**Issue:** [#4 — Progresso de leitura por arquivo e sessão para estudantes](https://github.com/fcoelopes/notely/issues/4)

## Intenção

Ajudar o estudante a retomar leituras longas e entender quanto do conjunto de PDFs de uma sessão já abriu, sem sugerir que o Notely avaliou compreensão ou conclusão intelectual.

## Definição do percentual

- Uma página conta quando o Reader a renderiza com sucesso. Contam-se **páginas distintas**; revisitas, zoom e reentrega da requisição não aumentam o numerador.
- O progresso de um arquivo dentro de uma sessão é `páginas distintas visualizadas nessa sessão / page_count`.
- O progresso global do arquivo, mostrado na biblioteca, é a união das páginas visualizadas em todas as sessões em que ele apareceu.
- O progresso da sessão soma os numeradores e denominadores dos arquivos **atualmente vinculados**. É ponderado por páginas, não uma média simples dos percentuais dos arquivos.
- O percentual é inteiro, arredondado ao inteiro mais próximo; uma sessão sem arquivos mostra 0%. A interface chama o número de progresso de páginas visualizadas e explica sua base.
- Saltar para a última página adiciona somente essa página. Documentos antigos começam em 0%, pois `reading_sessions` registra apenas páginas inicial/final e não permite reconstruir as páginas distintas com segurança.

## Persistência e integridade

Criar migration `0007` com uma tabela de páginas visualizadas, chave única `(study_session_id, document_id, page_number)`, data da primeira visualização e FKs para sessão/documento. Não vincular a linha ao vínculo atual `study_session_documents`: retirar um PDF da sessão exclui-o do percentual agregado, mas preserva as páginas visualizadas; reanexar o mesmo PDF recupera o progresso contextual. A exclusão explícita da sessão/documento poderá remover os registros via FK.

PostgreSQL é a fonte de verdade do progresso. TimescaleDB continua responsável pela história temporal; esta entrega não cria telemetria `page.viewed`, que tem plano separado em `future/telemetria-navegacao-leitura.md`. Como não há efeito assíncrono desencadeado pelo registro de uma página, nenhuma nova mensagem de outbox é necessária. AGE, pgvector e anotações não mudam.

## API e domínio

- Comando idempotente para registrar uma página renderizada, validando que a sessão existe, que o documento pertence a ela e que `1 <= page_number <= documents.page_count`.
- Consulta agregada para sessão e arquivos, e consulta global por arquivo para a biblioteca. Não transferir uma lista completa de páginas ao frontend só para calcular percentuais.
- Retornar numerador, denominador e percentual; o backend é responsável pelo cálculo para que Reader e biblioteca usem a mesma regra.
- Falha do registro de progresso não bloqueia renderização, navegação ou anotações.

## Reader

- Mostrar o percentual da sessão no cabeçalho e o percentual de cada PDF nas abas; mostrar os percentuais de sessões e arquivos na biblioteca.
- Registrar a página após renderização bem-sucedida e atualizar os indicadores a partir da resposta da API, sem bloquear a leitura.
- Ao reabrir a sessão, buscar o progresso persistido. Ao retirar ou reanexar um PDF, recalcular a sessão sem perder suas páginas vistas.
- Mostrar os contadores como `x/y páginas visualizadas`, sem apresentá-los como avaliação de aprendizado.

## Ordem

1. Migration, contrato de domínio e consultas agregadas em PostgreSQL.
2. API de leitura/registro e testes de persistência e falhas.
3. Integração do Reader e da biblioteca, com testes de UI.
4. Documentação operacional e validação das migrations `up`/`down`.

## Critérios de aceite

- Um PDF de 4 páginas aberto nas páginas 1 e 4 mostra 50%, e voltar à página 1 não aumenta o valor.
- Uma sessão com PDFs de 4 e 6 páginas, com 2 e 3 páginas visualizadas, mostra 50% (`5/10`).
- A biblioteca preserva o progresso após recarregar; o mesmo PDF em duas sessões usa a união de páginas para seu indicador global.
- Retirar um documento recalcula o percentual da sessão; reanexá-lo recupera suas páginas visualizadas naquela sessão.
- Documento fora da sessão, página inválida e sessão inexistente são rejeitados sem gravar progresso.
- Falha da API de progresso não interrompe o Reader. Testes automatizados e migrations reversíveis passam.

## Riscos e rollback

O percentual pode ser interpretado como compreensão. A UI explicita que mede páginas visualizadas. Uma página renderizada rapidamente pode ser contada sem leitura atenta; isso é uma limitação deliberada deste primeiro indicador. Para rollback operacional, ocultar indicadores e interromper a chamada de registro; manter a tabela até decidir retenção. A migration `down` remove os registros somente após backup quando aplicada fora de um banco descartável.

## Validação da implementação

- `uv run pytest` no backend: 79 testes passaram, incluindo persistência, idempotência, união entre sessões, retirada/reatribuição de documentos e erros da API.
- `npm test` no frontend: 34 testes passaram, incluindo atualização dos indicadores e continuidade da navegação quando o registro falha.
- `npm run build` no frontend: compilação e build concluídos.
- Migration `0007` validada como `up → down → up` em banco PostgreSQL descartável.
