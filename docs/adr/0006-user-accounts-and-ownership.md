# ADR 0006 — Contas de usuário, sessão opaca e dono por registro

**Status:** Aceito
**Data:** 2026-10-08

## Contexto

O Notely não tinha identidade: todas as tabelas de domínio eram globais e a API não tinha autenticação. Introduzir login com segundo fator exige decidir como a senha e o segredo do segundo fator são protegidos, como a sessão é transportada, como o domínio passa a ter dono e como uma mudança dessa amplitude entra sem quebrar o acervo já existente.

## Decisão

- **Senha com `hashlib.scrypt`** da biblioteca padrão, com os parâmetros gravados junto do hash, salt novo por senha e comparação em tempo constante. Evitou uma dependência.
- **TOTP do RFC 6238 implementado com `hmac`/`hashlib`**, coberto pelos vetores do próprio RFC. Também evitou uma dependência, e o risco de uma implementação incorreta fica preso ao teste dos vetores oficiais.
- **Segredo do segundo fator cifrado em repouso** com Fernet (`cryptography`), a única dependência nova e justificada: escrever criptografia própria para evitar o pacote seria pior. A chave vem de `NOTELY_AUTH_SECRET_KEY`; sem ela, ativar o segundo fator falha com erro de configuração em vez de gravar o segredo em claro.
- **Sessão opaca em tabela própria**, guardando apenas o hash do token, entregue em cookie `HttpOnly`, `SameSite=lax` e `Secure` por configuração. Uma sessão com segundo fator ativo nasce no estágio `mfa_pending` e **não autentica** até o segundo fator ser concluído.
- **Códigos de recuperação de uso único**, guardados como hash. O consumo é um `UPDATE` com `WHERE used_at IS NULL`, o que garante o uso único sem depender de checagem prévia.
- **Bloqueio temporário por tentativas falhas**, registrado em tabela. Uma falha de login não distingue e-mail inexistente de senha errada, inclusive no tempo de resposta: e-mail desconhecido verifica a senha contra um hash descartável.
- **Dono por registro** nas tabelas onde a posse é fato de domínio. Tabelas puramente derivadas — páginas e chunks do corpus, eventos de leitura e relação anotação → conceito — resolvem o dono pelo registro de origem em vez de duplicar a coluna.
- **Escopo na unidade de trabalho**: o `uow` recebe o dono, filtra as leituras e marca as escritas. O serviço de domínio não mudou de assinatura, e esquecer um filtro não vaza dado alheio, porque o `uow` é o único caminho de acesso ao banco.
- **Eventos de outbox carregam o dono**, marcados pela própria unidade de trabalho, para que as projeções derivadas saibam a quem o dado pertence.
- **Migration não-destrutiva**: `user_id` entrou com default transitório para o dono provisório, de modo que a aplicação continuou funcionando enquanto cada escrita passava a informar o dono. Uma revisão seguinte remove o default.
- **Sem tela de cadastro**: as contas nascem por linha de comando (`python -m notely.cli`), e `bootstrap` adota o dono provisório criado pela migration para que o acervo existente continue pertencendo à mesma pessoa.

## Consequências

- Nenhuma rota de domínio responde sem sessão ativa; `/health` permanece público. O gate está numa dependência única, então uma rota nova nasce protegida por padrão.
- Um recurso de outro usuário responde como inexistente, e não como proibido: o escopo do `uow` simplesmente não o encontra.
- O acervo anterior à entrega pertence ao dono provisório, que é adotado no primeiro `bootstrap`. O e-mail exibido muda, a identidade do dono não.
- Limitações conhecidas: o grafo no AGE ainda é único e identifica o dono por propriedade nos nós, não por isolamento de consulta — filtrar por dono nas leituras do grafo depende dos endpoints de leitura, que ainda não existem; o verificador de senha é único por conta, e trocar a senha não revoga sessões existentes; e o Reader ainda não tem telas de login e segundo fator.
- O bloqueio é por e-mail normalizado, então conhecer o endereço de alguém permite atrasar o login dessa pessoa por 15 minutos. É o custo aceito de limitar força bruta sem estado em memória.
