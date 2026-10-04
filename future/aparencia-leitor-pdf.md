# Aparência do leitor PDF

Solicitação: dar ao Reader aparência de leitor PDF.
Issue: pendente; acesso ao GitHub indisponível na sessão.

## Escopo e ordem

Compactar cabeçalho, sessão e abas. Usar área de leitura cinza neutra com página
branca centralizada. Agrupar navegação e zoom em barra de ferramentas; permitir
ir diretamente a uma página. Tornar anotações recolhíveis e tema da sessão
expansível, fechado inicialmente. Preservar seleção, persistência e sugestões
somente por aceite explícito. Sem dependências ou mudanças no backend.

## Validação e riscos

Testar salto de página e limites, recolhimento do painel e fluxos existentes.
Executar testes, build e diff check. Risco: controles apertados em telas pequenas;
usar quebra de linha e manter rolagem do PDF. Rollback: reverter apenas as mudanças
de aparência e controles desta entrega. Sem migrações.

## Resultado

Implementado e documentado no README do frontend. 27 testes passaram e o build
TypeScript/Vite passou. O aviso existente de tamanho do bundle permanece.
Validação visual em navegador não executada; issue pendente por falta de acesso
ao GitHub.

## Correção de rolagem

Limitar a linha do grid e a coluna do Reader à altura disponível, manter a barra
de ferramentas fixa e reservar barra vertical na área do PDF. Em telas pequenas,
o painel lateral sobrepõe a leitura sem aumentar a altura do documento.
A área do PDF recebe foco para rolagem por teclado.

Regressão validada em Chromium com página simulada de 1500 px de altura nas
larguras 1440, 800 e 390 px: scrollTop positivo, fim da página visível e área
contida na janela. Teste reproduzível em `frontend/tests/pdf-scroll.cjs`.

## Ajuste visual inspirado no Edge

Painel de anotações inicia recolhido. Cabeçalho e abas usam tons neutros,
com navegação de página e zoom agrupados na barra. O campo de página aceita
salto por Enter. Em telas estreitas, a página inicia ajustada à largura; zoom
continua disponível. A troca de página reposiciona a leitura no topo.

Verificação: PDF real de três páginas no Chromium em 1440 × 900 e 390 × 900;
página centralizada, rolagem vertical funcional no desktop, largura ajustada
no celular, próxima página no topo e painel de notas acessível. A API foi
simulada para esse ensaio visual.

## Refinamento do cabeçalho

A captura enviada pelo usuário mostrou três linhas altas e o nome do documento
repetido. Tema, upload, acervo e fechamento passaram à mesma linha; as abas
ficam abaixo e a barra de página/zoom permanece fixa. Aumentar zoom não trava
mais em 180%; o intervalo agora é de 50% a 300%. A página ganhou 33 px de
altura útil na conferência em Chromium a 1867 × 900. Verificado também em
900 × 900 e 390 × 900 com PDF de três páginas e API simulada.
