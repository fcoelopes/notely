# Progresso de leitura

O indicador mostra **páginas visualizadas**, não compreensão, tempo de estudo ou conclusão intelectual. Uma página entra na contagem após a renderização bem-sucedida no Reader. O mesmo número de página, quando revisto ou renderizado novamente após mudar o zoom, só conta uma vez por documento e sessão.

- **Arquivo na sessão:** páginas distintas visualizadas nessa sessão divididas por `documents.page_count`.
- **Arquivo na biblioteca:** união dos números de página visualizados em todas as sessões dividida por `page_count`.
- **Sessão:** soma das páginas visualizadas nos documentos atualmente vinculados dividida pela soma de suas páginas. O cálculo é ponderado pelo tamanho dos PDFs. Uma sessão sem documentos mostra 0%.
- Os percentuais são inteiros, arredondados ao inteiro mais próximo. Documentos anteriores a esta implementação começam em 0%, pois as sessões de leitura antigas só guardam páginas inicial e final.

`study_session_viewed_pages` no PostgreSQL é a fonte de verdade. A chave `(study_session_id, document_id, page_number)` torna o registro idempotente. A tabela não depende do vínculo atual em `study_session_documents`: retirar um documento o exclui do agregado da sessão, mas reanexá-lo restaura o progresso que já tinha. Excluir a sessão ou documento elimina os respectivos registros por FK. Este registro de domínio não gera processamento assíncrono nem altera a telemetria temporal, as anotações, AGE ou pgvector.

A API expõe `GET /api/reading-progress` para a biblioteca e o Reader e `PUT /api/study-sessions/{session_id}/documents/{document_id}/viewed-pages/{page_number}` para registrar a página. O `PUT` exige sessão e documento existentes, vínculo atual e página dentro de `1..page_count`; pode ser repetido sem aumentar a contagem. A resposta contém progresso da sessão e do arquivo na biblioteca. O Reader continua utilizável se essa API falhar.

A migration `0007_reading_progress.up.sql` cria a tabela e o índice para agregação global. A revisão `down` é bloqueada quando existem páginas visualizadas, para preservar o progresso. Veja [o procedimento de rollback](rollback.md).
