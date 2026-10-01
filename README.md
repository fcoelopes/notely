# Notely

**Leia. Anote. Conecte.**

O Notely será um leitor de documentos voltado para estudo, leitura técnica e construção de conhecimento pessoal.

A proposta é simples: **você continua sendo responsável pela leitura e por decidir o que é relevante; o Notely cuida de registrar, organizar, relacionar e reencontrar aquilo que surgiu durante a leitura.**

O projeto nasce para resolver um problema comum: depois de meses ou anos lendo PDFs, os destaques, dúvidas e comentários ficam espalhados entre arquivos, leitores diferentes e anotações difíceis de recuperar. O Notely pretende transformar cada interação de leitura em conhecimento estruturado, sem tirar o usuário do fluxo.

## Visão do produto

O fluxo principal será:

```text
PDF
 │
 ▼
Notely Reader
 │
 ├── destacar um trecho
 ├── escrever uma nota
 ├── marcar uma dúvida
 ├── indicar algo importante
 └── relacionar conceitos
        │
        ▼
persistência imediata
        │
        ▼
enriquecimento assíncrono
        │
        ├── contexto semântico
        ├── embeddings
        ├── conceitos
        ├── relações
        └── grafo de conhecimento
```

O Notely não foi pensado para "ler o PDF pelo usuário". O documento continua sendo lido normalmente. A inteligência entra como uma camada de apoio que acompanha o processo de leitura.

Quando o usuário fizer um destaque, comentário ou pergunta, o sistema poderá registrar:

- qual documento originou a anotação;
- a página e a região correspondente;
- o trecho selecionado;
- o contexto ao redor;
- a intenção da anotação;
- conceitos relacionados;
- relações com anotações anteriores;
- a diferença entre informação escrita pelo usuário e inferência gerada por modelo.

A proveniência deve permanecer preservada. Uma relação criada pela IA não poderá ser confundida com uma anotação feita pelo usuário.

## Reader

O núcleo da experiência será o **Notely Reader**, inicialmente voltado para PDF.

O objetivo não é criar um editor de documentos genérico, mas um ambiente de leitura simples, no qual seja possível:

- abrir e navegar por PDFs;
- criar highlights;
- adicionar comentários;
- registrar dúvidas;
- recuperar anotações existentes no próprio PDF;
- consultar rapidamente o contexto de uma anotação;
- relacionar uma anotação com conhecimento anterior;
- reabrir o documento posteriormente sem perder o estado da leitura.

A interface deve priorizar o documento. Recursos de IA, grafo e pesquisa devem aparecer como apoio, e não disputar atenção com a leitura.

## Multimodal

O Notely terá uma camada multimodal para situações em que texto extraído do PDF não é suficiente.

Ela poderá ser usada para compreender elementos como:

- gráficos;
- tabelas;
- diagramas;
- equações;
- figuras;
- regiões selecionadas de uma página;
- páginas escaneadas.

Exemplo de interação futura:

```text
usuário seleciona um gráfico
        │
        ▼
"o que este gráfico está mostrando?"
        │
        ▼
Notely envia:
- recorte visual
- contexto textual próximo
- pergunta do usuário
- contexto relevante do conhecimento existente
        │
        ▼
resposta contextualizada
```

O modelo multimodal será tratado como um provider substituível. O projeto não deve depender permanentemente de um único modelo.

## Dúvidas e pesquisa

Uma anotação marcada como dúvida poderá futuramente seguir uma sequência de investigação:

```text
dúvida
 │
 ├── contexto da própria página
 │
 ├── outras anotações e documentos do usuário
 │
 └── pesquisa externa, quando solicitada
```

A pesquisa externa poderá utilizar web search ou um agente de pesquisa, mantendo sempre a separação entre:

- conteúdo do documento;
- anotações do usuário;
- inferências de modelos;
- informações obtidas externamente.

## Grafo de conhecimento

O Notely deverá transformar anotações em uma rede navegável de conhecimento.

Exemplo:

```text
Documento
   │
   └── possui anotação
           │
           ├── refere-se a -> Conceito
           ├── questiona -> Conceito
           ├── suporta -> Claim
           └── contradiz -> Claim
```

O grafo não será a fonte primária dos dados. Ele será uma projeção derivada e reconstruível a partir das informações persistidas no PostgreSQL.

A intenção é permitir consultas como:

