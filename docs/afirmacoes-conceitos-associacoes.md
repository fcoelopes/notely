# Afirmações, conceitos e associações

O que o Notely faz com o conhecimento que deriva das suas anotações e com o que você afirma.
Escrito para responder "o que dá para fazer hoje" sem prometer o que não existe.

## A regra que separa as duas camadas

**Conceito é inferência. Afirmação é autoria.**

Um conceito é extraído das suas anotações por um provider — modelo ou heurística — e entra
marcado como `ai_inference`. Uma afirmação é escrita por você e entra marcada como
`user_authored`. As duas camadas convivem no grafo com essa diferença registrada em cada nó e em
cada aresta, e nada de inferência vira conhecimento seu sem uma ação explícita sua (AGENTS.md §1
e §6).

Consequência prática: um conceito pode ser removível por um `--rebuild` (ele é reconstruível a
partir das anotações), enquanto uma afirmação aceita não é reconstruível a partir de nada — por
isso a migration `0015` recusa o downgrade enquanto existir afirmação aceita.

## Conceitos

Termos extraídos das suas anotações, gravados em `concepts` e `annotation_concepts` com a relação
(`refers_to` para anotação, `questions` para dúvida), a proveniência, o status (`suggested`,
`accepted` ou `rejected`), o provider, o modelo e a confiança. Projetados no grafo como nós
`Concept` e arestas `REFERS_TO`/`QUESTIONS`.

Respondem **"onde eu já marquei algo sobre este conceito?"** — do conceito para o trecho, com
documento, página e citação. Ver [ADR 0005](adr/0005-concept-extraction-as-inference.md).

Estado hoje, medido na base real: **44 conceitos e 54 relações, todas `suggested`**. Quem extraiu
foi a heurística `frequency-v1`, que ranqueia termos por frequência: os nomes incluem coisas como
`natureza` e `afeta`. A qualidade vem de configurar um provider de modelo.

## Afirmações

Proposições escritas por você a partir de um trecho que você anotou, dizendo se aquele trecho a
**sustenta** ou a **contradiz**. Gravadas em `claims` e `claim_relations`, projetadas como nós
`Claim` e arestas `SUPPORTS`/`CONTRADICTS`. Respondem **"o que eu afirmo, e onde está a
evidência?"**. Ver [ADR 0008](adr/0008-claims-and-semantic-edges.md).

Como se comportam:

- **Uma afirmação por texto.** Repetir o mesmo texto (ignorando caixa e espaçamento) reaproveita a
  afirmação existente e apenas acrescenta evidência, permitindo que várias passagens sustentem a
  mesma afirmação.
- **A evidência é a anotação**, e não a afirmação: remover a última evidência não apaga a
  afirmação. Ela continua listada, marcada como sem evidência.
- **A remoção é suave.** O nó e as arestas saem do grafo, mas a linha permanece no PostgreSQL com
  `deleted_at`, e o `--rebuild` a ignora.
- **Reafirmar não reescreve.** O texto, a proveniência, o provedor e o modelo ficam como estavam.
  Corrigir a redação de uma afirmação não é possível hoje: exigiria mudar o domínio e o schema.
- **Não há sugestão por modelo.** O schema acomoda (`ai_inference` + `suggested`, com provedor e
  modelo preservados no aceite), mas não existe chamada: não há provedor real validado.

## Associações

`RELATED_TO` liga **dois conceitos**, e é a única aresta autoral entre duas coisas que não são
anotação. Você cria e remove pela tela do Reader ou pela API. O par é **ordenado**: associar na
ordem inversa cria outra aresta. A associação é sua; os conceitos associados continuam sendo
inferência.

## Dúvida respondida

`ANSWERED_BY` liga uma anotação do tipo `question` à anotação que a responde. **Existe no
domínio, na API (`POST /api/questions/{annotation_id}/answers`) e no grafo, mas não está na
tela**: não há como criá-la pelo Reader hoje. É o próximo passo mais barato da camada, porque o
painel de anotações já mostra dúvidas.

## O vocabulário do grafo

| Nó | Aresta | Significa | Origem |
|---|---|---|---|
| `Document` | `HAS_ANNOTATION` | o trecho pertence ao documento | estrutural |
| `Annotation`, `Question` | `REFERS_TO`, `QUESTIONS` | o trecho refere ou questiona um conceito | inferência |
| `Claim` | `SUPPORTS`, `CONTRADICTS` | o trecho sustenta ou contradiz a afirmação | autoral |
| `Concept` | `RELATED_TO` | você associou dois conceitos | autoral |
| — | `ANSWERED_BY` | a dúvida é respondida por aquele trecho | autoral, sem tela |

## O que dá para perguntar hoje

No Cypher do AGE, com `SET search_path = ag_catalog, public` antes. A alternância de rótulos
(`[:A|B]`) **não funciona nesta versão do AGE** — o filtro é por `label(r)`, dentro da consulta.

Afirmações e a evidência que as sustenta:

```sql
SELECT * FROM cypher('notely', $$
  MATCH (a)-[r]->(c:Claim)
  WHERE label(r) IN ['SUPPORTS', 'CONTRADICTS']
  RETURN c.text, label(r), a.source_id
$$) AS (afirmacao agtype, aresta agtype, anotacao agtype);
```

