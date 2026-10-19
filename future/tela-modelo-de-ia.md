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
- O nome do modelo é digitado; não há lista remota de modelos por provedor.
- A configuração é uma só para assistente e extração de conceitos: não há modelo separado por
  finalidade.
- A suíte cobre a tela com a API dublada; não há teste de ponta a ponta contra um provedor
  real, e a suíte do backend não foi executada porque nada no backend mudou.