- "onde já marquei algo sobre este conceito?";
- "quais documentos relacionam estes dois assuntos?";
- "quais dúvidas minhas continuam sem resposta?";
- "que anotações contradizem esta afirmação?";
- "onde eu já havia estudado algo semelhante?".

## Arquitetura de dados

A fundação será baseada em PostgreSQL e extensões complementares, cada uma com uma responsabilidade clara:

### PostgreSQL

Será a fonte de verdade do Notely.

Deverá armazenar documentos, páginas, anotações, comentários, dúvidas, claims, proveniência, sessões e demais entidades do domínio.

### TimescaleDB

Será utilizada para dados temporais e eventos de leitura, por exemplo:

- abertura de documento;
- visualização de página;
- criação de anotação;
- criação de dúvida;
- solicitação multimodal;
- pesquisa externa;
- projeção do grafo.

Isso permitirá estudar a evolução da leitura e do conhecimento ao longo do tempo sem transformar eventos em entidades do domínio principal.

### Apache AGE

Será responsável pela projeção do grafo de conhecimento.

As relações armazenadas no AGE deverão ser reconstruíveis. Nenhuma informação essencial poderá existir somente no grafo.

### pgvector

Será utilizado para recuperação semântica e busca por similaridade.

Ele permitirá localizar anotações ou trechos conceitualmente próximos mesmo quando ainda não existir uma relação explícita entre eles no grafo.

Embeddings deverão ser versionados por modelo para permitir reconstrução futura.

## Processamento assíncrono

O Notely utilizará **Transactional Outbox** para separar a interação de leitura do processamento pesado.

Uma ação como criar um highlight deverá seguir aproximadamente este fluxo:

```text
usuário cria highlight
        │
        ▼
transação PostgreSQL
        │
        ├── salva a anotação
        └── grava evento no outbox
        │
        ▼
Reader recebe confirmação
        │
        ▼
workers processam em segundo plano
        │
        ├── evento temporal
        ├── embedding
        ├── conceitos
        ├── grafo
        ├── multimodal, se necessário
        └── pesquisa, se solicitada
```

O usuário não deve precisar esperar por embeddings, grafo ou IA para continuar lendo.

## Princípios

O desenvolvimento do Notely seguirá alguns princípios centrais:

1. **O usuário lê.** A IA não substitui a leitura por padrão.
2. **O usuário decide o que é relevante.**
3. **Anotações são dados de primeira classe.**
4. **Proveniência não pode ser perdida.**
5. **Informação do usuário e inferência da IA devem permanecer distinguíveis.**
6. **PostgreSQL é a fonte de verdade.**
7. **AGE e pgvector são representações derivadas e reconstruíveis.**
8. **Processamento pesado deve acontecer fora do caminho crítico da leitura.**
9. **O Reader deve continuar simples.**
10. **A arquitetura deve permitir trocar modelos e providers sem reescrever o produto.**

## Estrutura inicial do repositório

```text
frontend/
backend/
  src/notely/
    api/
    core/
    db/
    workers/
    providers/
  tests/
infra/
  db/
    migrations/
docs/
.github/
AGENTS.md
README.md
```

Responsabilidades principais:

- `frontend/`: interface do Notely Reader;
- `backend/src/notely/api/`: fronteira HTTP/API;
- `backend/src/notely/core/`: domínio e regras de aplicação;
- `backend/src/notely/db/`: persistência e acesso aos bancos/extensões;
- `backend/src/notely/workers/`: consumers do outbox e tarefas assíncronas;
- `backend/src/notely/providers/`: multimodal, web search e integrações externas;
- `infra/db/migrations/`: migrações de banco;
- `docs/`: PRD, especificação técnica, ADRs e documentação do projeto;
- `AGENTS.md`: regras operacionais para agentes de desenvolvimento.

## Estado do projeto

O Notely está em fase inicial de definição e estruturação.

A primeira entrega útil deve ser pequena e verificável:

```text
abrir PDF
    ↓
selecionar texto
    ↓
criar highlight
    ↓
persistir
    ↓
fechar e reabrir
    ↓
highlight continua lá
```

Depois dessa base, entram enriquecimento assíncrono, busca semântica, grafo, multimodal e pesquisa.

O objetivo não é construir todas as capacidades de uma vez. É garantir primeiro que **a leitura e as anotações sejam sólidas**, para então adicionar inteligência sem comprometer o dado mais importante do sistema: aquilo que o usuário decidiu guardar.
