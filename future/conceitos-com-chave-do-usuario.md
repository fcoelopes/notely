# Plano — extração de conceitos com a chave do usuário

**Estado:** implementado; aguardando revisão

**Issue:** [#23 — Extração de conceitos com a chave do usuário](https://github.com/fcoelopes/notely/issues/23)

## Intenção

Fazer a extração de conceitos usar a chave de modelo que o usuário já cadastrou para o
assistente. Hoje essa chave alimenta só o assistente: `workers/concepts.py` lê
`NOTELY_CONCEPT_*` do ambiente do servidor, então cadastrar a própria chave não melhora os
conceitos. O ADR 0005 e o plano da fase B prometiam BYOK e o código não cumpria; a documentação
foi corrigida para dizer a verdade, e agora a lacuna é fechada no código.

## Decisões

- **Uma configuração, dois usos.** A extração usa o mesmo `ai_model_configs` do assistente
  (provider, modelo, base URL e chave cifrada). Não há uma segunda tela nem um segundo cadastro.
- **Precedência:** chave do dono da anotação → provider do servidor (`NOTELY_CONCEPT_*`, útil para
  quem não cadastrou nada) → heurístico (mantém o fluxo offline e testável).
- **A proveniência registra quem extraiu.** A linha de `annotation_concepts` passa a levar o
  provider e o modelo reais do usuário; o `ModelConceptExtractionProvider` já expõe
  `name = "<provider>:concepts"` e `model` do chat.
- **Degradação declarada.** Sem a chave do servidor que decifra (`NOTELY_AI_SECRET_KEY`) ou com
  chave ilegível, o worker **avisa no log e cai para o provider do servidor ou para o
  heurístico**. Não trava a entrega em retry infinito por uma configuração de ambiente, e o aviso
  diz exatamente o que aconteceu.
- **O custo é do usuário**, e a extração continua assíncrona: nada bloqueia a leitura.
- **Falha do modelo do usuário** (chave inválida, sem cota, provedor fora) preserva a anotação e
  deixa a entrega pendente com backoff, como já é o comportamento do consumidor `concepts`.

## Escopo técnico

1. `workers/concepts.py`: resolver o provider **por dono da anotação**, dentro do lote, em vez de
   um provider único por processo. A leitura da configuração usa a unidade de trabalho já escopada
   ao dono (`get_ai_model_config`), e a decifra usa `SecretCipher` com `NOTELY_AI_SECRET_KEY`. Um
   cache por lote evita reler a configuração a cada anotação do mesmo dono, e a chave decifrada
   não vive além do provider que a usa.
2. Pré-requisito operacional: o processo do worker precisa de `NOTELY_AI_SECRET_KEY` para decifrar.
   `scripts/dev.sh` passa a exportar as duas chaves de segredo lidas do `.env`, para que o
   ambiente local funcione sem passo manual.
3. Testes: dono com configuração ativa usa o modelo do usuário e registra provider/model reais;
   dono sem configuração cai no provider do servidor ou no heurístico; chave ilegível avisa e
   degrada; falha do modelo deixa a entrega pendente.
4. Documentação: ADR 0005 (a decisão de provider passa a incluir a chave do usuário), plano da
   fase B, `docs/afirmacoes-conceitos-associacoes.md` (a seção que hoje avisa "não é BYOK"),
   `backend/README.md` e o README do frontend se mencionar o assunto.

## Critérios de aceite

- Com configuração ativa, a extração de uma anotação daquele dono usa o modelo dele, e a linha
  gravada registra o provider e o modelo usados.
- Sem configuração, o comportamento atual é preservado (provider do servidor, se houver;
  heurístico, se não).
- A chave do usuário nunca aparece em log, no grafo ou em resposta de API, e continua cifrada em
  repouso.
- Chave ilegível ou ausente do servidor não derruba o worker: aviso no log e degradação para a
  alternativa configurada.
- Falha do provider do usuário preserva a anotação e deixa a entrega pendente para retry.
- Nenhuma migration: o schema já tem `provider` e `model` na relação, e `ai_model_configs` já
  existe.
- Suíte do backend sem regressão.

## Riscos e rollback

- Custo: extrair com modelo pago consome tokens do usuário a cada anotação nova. O worker já é
  assíncrono e a extração é idempotente (reprocessar não chama o provider de novo quando a
  relação existe), mas o custo passa a existir para quem cadastra chave. Está declarado; desligar
  a configuração volta ao heurístico.
- Desempenho: uma leitura de configuração por dono por lote, em uma transação de leitura própria
  além da do lote. O cache é em memória e não atravessa lotes; não há medição de carga real que
  justifique mais do que isso agora.
- Rollback: reverter o commit devolve o comportamento de provider único por ambiente. Não há
  migração nem dado a desfazer.

## Fora de escopo

- Extração de afirmações por modelo.
- Aceitar ou rejeitar conceitos.
- Uma configuração de modelo separada para a extração, distinta da do assistente.

## Validação da implementação

- Suíte do backend inteira, em banco descartável próprio (`create database`, `alembic upgrade
  head`, `NOTELY_TEST_DATABASE_URL` sempre explícita): **218 testes passaram**, 15 deles novos —
  7 de unidade da regra de configuração (`tests/test_study_assistant_config.py`) e 8 de integração
  do pipeline de conceitos.
- Dono com configuração ativa usa o modelo dele e a linha grava a proveniência real
  (`tests/test_integration_concepts.py::test_owner_model_config_extracts_with_the_owner_key`): a
  chave cifrada é decifrada com `NOTELY_AI_SECRET_KEY`, o factory de chat recebe a chave em claro,
  a relação fica com `provider = "openai_compatible:concepts"` e `model` do dono, e o `event_key`
  publicado leva o mesmo provider. O provider do servidor não é chamado.
- A resolução é mesmo por dono, dentro do mesmo lote
  (`::test_each_owner_uses_its_own_provider_in_the_same_batch`): duas anotações de um dono
  configurado e uma de um dono sem configuração no mesmo lote — cada relação registra o provider
  de quem a escreveu, e a configuração é lida uma vez por dono (cache por lote).
- Sem configuração, o comportamento atual é preservado
  (`::test_owner_without_configuration_uses_the_server_provider`,
  `::test_owner_without_configuration_falls_back_to_the_heuristic` e
  `::test_disabled_configuration_keeps_the_server_provider`): provider do servidor e, sem ele, o
  heurístico `heuristic`/`frequency-v1`; configuração desligada segue o mesmo caminho, sem aviso.
- Chave ilegível não derruba o worker
  (`::test_missing_server_secret_key_warns_and_falls_back` e
  `::test_owner_key_that_cannot_be_decrypted_warns_and_falls_back`): o lote conclui, a extração
  acontece pela alternativa, o aviso no log cita a variável ausente ou a falha de decifra, e o
  provider do dono nem chega a ser montado. O teste também confere que a chave em claro não
  aparece no log.
- Falha do provider do usuário preserva a anotação e deixa a entrega pendente
  (`::test_owner_provider_failure_keeps_the_annotation_and_retries_later`): a extração tentou com a
  chave do dono, nenhuma relação foi gravada, a anotação continua intacta e a entrega fica com
  `attempt_count = 1`.
- `scripts/dev.sh`: sintaxe conferida com `bash -n`; a leitura pontual do `.env` foi exercitada à
  parte, sem imprimir valores, confirmando que as duas linhas viram chaves Fernet válidas e que um
  valor já exportado no ambiente não é sobrescrito.
- Nenhuma migration: o banco de teste fica em `0015 (head)` e o schema já tinha `provider` e
  `model` em `annotation_concepts` e a tabela `ai_model_configs`. Rotas, schemas e respostas da API
  não mudaram.
- O teste de migrations do repositório exige `UV_CACHE_DIR` gravável no sandbox; com ele, os 8
  testes de `test_dev_migrations.py` passam junto do restante da suíte.

## Limitações conhecidas

- **O custo passa a ser do dono.** Cada anotação nova de quem cadastrou chave consome tokens do
  modelo que ele escolheu. A extração continua assíncrona e idempotente: reprocessar não chama o
  modelo de novo quando a relação já existe.
- **O worker precisa da chave do servidor.** Sem `NOTELY_AI_SECRET_KEY` no ambiente do processo,
  a chave do dono não é decifrável e a extração degrada com aviso. No ambiente local o
  `scripts/dev.sh` exporta a variável a partir do `.env`; em produção ela tem que estar no
  ambiente do worker, não só no da API (que lê o `.env` pelo pydantic-settings).
- **Cache por lote.** A configuração é lida uma vez por dono em cada lote e o cache não atravessa
  lotes: uma troca de modelo passa a valer no lote seguinte. Como a extração é idempotente,
  anotações já extraídas não são reextraídas com o modelo novo — para isso é preciso apagar as
  relações ou usar o backfill com outro provider na chave do pedido.
- **Degradação silenciosa para o usuário.** Quem cadastrou chave e está sem `NOTELY_AI_SECRET_KEY`
  no worker recebe conceitos do provider do servidor ou do heurístico sem aviso na interface; o
  aviso existe apenas no log do worker.
- **Um modelo para tudo.** Não há como escolher um modelo só para a extração: é a mesma
  configuração do assistente, e desligá-la (`enabled = false`) devolve a extração ao provider do
  servidor e ao heurístico.
- **A sugestão de tema continua por ambiente.** A configuração do usuário não alimenta
  `providers/topic.py`.
- **Chave ilegível não gera retry.** A degradação evita entrega presa, mas também esconde o
  problema: corrigir `NOTELY_AI_SECRET_KEY` só muda o resultado das anotações seguintes, não das
  que já foram extraídas pela alternativa.
