# ADR 0007 — Assistente de estudo com o modelo do próprio usuário

**Status:** Aceito
**Data:** 2026-10-08

## Contexto

O usuário quer plugar o próprio modelo (Claude ou GPT) para ajudar no estudo. Duas restrições do repositório determinam o desenho: a saída do modelo é inferência e não pode se confundir com o que o usuário escreveu nem com o que o documento afirma (AGENTS.md §1), e o documento integral não deve ser enviado para fora por padrão (PRD §18).

## Decisão

- **BYOK por usuário**: uma configuração por conta em `ai_model_configs`, com provider, modelo, base URL e chave. A chave é cifrada em repouso com `NOTELY_AI_SECRET_KEY`, **separada da chave de autenticação** para que o vazamento de uma não exponha a outra.
- **A chave nunca é devolvida pela API**: a resposta informa apenas `has_key`. Enviar `api_key` novamente é opcional; sem ela, a chave guardada é mantida.
- **Camada de chat compartilhada** (`providers/chat_model.py`), criada na fase B e reaproveitada aqui. Os adapters falam HTTP puro, sem SDK, e a configuração é explícita.
- **Assistente ancorado primeiro**: com uma sessão de estudo, o contexto vem do corpus indexado da sessão, por busca lexical, mais as anotações do usuário naquela sessão. Sem sessão é chat livre, e a resposta traz `grounded: false`.
- **Contexto mínimo**: limite de trechos, de anotações e de caracteres por item. O documento integral não é enviado.
- **Proveniência explícita no prompt**: o sistema instrui o modelo a citar documento e página, a declarar quando não há evidência suficiente e a não apresentar a anotação do estudante como afirmação do documento.
- **A resposta não é persistida**: ela não vira anotação, conceito nem conhecimento autoral. O desfecho informa `provider` e `model`, e guardar algo continua sendo decisão do usuário.
- **A chamada ao modelo acontece fora da unidade de trabalho**, como já ocorria na sugestão de tema: o contexto é lido em uma transação de leitura que termina antes da chamada.
- **A recuperação é uma porta da unidade de trabalho**, não uma consulta solta: o assistente herda o escopo de dono do restante da aplicação e não alcança material de outra conta.

## Consequências

- O custo e a escolha do provedor ficam com o usuário; sem configuração, o erro é explícito em vez de silencioso.
- Um provider indisponível responde `502` e não afeta leitura nem anotações. A verificação de que a resposta não vira anotação está coberta por teste.
- Limitações conhecidas: a recuperação é lexical e restrita a uma sessão de estudo — não há busca no acervo inteiro nem similaridade semântica, porque pgvector segue pendente; não há streaming nem histórico da conversa; e a resposta é instruída a citar, mas não é verificada automaticamente contra o texto.
- A interface do Reader ainda não expõe o assistente; o contrato HTTP está pronto e coberto por testes.
