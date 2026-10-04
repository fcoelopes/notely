# Notely Reader

O Reader abre o PDF localmente para resposta visual imediata e envia o arquivo à API. A persistência e as anotações só são habilitadas depois que o backend conclui o scan do ClamAV e o armazenamento no MinIO.

## Executar

Com backend, PostgreSQL, MinIO e ClamAV disponíveis:

```bash
cd frontend
npm install
npm run dev
```

Abra `http://localhost:5173`. O Vite encaminha `/api` para `http://localhost:8000` durante o desenvolvimento.

## Testar e compilar

```bash
npm test
npm run build
```

## Escopo atual

- renderização local via PDF.js;
- upload multipart verificado, com contagem de páginas lida no cliente antes do envio;
- armazenamento MinIO por SHA-256;
- posições normalizadas independentes do zoom;
- highlights, notas, dúvidas, itens importantes e discordâncias restaurados pela API, agrupados por documento;
- sessões de estudo com tema autoral e vários documentos em abas;
- biblioteca de documentos já ingeridos e download do PDF armazenado, para retomar uma sessão;
- sugestões de tema de IA como sugestão pendente, com provider e modelo visíveis, aplicadas apenas por aceite explícito do usuário;
- fontes sugeridas para dúvidas criadas numa sessão, com estado assíncrono, excerto verificável, prévia da página e navegação explícita.

O download autenticado (multi-usuário) do PDF armazenado fica para quando houver escopo por usuário.

## Biblioteca e navegação

A biblioteca oferece visão geral, sessões e documentos, com contagens do acervo e
busca por tema, título ou nome de arquivo, ignorando acentos. Crie uma sessão com
ou sem tema e retome sessões existentes pelos cartões. O tema continua sendo
editável no Reader, e sugestões de IA precisam de aceite explícito.

O layout se adapta a telas pequenas; os controles de página e zoom têm nomes
acessíveis e os elementos interativos mostram foco por teclado. A interface usa
fontes do sistema, sem buscar fontes externas.

Ao mudar de página, documento ou zoom, a seleção ainda não salva é descartada
para evitar marcações com posições obsoletas. Falhas de carregamento do arquivo
ou de renderização de página aparecem no Reader em português.

O Reader usa cabeçalho compacto, abas e página branca sobre fundo cinza. A barra
oferece navegação, salto direto por número de página e zoom. O botão “Anotações”
abre o painel lateral, que começa recolhido para dar espaço ao PDF. Tema e sugestões ficam na seção
expansível “Tema e sugestões da sessão”, fechada inicialmente.

A área do PDF tem barra de rolagem vertical e mantém os controles visíveis.
Ela pode receber foco para rolar pelo teclado. Em telas pequenas, o painel de
anotações sobrepõe a leitura e pode ser recolhido pelo botão da barra.

Para verificar o layout de rolagem com Chromium e Playwright disponíveis:
`node tests/pdf-scroll.cjs [caminho-do-playwright] [executável-do-chromium]`.
O teste usa uma página longa simulada e verifica o acesso ao seu final em três
larguras de tela.

A apresentação do Reader segue o padrão visual de um leitor PDF: abas, barra
compacta de página e zoom, página centralizada sobre fundo cinza e rolagem
vertical sempre visível. Em telas estreitas, o PDF começa ajustado à largura
da leitura; aumentar o zoom permite examinar detalhes. Ao trocar de página,
a leitura volta ao topo.

O topo do Reader reúne tema, abertura de arquivos e saída numa linha, seguido
pelas abas e por uma barra curta de navegação. O nome do PDF aparece nas abas,
sem repetição na barra. O zoom vai de 50% a 300%.

## Fontes sugeridas para dúvidas

Ao salvar uma Dúvida, o Reader envia o identificador da sessão de estudo e
continua disponível imediatamente. “Ver fontes” mostra a busca em andamento,
até três fontes, ausência de fonte útil ou indisponibilidade. A prévia não troca
a página principal; “Abrir no Reader” faz a navegação. Fontes de documentos
retirados da sessão ficam sinalizadas como indisponíveis.

O provider padrão do backend é busca lexical local, apresentado como tal.
O texto da fonte e o motivo da sugestão aparecem separados, com provider e
modelo visíveis. O painel consulta novamente a API enquanto o estado está
`pending` e oferece retry após `failed` ou `no_source`.
