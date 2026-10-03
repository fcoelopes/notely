# Reconstrução do frontend

Solicitação: reconstruir o frontend do Notely.
Issue: pendente; consulta ao GitHub indisponível neste ambiente.

## Escopo e decisões

Reconstruir a biblioteca como espaço de estudo, com navegação entre visão geral,
sessões e documentos, busca local por título/tema, contagens reais e estados vazios.
Usar uma identidade editorial clara, com papel claro e acentos verdes, e adaptar
biblioteca e Reader a telas pequenas. Preservar upload, PDF.js, abas, anotações,
telemetria de leitura e aceite explícito das sugestões de tema.
Não alterar domínio, API, workers, PostgreSQL, AGE ou pgvector. Sem dependências novas.

## Ordem

1. Reconstruir a biblioteca e seus controles acessíveis.
2. Atualizar estilos e acessibilidade dos controles do Reader.
3. Testar navegação, busca, estados vazios e fluxos existentes; compilar.
4. Documentar uso e resultados.

## Riscos e rollback

Regressões de seleção de PDF e navegação: manter componentes e contratos existentes,
validar testes do App. Reverter os arquivos de interface desta mudança para rollback;
nenhuma migração ou transformação de dados necessária.

## Validação

`cd frontend && npm test` e `npm run build`.
Testar filtros, busca sem resultados, retomada de sessão e criação com tema opcional.

## Resultado

Interface implementada e documentada em `frontend/README.md`.
Validação: 21 testes passaram em três arquivos; build TypeScript/Vite passou;
`git diff --check` sem erros. O Vite continua avisando sobre o tamanho do bundle
que inclui o leitor PDF. Validação visual em navegador não executada.
Issue permanece pendente por indisponibilidade de acesso ao GitHub.
