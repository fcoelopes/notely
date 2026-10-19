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
- fontes sugeridas para dúvidas criadas numa sessão, com estado assíncrono, excerto verificável, prévia da página e navegação explícita;
- afirmações do leitor a partir de um trecho anotado, com evidência visível, remoção e reafirmação que reaproveita a afirmação existente;
- conceitos das obras da sessão marcados como inferência, com a contagem de trechos e a associação autoral entre dois deles;
- painel de configuração do modelo de IA na barra lateral, com provedor, modelo escolhido na lista do próprio provedor (ou digitado em "outro"), endpoint, existência de chave e remoção confirmada.

O download autenticado (multi-usuário) do PDF armazenado fica para quando houver escopo por usuário.

## Biblioteca e navegação

A biblioteca oferece visão geral, sessões e documentos, com contagens do acervo e
busca por tema, título ou nome de arquivo, ignorando acentos. Crie uma sessão com
ou sem tema e retome sessões existentes pelos cartões. O tema continua sendo
editável no Reader, e sugestões de IA precisam de aceite explícito.

O layout se adapta a telas pequenas; os controles de página e zoom têm nomes
acessíveis e os elementos interativos mostram foco por teclado. A interface usa
fontes do sistema, sem buscar fontes externas.

O Reader permite marcar manualmente a página atual de cada PDF. Ao reabrir uma
sessão, o documento começa nessa página; o controle também permite voltar ao
marcador e removê-lo. A marcação não avança sozinha ao navegar.

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

Para verificar que os cartões da biblioteca não se sobrepõem em desktop, tablet e
celular: `node tests/library-layout.cjs [caminho-do-playwright] [executável-do-chromium-headless-shell]`.

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

## Afirmações e associações na sessão (issue #22)

O painel de anotações ganhou a ação **Afirmar** em cada trecho. O formulário pede o texto da
afirmação e a relação com o trecho — **sustenta** ou **contradiz** — e grava em
`POST /api/claims`. Afirmar o mesmo texto de novo não duplica nada: o domínio reaproveita a
afirmação existente e apenas acrescenta a evidência, e a lista reflete isso sem erro.

Os painéis **Afirmações** e **Conceitos e associações** ficam na barra lateral, dentro de
`<details>` fechados como o painel de tema, para não empurrar a leitura para fora da tela.
Ambos filtram pelas obras da sessão aberta, usando `document_id` dos documentos da sessão:

- **Afirmações** lista cada afirmação com documento, página e trecho, na ordem em que foram
  feitas, e cada linha pode ser removida sem afetar a anotação que serviu de evidência. A
  afirmação que perdeu a evidência continua listada, marcada como **sem evidência**: o
  domínio garante que ela segue sendo do usuário. O estado vazio orienta o primeiro uso.
- **Conceitos e associações** lista os conceitos com a contagem de trechos e os documentos da
  sessão, e diz com todas as letras que eles vêm de extração por modelo ou heurística de
  frequência — são **inferência**, não conhecimento confirmado. Selecionar dois e associar
  cria a `RELATED_TO` pelo `POST /api/concept-relations`; a associação já existente na mesma
  ordem não é oferecida de novo, e a tela avisa que a ordem do par é significativa hoje, de
  modo que associar invertido cria outra aresta. As associações existentes aparecem uma única
  vez, com remoção, que não remove os conceitos.

Falha de API em qualquer dessas ações vira mensagem no painel e não bloqueia ler, navegar ou
anotar: o estado local só muda depois do sucesso, como no marcador de página. Um trecho já
escrito no formulário de afirmação permanece ali quando a gravação falha.

O backend expõe `GET /api/concepts` para essa tela; nada foi aceito automaticamente, e a
associação entre dois conceitos é autoral. Ver
[plano](../future/tela-afirmacoes-reader.md) e
[issue #22](https://github.com/fcoelopes/notely/issues/22).

## Modelo de IA pela tela (issue #24)

O painel **Modelo de IA** fica na barra lateral, em `<details>` como os demais, e é a única
forma de cadastrar o modelo: não é mais preciso usar `curl`. Ele mostra o que a API devolve —
provedor, modelo, endpoint, se existe chave gravada e se o modelo está ativo — e permite
gravar, atualizar e remover a configuração.

- `GET /api/ai-config` respondendo `404` significa **sem configuração**, não erro: o painel
  mostra o formulário vazio e diz que nenhum modelo está configurado.
- `PUT /api/ai-config` grava `provider`, `model`, `base_url`, `enabled` e, quando houver,
  `api_key`. O campo de chave é `type="password"`, com opção de mostrar, e nasce sempre
  vazio: deixado vazio, a chave já gravada é **mantida**, e a interface diz isso. Depois de
  salvar com sucesso o campo é limpo do estado.
- `provider` aceita `anthropic` e `openai_compatible`. O segundo cobre Ollama, LM Studio,
  vLLM e gateways compatíveis e **exige o endpoint antes do envio**; o primeiro usa o
  endpoint oficial e dispensa.
- `DELETE /api/ai-config` remove a configuração, sempre depois da confirmação no próprio
  painel, e a leitura seguinte volta a responder `404`.

A chave nunca entra no estado da tela, no log, em URL ou em `localStorage`: a API só devolve
`has_key`, e as mensagens de erro não a repetem. Como no marcador de página, o estado local
só muda depois do sucesso da API; uma falha aparece junto ao formulário, mantém o que foi
digitado e não impede ler, navegar ou anotar. Ver
[plano](../future/tela-modelo-de-ia.md) e
[issue #24](https://github.com/fcoelopes/notely/issues/24).

## Lista de modelos do provedor (issue #25)

O nome do modelo deixou de ser digitado às cegas: o campo **Modelo** é uma lista alimentada
pelo próprio provedor, com **"outro (digitar)"** como saída para o que a lista não traz.
`POST /api/ai-config/models` recebe `{provider, api_key?, base_url?}` e devolve
`{models: [{id, display_name?}]}`; a rota não grava nada.

- Com configuração salva a lista é pedida **automaticamente**, usando a chave já gravada e o
  endpoint gravado. Sem configuração, ela sai por um botão explícito, **Carregar modelos**,
  que envia a chave e o endpoint digitados no formulário — só para aquela chamada.
- **"outro (digitar)"** volta ao campo de texto livre, já com o modelo atual dentro: é o
  caminho para endpoints locais e para modelos que o provedor não devolve. Se o modelo
  gravado não estiver na lista, o painel mantém o campo de texto em vez de apagar o que está
  configurado.
- Falha ao listar aparece junto ao campo e **não impede salvar digitando**: `409` é falta de
  chave, `422` é endpoint que não termina em `/chat/completions`, `502` é provedor que não
  respondeu.
- O campo do provedor compatível com OpenAI passou a se chamar **Endpoint de
  /chat/completions**: é a URL completa que o adapter chama, e é dela que sai a lista de
  modelos. O rótulo anterior, "base URL", induzia a gravar só o prefixo.

A lista é só conveniência: o que vale é o que a pessoa grava, e a tela nunca transforma
resposta de provedor em configuração sem o envio do formulário. Ver
[plano](../future/tela-modelo-de-ia.md) e
[issue #25](https://github.com/fcoelopes/notely/issues/25).
