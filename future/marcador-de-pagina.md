# Plano — marcador de página do Reader

**Issue:** [#11 — Marcador de página para retomar PDFs](https://github.com/fcoelopes/notely/issues/11).

## Intenção e comportamento

Permitir que a pessoa marque manualmente a página em que deseja retomar um PDF. Há um marcador por documento e por usuário; marcar outra página substitui a anterior. O Reader indica a página marcada, oferece ir até ela e removê-la. Ao reabrir uma sessão ou selecionar um PDF, a primeira abertura daquele PDF vai à página marcada. Navegar normalmente não altera o marcador.

## Escopo técnico

- PostgreSQL guarda `document_bookmarks` com dono, documento, página e datas. A migration valida página positiva e apaga o marcador se o documento for excluído. O serviço valida a página contra `documents.page_count` e o escopo do usuário.
- API autenticada: consultar marcadores do usuário, marcar/substituir e remover. Repetir um comando não cria duplicatas.
- O Reader carrega os marcadores, trata falhas sem bloquear a leitura e atualiza o estado somente após sucesso da API. A interface deixa clara a ação manual e exibe a página marcada.
- O marcador é dado autoral. Não altera o progresso de páginas visualizadas, a trilha temporal, AGE ou pgvector. Nenhum processamento assíncrono é necessário, portanto não há consumidor de outbox novo.

## Ordem e validação

1. Migration, modelo, porta, persistência e testes de validação, propriedade e substituição.
2. Contratos HTTP e testes de sucesso, documento alheio/inexistente e página inválida.
3. Controles do Reader, retomada ao reabrir e testes de comportamento/falha.
4. Rodar backend, frontend, build, migration e atualizar documentação.

## Riscos e rollback

A retomada automática não pode sobrescrever uma navegação feita depois da abertura, inclusive quando a resposta da API chega tarde. A falha do marcador não pode impedir abrir, navegar ou anotar. Para rollback operacional, ocultar controles e interromper chamadas; preservar a tabela até decidir retenção, pois contém escolha do usuário. A migration reversa deve bloquear exclusão de marcadores existentes.

## Resultado da implementação

Implementado na migration `0014`, no serviço de domínio, na API autenticada e no Reader. O marcador é carregado ao abrir a sessão e substituído apenas por ação explícita. Documentação durável: `docs/marcador-pagina.md`.

Validação: suíte completa do backend e 49 testes do frontend passaram; build do frontend passou. Migration `0014` aplicada nos bancos local e de testes, com backup verificado, e validada em banco descartável. O rollback recusou descartar um marcador existente. A barra do Reader foi verificada no Chromium a 1440, 800 e 390 px, sem controles sobrepostos nem rolagem horizontal.