Onde já marquei algo sobre um conceito:

```sql
SELECT * FROM cypher('notely', $$
  MATCH (a)-[r]->(c:Concept)
  WHERE label(r) IN ['REFERS_TO', 'QUESTIONS']
  RETURN c.name, label(r)
$$) AS (conceito agtype, aresta agtype);
```

Quais conceitos aparecem nos trechos que sustentam uma afirmação — dois saltos, passando pela
anotação de origem, que é o que as duas camadas têm em comum:

```sql
SELECT * FROM cypher('notely', $$
  MATCH (a)-[:SUPPORTS]->(c:Claim)
  MATCH (a)-[:REFERS_TO]->(k:Concept)
  RETURN c.text, k.name
$$) AS (afirmacao agtype, conceito agtype);
```

Da tela, os mesmos dados aparecem nos painéis de afirmações e de conceitos da barra lateral do
Reader, filtrados pelas obras da sessão aberta.

## Lacunas conhecidas

- **Não existe aresta entre `Claim` e `Concept`.** As duas camadas se tocam apenas pela anotação de
  origem, então "quais afirmações tratam deste conceito?" só sai por caminho indireto, e afirmar
  *sobre* um conceito não é algo que o modelo represente.
- **Não há como aceitar ou rejeitar um conceito.** Os estados `accepted` e `rejected` existem no
  schema e nenhuma rota ou tela os altera: tudo que foi extraído permanece `suggested`, e o grafo
  mostra inferência distinguível, não conhecimento confirmado.
- **Sem agrupamento semântico.** `ativos`, `assets` e `ativo` são conceitos distintos enquanto
  pgvector não entrar.
- **A extração de conceitos pode usar a sua chave, mas a sugestão de tema não.** A configuração
  de modelo do assistente (`PUT /api/ai-config`) alimenta a extração de conceitos do seu próprio
  acervo; a sugestão de tema continua vindo de variáveis de ambiente do servidor. Ver abaixo.
- Reextrair acumula arestas antigas da mesma anotação até um `--rebuild`, que reconcilia o grafo
  com o PostgreSQL.

## Qual modelo extrai os conceitos

A extração resolve o provider **por dono da anotação**, nesta ordem:

1. a configuração de modelo que o dono cadastrou para o assistente (`PUT /api/ai-config`, cifrada
   em `ai_model_configs`), quando existe, está ligada e tem chave;
2. o provider do **servidor**, por variáveis de ambiente;
3. o heurístico local, determinístico e sem rede.

Ou seja: cadastrar a sua chave melhora os conceitos do seu acervo. É uma configuração, dois usos —
o mesmo modelo responde no assistente e extrai os conceitos —, e a linha de `annotation_concepts`
registra o provider e o modelo que de fato extraíram (`<provider>:concepts` e o modelo do dono).

O worker precisa de `NOTELY_AI_SECRET_KEY` para decifrar a sua chave; é a mesma variável que cifra
o segredo do assistente. Sem ela, ou com a chave ilegível, a extração **avisa no log e cai** para o
provider do servidor e, sem ele, para o heurístico: a anotação nunca fica presa por causa de uma
configuração de ambiente. Com a configuração desligada o caminho é o mesmo, sem aviso — é o
comportamento de quem não tem configuração. Já a falha do modelo em si (chave inválida, sem cota,
provedor fora) preserva a anotação e deixa a entrega pendente para nova tentativa.

Sem nenhuma chave cadastrada, o comportamento é o do servidor:

```bash
NOTELY_CONCEPT_PROVIDER=anthropic NOTELY_CONCEPT_MODEL=claude-... NOTELY_CONCEPT_API_KEY=... \
  uv run --project backend python -m notely.workers.concepts
```

Valores de `NOTELY_CONCEPT_PROVIDER`: `heuristic` (padrão, local, sem rede) ou `anthropic` e
`openai_compatible`. Valem também `NOTELY_CONCEPT_ENDPOINT` e `NOTELY_CONCEPT_TIMEOUT_SECONDS`.

**Atenção:** a sugestão de tema continua vindo de variáveis de ambiente do servidor, não da
configuração do assistente. Ver [ADR 0005](adr/0005-concept-extraction-as-inference.md) e
[ADR 0007](adr/0007-study-assistant-with-user-model.md).

## Onde isso vive

- Rotas: `GET /api/claims`, `POST /api/claims`, `DELETE /api/claims/{claim_id}`,
  `POST /api/concept-relations`, `DELETE /api/claim-relations/{relation_id}`,
  `GET /api/concepts` e `POST /api/questions/{annotation_id}/answers`.
- Domínio e persistência: `backend/src/notely/core/claims.py`, `core/concepts.py`,
  `core/models.py` e `db/uow.py`.
- Projeção: `backend/src/notely/db/graph.py` e `workers/graph.py` — o worker de grafo é o único
  escritor no AGE.
- Extração: `backend/src/notely/workers/concepts.py` e `providers/concept_extraction.py`.
- Migrations: `0010` (conceitos), `0015` (afirmações e as quatro arestas).
- Tela: `frontend/src/components/ClaimPanel.tsx`, `ConceptPanel.tsx` e `AnnotationPanel.tsx`.
