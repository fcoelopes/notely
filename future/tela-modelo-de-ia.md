# Plano — tela de configuração do modelo de IA

**Estado:** implementado

**Issue:** [#24 — Tela de configuração do modelo de IA](https://github.com/fcoelopes/notely/issues/24)

## Intenção

Tirar o cadastro da chave do `curl`. A #8 entregou API, domínio e cifra, mas deixou a
configuração sem interface: hoje só se cadastra um modelo com três comandos no terminal, e é a
única coisa no Notely que ainda exige isso. Com a tela, qualquer pessoa configura o próprio
modelo e passa a usar o assistente e a extração de conceitos.

## Decisões

- **Painel na barra lateral do Reader**, em `<details>` como o tema, as afirmações e os
  conceitos — a configuração é do usuário, não da sessão, e por isso fica no mesmo lugar das
  outras preferências da leitura.
- **A chave nunca volta da API**, só `has_key`. O campo de chave nasce vazio e, deixado vazio,
  mantém a chave já gravada: trocar modelo ou base URL não exige redigitar o segredo.
- **Provedor decide os campos.** `anthropic` precisa de modelo e chave (o endpoint é o oficial);
  `openai_compatible` precisa também da base URL, porque cobre Ollama, LM Studio, vLLM e gateways
  compatíveis. A tela exige o que o provedor exige, em vez de deixar a API recusar depois.
- **A chave é campo de senha**, com opção de mostrar, e não aparece em log nem em resposta.
- **Remoção explícita**, com confirmação no próprio painel, chamando o `DELETE` que já existe e
  deixa a rota em `404` na leitura seguinte.
- **Falha da API não perde o que foi digitado**: a mensagem de erro acompanha o formulário e os
  campos permanecem preenchidos. A leitura continua funcionando.
- **Nenhuma mudança de backend**: `GET`, `PUT` e `DELETE /api/ai-config` já existem, com a chave
  cifrada em repouso por `NOTELY_AI_SECRET_KEY`.

## Escopo

1. `frontend/src/types.ts` e `src/lib/api.ts`: tipos e as três funções, tratando `404` como
   "sem configuração" em vez de erro.
2. `frontend/src/components/AIModelPanel.tsx`: estado atual (`provider`, `model`, `base_url`,
   `enabled`, `has_key`), formulário de gravação, remoção e mensagens de sucesso e falha.
3. `frontend/src/App.tsx`: carregar o estado uma vez, montar o painel na barra lateral e manter o
   padrão do marcador de página: o estado local só muda depois do sucesso da API.
4. `frontend/src/styles.css`: no vocabulário visual existente.
5. Testes de componente e de integração com a API dublada; suíte do frontend e build.
6. Documentação: `frontend/README.md`, a seção do assistente no `backend/README.md` e o plano da
   #8, que passa a apontar a tela.

## Critérios de aceite

- Sem configuração, o painel mostra o formulário vazio e diz que não há modelo configurado.
- Com configuração, mostra provedor, modelo, base URL e que existe chave (`has_key`), sem expor a
  chave.
- Salvar sem preencher a chave mantém a chave gravada e atualiza os outros campos.
- Escolher `openai_compatible` exige a base URL antes de enviar; `anthropic` dispensa.
- Remover com confirmação deixa o painel no estado sem configuração, e a API responde `404` na
  leitura seguinte.
- Falha da API mostra a mensagem e preserva os campos digitados; ler e navegar continuam.
- Nenhuma resposta, log ou estado da tela expõe a chave.
- Suíte do frontend e build passando; suíte do backend sem regressão.

## Riscos e rollback

- O risco principal é a chave vazar para o estado da tela, para o log ou para um teste. O campo é
  de senha, a API nunca a devolve e os testes usam chave fictícia.
- Nada de migração; sem mudança de schema ou de rota. Rollback é reverter o commit, e a
  configuração já gravada continua válida pela API.
- Sem validação da chave contra o provedor no cadastro: uma chave errada só aparece no primeiro
  uso (assistente ou extração). Testar na hora exigiria uma chamada de modelo no cadastro, que é
  custo e latência na interação — fica fora, declarado.

## Fora de escopo

- A tela de conversa com o assistente (perguntar no Reader).
- Validar a chave contra o provedor no momento do cadastro.
- Lista remota de modelos por provedor: o nome do modelo é digitado.
- Escolher um modelo diferente para a extração de conceitos: a configuração é uma só.

## Validação da implementação

Frontend, sem tocar no backend:

- `npm test`: 7 arquivos, 88 testes passando (`AIModelPanel.test.tsx` com 10, `api.test.ts`
  com 5, `App.test.tsx` com 47, os demais inalterados).
- `npm run build`: `tsc -b && vite build` sem erro; apenas o aviso já existente de tamanho de
  chunk.

Cada critério de aceite e onde ele é verificado:

| Critério | Verificação |
| --- | --- |
| Sem configuração, formulário vazio e aviso de que não há modelo | `AIModelPanel.test.tsx` (vazio) e `App.test.tsx` "configures the model from the sidebar", com `getAIModelConfig` devolvendo `null` |
| Com configuração, mostra provedor, modelo, base URL e `has_key` sem a chave | `AIModelPanel.test.tsx` "shows provider, model, base url…" (campo de chave vazio e `type="password"`) e `App.test.tsx` "keeps the stored key…" |
| Salvar com a chave em branco mantém a chave e atualiza os campos | `AIModelPanel.test.tsx` "keeps the stored key out of the payload…" (`api_key` ausente) e `App.test.tsx` "keeps the stored key when saving…" |
| `openai_compatible` exige base URL; `anthropic` dispensa | `AIModelPanel.test.tsx` "requires the base url…" e `App.test.tsx` "does not send openai_compatible without a base url" |
| Remover com confirmação volta ao estado vazio e a leitura seguinte é `404` | `AIModelPanel.test.tsx` "asks for confirmation…" e "goes back to the empty state…"; `App.test.tsx` "removes the configuration after confirmation…"; `api.test.ts` "trata o 404 da leitura como ausência de configuração" |
| Falha da API mostra a mensagem, preserva os campos e não bloqueia a leitura | `AIModelPanel.test.tsx` "shows the api failure…" e `App.test.tsx` "shows the configuration failure and keeps reading" (a página seguinte é renderizada) |
| Nenhuma resposta, log ou estado da tela expõe a chave | `App.test.tsx` verifica que a chave fictícia não aparece no texto da página e que o campo volta vazio; `api.test.ts` verifica que a resposta da leitura só traz `has_key`; a implementação não usa log, URL ou armazenamento local |

O 404 não é mais tratado no componente: `getAIModelConfig` o converte em `null`, e o erro
genérico continua subindo como `ApiError`.

## Limitações conhecidas

- O painel vive na barra lateral do Reader, que só existe dentro de uma sessão de estudo: não
  há configuração na tela da biblioteca.
- A chave não é validada contra o provedor no cadastro; uma chave errada só aparece no
  primeiro uso do assistente ou da extração.
- O nome do modelo era digitado nesta etapa; a segunda etapa passou a listar os modelos do
  próprio provedor, com "outro (digitar)" para o que a lista não traz.
- A configuração é uma só para assistente e extração de conceitos: não há modelo separado por
  finalidade.
- A suíte cobre a tela com a API dublada; não há teste de ponta a ponta contra um provedor
  real, e a suíte do backend não foi executada porque nada no backend mudou.


## Lista de modelos (segunda etapa)

Digitar o nome do modelo é onde se erra, e uma lista fixa no código envelhece em semanas. A lista
é pedida ao próprio provedor, com a credencial que o usuário acabou de digitar:

- **Backend:** `POST /api/ai-config/models` com `{provider, api_key?, base_url?}`. Sem `api_key`,
  usa a chave já gravada (a mesma semântica do `PUT`). Responde a lista de modelos do provedor.
  - `anthropic`: `GET https://api.anthropic.com/v1/models` com `x-api-key` e `anthropic-version`.
  - `openai_compatible`: a partir do endpoint configurado, troca o sufixo `/chat/completions` por
    `/models`; sem esse sufixo, responde `422` explicando o formato esperado — em vez de tentar
    adivinhar a URL.
  - Erros distintos: sem configuração nem chave → `409`; falha do provedor → `502` com mensagem
    clara; a chave nunca aparece em erro, log ou resposta.
- **Frontend:** o campo de modelo vira uma lista, com "outro (digitar)" como saída para modelos que
  o provedor não devolve e para endpoints locais. A lista é carregada por um botão explícito
  enquanto não há chave salva, e automaticamente quando já existe configuração. Falha ao listar
  não impede salvar digitando.
- **Rótulo do endpoint:** o campo do compatível com OpenAI passa a dizer que é a URL de
  `/chat/completions`, porque é isso que o adapter chama; hoje o rótulo "base URL" induz a erro.
- **Testes:** listagem com providers falsos (sem rede), os três erros, o fallback de digitar e a
  troca de sufixo. `POST /api/ai-config/models` é coberto com teste de integração.

### Decisões tomadas na implementação

- **A listagem mora no adapter, a tradução de erro no domínio.** `providers/chat_model.py` ganhou
  `list_anthropic_models`, `list_openai_compatible_models`, `models_endpoint_for` e os dois
  conjuntos de headers que os adapters de chat já usavam (extraídos para `_anthropic_headers` e
  `_openai_compatible_headers`, em vez de repetir strings). Quem decide status HTTP é
  `core/study_assistant.py`, que traduz `InvalidModelEndpointError` em
  `AssistantInvalidEndpointError` (`422`) e `ChatModelListingError` em `AssistantUnavailableError`
  (`502`); `409` continua sendo o `AssistantNotConfiguredError` das outras rotas.
- **A rota não grava nada.** A chave digitada vale só para aquela chamada; `list_models` lê a
  configuração numa transação de leitura que termina antes do HTTP, como o `ask` faz com o
  contexto.
- **`models_endpoint_for` recusa em vez de adivinhar**, e a mensagem do `422` diz o formato
  esperado. Lista vazia é resposta legítima: a tela cai no campo de texto.
- **A tela só troca o campo de texto pela lista quando a lista tem algo a oferecer.** Modelo
  gravado que o provedor não devolve mantém o campo livre, em vez de apagar o que está
  configurado. "outro (digitar)" preserva o valor atual no campo de texto.
- **A lista é pedida de novo a cada mudança de configuração confirmada.** É uma chamada ao
  provedor por leitura/gravação, aceita em troca de não manter cache de catálogo alheio no
  servidor.

## Validação da implementação (segunda etapa)

Backend, com banco próprio (`notely_test_modellist`) e transporte do provedor dublado:

- `backend/.venv/bin/python -m pytest` (com `NOTELY_TEST_DATABASE_URL`, `UV_CACHE_DIR` e
  `DOCKER_CONFIG` exportados): **244 passed** em 147,69 s. Os arquivos novos são
  `test_model_listing.py` (19 testes, sem rede e sem banco) e
  `test_integration_model_listing.py` (7 testes contra o PostgreSQL).

Frontend:

- `npm test`: 7 arquivos, **99 testes** passando (`AIModelPanel.test.tsx` com 17 — eram 10 —,
  `api.test.ts` com 7 — eram 5 —, `App.test.tsx` com 49 — eram 47 —; os demais inalterados).
- `npm run build`: `tsc -b && vite build` sem erro; só o aviso já existente de tamanho de chunk.

Cada critério de aceite e onde ele é verificado:

| Critério | Verificação |
| --- | --- |
| A rota lista os modelos dos dois provedores | `test_model_listing.py` "anthropic_listing_uses_the_official_endpoint…" e "openai_compatible_listing_swaps_the_chat_completions_suffix"; `test_integration_model_listing.py` "listing_uses_the_stored_key_and_the_stored_endpoint" e "the_anthropic_listing_goes_to_the_official_endpoint" |
| Sem `api_key`, usa a chave gravada; com ela, usa a digitada e não grava | `test_model_listing.py` "without_a_typed_key_the_stored_key_is_decrypted_and_used" e "a_typed_key_lists_models_before_anything_is_saved"; `test_integration_model_listing.py` "a_typed_key_is_used_without_being_stored" (nenhuma linha em `ai_model_configs`) |
| `409` sem configuração nem chave | `test_model_listing.py` "listing_without_a_configuration_and_without_a_key_is_a_missing_setup" e "a_configuration_without_a_stored_key_still_needs_the_key"; `test_integration_model_listing.py` "listing_without_a_configuration_and_a_key_is_a_409" |
| `422` para endpoint sem `/chat/completions`, com o formato esperado | `test_model_listing.py` "an_endpoint_without_the_chat_completions_suffix_is_refused" e "openai_compatible_listing_requires_the_chat_completions_url"; `test_integration_model_listing.py` "a_stored_endpoint_without_the_chat_completions_suffix_is_a_422" (nada é chamado) |
| `502` para falha do provedor | `test_model_listing.py` "a_failure_of_the_provider_is_reported_without_the_key" e "a_provider_failure_is_an_unavailable_provider"; `test_integration_model_listing.py` "a_provider_failure_is_a_502_without_exposing_the_key" |
| A chave não sai em erro, log ou resposta | As asserções `PROVIDER_KEY not in …` nos casos de `422` e `502`; `test_integration_model_listing.py` verifica o corpo da resposta; `App.test.tsx` verifica que a chave digitada não aparece no texto da página |
| Lista carregada automaticamente com configuração, e por botão sem ela | `AIModelPanel.test.tsx` "lists the provider models automatically when a configuration exists" e "lists on demand with the typed key while there is no saved configuration"; `App.test.tsx` "lists the provider models automatically and saves the chosen one" |
| Escolha pela lista grava o modelo escolhido | `AIModelPanel.test.tsx` "lists the provider models automatically…" e `App.test.tsx` "lists the provider models automatically and saves the chosen one" (`model` no corpo do `PUT`) |
| "outro (digitar)" volta ao texto livre preservando a digitação | `AIModelPanel.test.tsx` "goes back to the free text field when 'outro (digitar)' is chosen" e "keeps the free text field when the configuration model is not in the list" |
| Falha ao listar não impede salvar digitando | `AIModelPanel.test.tsx` "keeps the typed model when the provider list fails" e "reports a provider failure when the list does not come, without blocking typing"; `App.test.tsx` "does not block saving by typing when the model list fails" |
| Rótulo do endpoint vira a URL de `/chat/completions` | `AIModelPanel.test.tsx` "requires the chat completions url for openai_compatible…" (rótulo, `placeholder` e ajuda) e `App.test.tsx` "does not send openai_compatible without a base url" |
| Nenhuma migration, nenhuma dependência nova, três rotas antigas intactas | `git diff` sem `infra/db/migrations` e sem `pyproject.toml`/`package.json`; os testes de `test_api_assistant.py` e `test_study_assistant_config.py` seguem passando sem alteração |

## Limitações conhecidas (segunda etapa)

- **A lista da Anthropic usa sempre o endpoint oficial.** Quem gravou um `base_url` próprio para a
  Anthropic (um proxy, por exemplo) continua conversando pelo proxy, mas lista os modelos em
  `api.anthropic.com`. O plano pedia o endpoint fixo; respeitar o proxy exigiria saber o caminho
  de `/models` dele.
- **Só a primeira página do catálogo é lida.** A API da Anthropic pagina `GET /v1/models` e
  devolve `has_more`; a rota não segue paginação. Quem tiver mais modelos do que a primeira página
  usa "outro (digitar)".
- **Cada abertura do painel com configuração salva (e cada gravação) faz uma chamada ao
  provedor.** Não há cache de catálogo: ele muda sem aviso e um cache no servidor poderia servir
  lista velha para outra chave.
- **A listagem não valida a escolha.** Escolher um modelo da lista não prova que a chave tem
  acesso a ele; isso continua aparecendo no primeiro uso, como antes.
- **A chave digitada trafega para a listagem mesmo sem ser gravada.** É inevitável: o provedor não
  lista modelos sem credencial. Ela não é persistida por essa rota.
- **Nenhum teste de ponta a ponta contra provedor real**: os adapters são testados com o
  transporte dublado, e a integração usa o PostgreSQL de verdade mas não a rede.
