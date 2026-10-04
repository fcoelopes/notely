# Plano — rollback seguro de migrations Alembic

**Estado:** implementação concluída na branch `feat/reading-progress`; aguardando revisão.

**Issue:** [#5 — Rollback seguro de migrations Alembic](https://github.com/fcoelopes/notely/issues/5).

## Intenção

Permitir uma reversão operacional rápida sem apagar anotações, sessões, trilha temporal ou progresso de leitura. O banco PostgreSQL permanece a fonte de verdade; a recuperação não transforma projeções reconstruíveis em dados autorais.

## Decisões

1. Reverter uma revisão por vez, com prévia e alvo explícito. Não automatizar saltos longos entre versões.
2. Antes do `down`, verificar conexões concorrentes, criar `pg_dump` local e fazer uma restauração completa de ensaio em banco temporário.
3. Dentro da mesma transação do `down`, bloquear as tabelas afetadas e recusar revisões que descartariam dados protegidos. Revisões novas ficam bloqueadas até declarar sua política.
4. Permitir `down` das revisões `0004` e `0005` porque removem projeções textuais reconstruíveis; exigir backfill quando voltarem a ser usadas.
5. Restaurar backups somente em banco novo. O operador compara o estado recuperado e aponta a aplicação para ele. O script local aceita `--database NOME`.
6. Preferir correção ou migration de avanço quando o esquema antigo não comporta dados existentes. Evitar `down` destrutivo com autorização genérica.

## Implementação

- `infra/db/migrations/rollback_policy.py` define os dados protegidos nas revisões históricas e a proteção usada pelo Alembic.
- `scripts/rollback.py` mostra prévia e executa o passo único com backup e ensaio de restauração.
- `scripts/restore_backup.py` restaura um arquivo customizado em banco novo, com suporte às etapas de restauração TimescaleDB.
- `scripts/dev.sh` permite selecionar um banco local recuperado.
- `docs/rollback.md` contém o procedimento operacional e o limite do backup PostgreSQL em relação aos PDFs no MinIO.

## Validação

- Banco descartável vazio: prévia, backup restaurável, `0007 -> 0006 -> 0007`.
- Banco com progresso: comando operacional e Alembic direto recusam o `down`, mantendo revisão e linhas.
- Backup com TimescaleDB: restauração em banco novo; segunda tentativa ao mesmo nome recusada; aplicação inicia com o banco recuperado.
- Suíte completa do backend e verificação de diff.

## Riscos e recuperação

O `pg_dump` cobre o banco, não os arquivos MinIO. Uma restauração volta ao instante do backup e não incorpora escritas posteriores. Em produção, usar backups externos, retenção e recuperação pontual testada. O comando implementado se limita ao PostgreSQL Docker local e não substitui esse plano de operação.
