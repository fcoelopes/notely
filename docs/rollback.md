# Rollback de migrations

Este procedimento cobre o PostgreSQL local iniciado pelo Docker Compose. O banco guarda dados autorais, anotações, sessões, outbox e progresso; TimescaleDB guarda a trilha temporal. Os PDFs permanecem no MinIO, fora do `pg_dump`. Uma recuperação completa de ambiente também precisa preservar esses objetos.

## Regra de decisão

Prefira corrigir a aplicação ou publicar uma migration de avanço quando houver dados nas estruturas que a revisão `down` apagaria. Reverter apenas o código da aplicação é possível quando a versão anterior aceita o esquema atual. Não apague anotações, progresso ou proveniência para fazer o esquema caber em uma versão anterior.

O rollback operacional reverte **uma revisão por vez**. As revisões `0001`, `0002`, `0003`, `0006` e `0007` bloqueiam o `down` se houver dados nas tabelas ou colunas removidas. As revisões `0004` e `0005` removem somente projeções de corpus reconstruíveis; após voltar a uma versão que as usa, execute o backfill. As verificações ocorrem dentro da transação Alembic, com bloqueio das tabelas, inclusive quando alguém chama `alembic downgrade` diretamente. Uma revisão nova fica bloqueada até ganhar uma política explícita em `infra/db/migrations/rollback_policy.py`.

## Reverter uma revisão

1. Pare API e workers. O comando recusa conexões de outros clientes no banco.
2. Veja a revisão atual e a prévia do passo anterior:

   ```bash
   backend/.venv/bin/alembic -c alembic.ini current
   backend/.venv/bin/python scripts/rollback.py --target 0006
   ```

3. Se a prévia não indicar dados protegidos, execute:

   ```bash
   backend/.venv/bin/python scripts/rollback.py --target 0006 --execute
   ```

O comando cria um `pg_dump` em `.local/backups/`, verifica seu conteúdo, restaura uma cópia inteira em banco temporário e só então faz o `down`. Se a restauração ou o downgrade falhar, a revisão não é tratada como concluída. O caminho do backup aparece no terminal. Use `--database NOME` para outro banco Docker local. O `--target` deve ser exatamente a revisão anterior, evitando saltos acidentais.

Depois do rollback, use código compatível com a revisão resultante. Executar `./scripts/dev.sh` com código que contém revisões mais novas aplicará `upgrade head` novamente.

## Recuperar o backup

Restaure sempre em **outro banco**, preservando o original para comparação:

```bash
backend/.venv/bin/python scripts/restore_backup.py .local/backups/ARQUIVO.dump --verify-only
backend/.venv/bin/python scripts/restore_backup.py .local/backups/ARQUIVO.dump --database notely_recovered
```

O script recusa um nome já existente, usa restauração transacional e executa as etapas de pré e pós restauração do TimescaleDB quando a extensão estiver no arquivo. Verifique a revisão, as anotações, sessões, progresso e acesso aos PDFs antes de apontar a aplicação para o banco recuperado. No ambiente local, com o código correspondente ao backup:

```bash
./scripts/dev.sh --database notely_recovered
```

O backup foi feito antes do `down`; recuperar essa cópia restaura o estado daquele momento. Escritas posteriores precisam ser reconciliadas separadamente. Para produção, use o mesmo princípio com backups externos, retenção e recuperação pontual testada; estes scripts se limitam ao PostgreSQL Docker local.

Referências: [Alembic](https://alembic.sqlalchemy.org/en/latest/api/commands.html), [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html), [restauração TimescaleDB](https://docs.tigerdata.com/api/latest/administration/).
