# Correção do leitor PDF

Solicitação: corrigir o leitor PDF.
Issue: pendente; api.github.com indisponível neste ambiente.

## Escopo

Descartar seleção e compositor ao mudar documento, página ou zoom para impedir
anotações com coordenadas obsoletas. Tratar falhas de fonte, interpretação e
renderização no Reader, com mensagens em português. Preservar os contratos de
persistência e a proveniência. Sem mudanças em domínio, banco ou workers.

## Ordem e validação

1. Corrigir o ciclo de vida da seleção e os estados do PDF.
2. Testar navegação com seleção, zoom e falhas de carregamento.
3. Executar testes, build e verificação do diff; documentar comportamento.

## Riscos e rollback

A seleção não salva é descartada ao navegar ou alterar zoom. Anotações salvas
permanecem intactas. Reverter apenas esta correção do Reader para rollback;
nenhuma migração necessária.

## Resultado

Correção implementada. 25 testes passaram; build TypeScript/Vite passou e
`git diff --check` passou. O build mantém o aviso de bundle acima de 500 kB.
Validação visual com PDF real no navegador não executada. Issue pendente por
indisponibilidade do GitHub.
