# Marcador de página

O marcador registra **a página que a pessoa escolheu para retomar**. Há um marcador por PDF e por usuário. Marcar outra página substitui a anterior; mudar de página, visualizar páginas e fechar a sessão não o movem. Ao abrir uma sessão, cada PDF começa na sua página marcada. Durante a sessão, o Reader também oferece ir ao marcador e removê-lo.

O marcador é diferente do [progresso de páginas visualizadas](progresso-leitura.md): ele expressa uma escolha explícita, não uma contagem de páginas renderizadas. É um dado autoral no PostgreSQL (`document_bookmarks`), separado das anotações e da trilha temporal. A migration `0014` cria a tabela com chave `(user_id, document_id)` e remoção em cascata quando a conta ou o documento é excluído. A API valida que a página está entre `1` e `documents.page_count` e que o documento pertence à conta autenticada.

API:

- `GET /api/document-bookmarks`: lista os marcadores da conta.
- `PUT /api/documents/{document_id}/bookmark` com `{"page_number": 12}`: marca ou substitui a página.
- `DELETE /api/documents/{document_id}/bookmark`: remove o marcador; repetir a chamada é seguro.

Uma falha nessas operações não impede abrir PDFs, navegar ou anotar. Os comandos não geram projeções em AGE/pgvector nem trabalho assíncrono, por isso não publicam eventos de outbox. O rollback da migration recusa descartar a tabela quando existem marcadores; o procedimento operacional está em [rollback](rollback.md).
