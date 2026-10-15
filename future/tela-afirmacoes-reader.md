# Plano — tela de afirmações e associações no Reader

**Estado:** implementado; a validação por clique do dono, que é o objetivo final, ainda está
pendente. O que existe hoje está registrado em "Validação da implementação", ao final.

**Issue:** [#22 — Tela de afirmações e associações no Reader](https://github.com/fcoelopes/notely/issues/22)

## Intenção

Fechar o ciclo da fase C pela leitura: a pessoa afirma algo a partir de um trecho que anotou,
vê o que afirmou com a evidência ao lado, remove o que não quer mais, e associa dois conceitos
que ela vê nas obras da sessão. Hoje isso só existe por HTTP, e por isso a fase C **nunca foi
usada de verdade** — a tabela `claims` está vazia na base real.

## Decisões de produto

Ratificadas na curadoria:

- **A afirmação nasce de uma anotação existente.** O domínio já exige `annotation_id`: a evidência
  é o trecho que a pessoa leu e marcou, e a tela não inventa anotação para poder afirmar. A ação
  fica no painel de anotações, em cada trecho.
- **A lista vive na barra lateral do Reader**, filtrada pelas obras da sessão aberta, mostrando
  documento, página e trecho de cada afirmação.
- **Conceito ↔ conceito entra nesta entrega.** Isso obriga a dar aos conceitos um lugar na tela,
  que hoje não existe em lugar nenhum, e um endpoint de leitura que a fase B não entregou.
- **`ANSWERED_BY` (dúvida → anotação que responde) fica fora.** Ela caberia no painel de
  anotações e o domínio já a suporta; ficou de fora porque a escolha da curadoria foi a associação
  entre conceitos. Fica registrada como o próximo passo mais barato.
- **Nada é aceito automaticamente.** Os conceitos que aparecem são extraídos por modelo ou
  heurística e continuam marcados como inferência; a tela diz isso com todas as letras. A
  associação que a pessoa cria é autoral.

## Estado atual que a tela usa

Rotas já existentes e testadas: `POST /api/claims`, `GET /api/claims`, `DELETE /api/claims/{id}`,
`POST /api/concept-relations` e `DELETE /api/claim-relations/{id}`. Nada muda nelas.

Falta no backend apenas a leitura de conceitos: `GET /api/concepts`, escopada por dono, devolvendo
os conceitos do usuário com a contagem de anotações que os referenciam e as associações
`RELATED_TO` existentes — a contagem é o que dá sentido ao que aparece (sem ela, a lista é uma
nuvem de termos ranqueados por frequência), e as associações existentes evitam que a tela ofereça
o que já foi afirmado e permitem remover.

A filtragem por sessão usa `document_id`, que a evidência de cada afirmação já carrega e que as
anotações de cada conceito também permitem resolver.

## Tela

- **Painel de anotações**: cada trecho ganha a ação de afirmar. O formulário pede o texto da
  afirmação e a relação com o trecho — **sustenta** ou **contradiz** —, e grava pelo
  `POST /api/claims`. Repetir o mesmo texto reaproveita a afirmação existente e só acrescenta
  evidência, como no domínio.
- **Painel de afirmações** (`<details>`, como o painel de tema): lista as afirmações das obras da
  sessão com documento, página e trecho; cada uma tem remoção. Afirmação sem evidência aparece
  marcada como tal, em vez de sumir — é o comportamento que o domínio garante desde a correção
  pós-revisão.
- **Painel de conceitos**: lista os conceitos das obras da sessão, com a contagem de trechos que
  os referenciam e um aviso de que são inferência. Selecionar dois e associar cria a `RELATED_TO`
  pelo `POST /api/concept-relations`; as associações existentes aparecem com remoção. A tela
  informa que a ordem do par é significativa hoje: afirmar a associação invertida cria outra
  aresta, limitante já registrada no plano da fase C.
- **Falha não bloqueia a leitura**: erro de gravação vira mensagem, o trecho e a navegação
  continuam funcionando, e o estado local só muda depois do sucesso — como já é o padrão do
  marcador de página.

## Ordem

1. `GET /api/concepts` no backend, escopado por dono, com teste de integração.
2. `src/types.ts` e `src/lib/api.ts`: tipos e funções das rotas de afirmação, associação e conceito.
3. Painel de afirmações, com o estado vazio orientando o primeiro uso.
4. Ação de afirmar no painel de anotações, com o formulário de relação.
5. Painel de conceitos e a associação entre dois.
6. Estilos, seguindo o vocabulário visual já existente.
7. Testes de componente; suíte do frontend e build.
8. Documentação: README do frontend e do backend, e o plano da fase C apontando para a tela.

## Critérios de aceite

- Cada anotação do painel oferece afirmar; o formulário exige texto, permite escolher sustenta ou
  contradiz e grava a afirmação ligada àquele trecho.
- A lista mostra as afirmações das obras da sessão com documento, página e trecho, e cada uma pode
  ser removida sem afetar a anotação que servia de evidência.
- Afirmação que perdeu a evidência continua listada, marcada como sem evidência.
- Falha da API em qualquer uma dessas ações mostra erro e não bloqueia ler, navegar ou anotar.
- Os conceitos das obras da sessão aparecem com a contagem de trechos e marcados como inferência.
- Associar dois conceitos cria a aresta; associar de novo não duplica; remover a associação
  funciona e não remove os conceitos.
- Nenhuma outra conta aparece: lista, remoção e associação são escopadas por dono.
- Suíte do frontend e build passando, suíte do backend sem regressão.

## Riscos e rollback

- A barra lateral já tem tema e anotações. Os painéis novos vão em `<details>` fechados, para não
  empurrar a leitura para fora da tela em telas pequenas.
- Nada de migração: a tela é aditiva e usa rotas que já existem, com uma leitura nova. Rollback é
  reverter o commit.
- Risco de qualidade: os conceitos que aparecem hoje vêm da heurística de frequência e têm
  contagem baixa. A tela mostra a contagem justamente para que a associação seja feita com
  critério, e nada é promovido a autoral sem o gesto da pessoa.

## Fora de escopo

- `ANSWERED_BY` na tela, embora o domínio já a suporte.
- Editar a redação de uma afirmação: reafirmar reaproveita sem alterar, por decisão de preservar
  proveniência. Corrigir texto exigiria mudar o domínio e o schema.
- Aceitar ou rejeitar conceitos sugeridos (a fase B deixou isso fora e continua fora).
- Sugestão de afirmação por modelo, que depende de provedor real validado.

## Validação da implementação

- **Backend.** `GET /api/concepts` em `backend/src/notely/api/app.py`, com o schema de resposta
  em `schemas.py`, o método `list_concept_overviews` em `core/services.py`, as dataclasses
  `ConceptOverview`, `ConceptReference` e `ConceptAssociation` em `core/concepts.py` e os três
  métodos novos de leitura em `UnitOfWork`/`SqlAlchemyUnitOfWork` (`list_concepts`,
  `list_concept_references`, `list_concept_associations`). Nada nas rotas da fase C mudou.
- **Teste de integração** `backend/tests/test_integration_concepts_api.py` (4 testes, PostgreSQL
  real + AGE, no padrão de `test_integration_claims.py`): a contagem por conceito e os documentos
  das anotações que o citam; a associação visível nas duas pontas com `outgoing`, nome do outro
  conceito, id da aresta, proveniência e status, e a remoção que não remove os conceitos; contas
  separadas, provando que lista, contagens e associações são escopadas por dono; e o conceito sem
  trecho, que continua listado com contagem zero.
- **Suíte do backend:** `203 passed, 5 warnings in 118.65s` em banco próprio
  (`notely_claims_ui`, criado só para esta execução, em `0015`), com
  `UV_CACHE_DIR`/`DOCKER_CONFIG` apontando para diretórios graváveis porque
  `test_dev_migrations.py` invoca `docker compose`. Os 4 testes novos somam aos 199 da fase C;
  nenhum teste anterior deixou de passar. As 5 advertências são as antigas
  (`DeprecationWarning` do alembic e `PytestReturnNotNoneWarning` em `test_api_auth.py`).
- **Suíte do frontend:** `68 passed` em 5 arquivos, e `npm run build` (que roda `tsc -b`)
  concluído sem erro. Antes desta entrega a suíte tinha 49 testes; entram 9 em
  `src/components/ClaimsPanels.test.tsx` e 10 em `src/App.test.tsx`.
- **Critérios de aceite, um a um:**
  - *Afirmar pelo formulário* — `App.test.tsx` "asserts from an annotated excerpt and lists the
    claim with its evidence": a ação **Afirmar** aparece em cada trecho, o formulário exige texto,
    a relação vai como `contradicts` e o `POST /api/claims` recebe `annotation_id`, `text`
    aparado e `relation`; a lista mostra documento, página e trecho.
  - *Reafirmar reaproveita* — "reuses the claim when the same text is asserted again": dois
    envios do mesmo texto, uma linha na lista e nenhum erro.
  - *Remover sem afetar a anotação* — "removes a claim without touching the annotation that was
    its evidence": `DELETE /api/claims/{id}` e a anotação continua no caderno, com a ação de
    afirmar ainda disponível.
  - *Afirmação sem evidência* — "marks a claim that lost its evidence instead of hiding it" e,
    no componente, "keeps a claim that lost its evidence marked instead of hiding it": a linha
    continua, marcada como **sem evidência**.
  - *Erro sem bloquear* — "shows the failure and keeps reading when asserting fails" e "keeps
    reading and annotating when the concept list is unavailable": a mensagem aparece no painel e
    junto ao formulário, o rascunho permanece, a anotação continua listada e a página navega.
  - *Conceitos com contagem e aviso* — "lists the concepts of the session with their excerpt
    count and the inference warning" e o teste de componente: contagem de trechos, documentos da
    sessão e o aviso de que são inferência de modelo ou heurística, não conhecimento confirmado.
  - *Associar sem duplicar* — "associates two concepts once and never duplicates the edge": a
    aresta nasce, aparece uma única vez e o par já associado deixa de ser oferecido.
  - *Remover associação* — "removes an association keeping both concepts".
  - *Nada de outra conta* — teste de integração que compara duas contas: conceitos homônimos com
    ids distintos, cada um com o seu lastro, e a associação de uma não aparece na outra. No
    cliente, "lists only the claims of the documents in the open session" garante que só as obras
    da sessão aberta entram nos painéis.
- **Filtragem por sessão** — verificada por `document_id` nos três painéis: `openSession` carrega
  `GET /api/claims` e `GET /api/concepts` uma vez, e a lista exibida cruza esses dados com
  `session.documents`.
- **Falha não bloqueante por construção:** nenhuma dessas ações toca o PDF, a navegação ou a
  criação de anotação; o estado local só muda depois da resposta bem-sucedida, e a lista é
  recarregada do servidor em vez de ser adivinhada no cliente.

## Limitações conhecidas

- **A ordem do par continua significativa.** `RELATED_TO` é simétrico no significado e ordenado
  no banco: associar `A → B` e depois `B → A` cria duas arestas. A tela avisa e mostra a direção,
  mas não canoniza o par; unificar exigiria a regra de canonização que ficou fora da fase C.
- **Afirmação sem evidência não sabe de que sessão é.** Ela perdeu o `document_id` junto com a
  anotação, então aparece em toda sessão aberta, marcada. É o preço de não esconder dado
  autoral; atribuí-la exigiria guardar a sessão na aresta.
- **Reafirmar não reescreve nem republica.** Reaproveitar a afirmação existente não altera texto
  nem proveniência e não gera evento: a redação não pode ser corrigida nesta entrega, e a
  reconciliação de uma projeção que falhou é `--rebuild`, como no plano da fase C.
- **Os conceitos que aparecem hoje são fracos.** Vêm da heurística de frequência (ou de um
  modelo, quando configurado) e costumam ter contagem baixa; a tela mostra a contagem justamente
  para que a associação seja feita com critério. Não há aceite ou rejeição de conceito sugerido.
- **Falta reavaliar em uso.** A tela foi validada por testes de componente e de integração, não
  por uma leitura real: o objetivo declarado do dono — clicar e ver a fase C funcionando — ainda
  não aconteceu. O que depende disso é sobretudo o vocabulário da interface e o tamanho dos
  painéis numa sessão com muitos conceitos.
- **A lista de conceitos carrega inteira.** `GET /api/concepts` não pagina nem filtra por
  documento: com centenas de conceitos a resposta cresce, e o filtro por sessão acontece no
  cliente. Paginar exigiria um parâmetro novo na rota, que não entrou aqui.
- **`test_integration_concepts_api.py` limpa o banco depois de cada teste** porque o `--rebuild`
  do worker de grafo conta o que existe no PostgreSQL: um conceito deixado no banco mudaria a
  expectativa de `test_integration_graph.py`, que roda em seguida. O fixture de lá continua
  sensível a resíduo de conceitos de outros arquivos.
