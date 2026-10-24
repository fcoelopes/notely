# ADR 0009 — Armazenamento S3 externo compartilhado

**Status:** Aceito
**Data:** 2026-10-24
**Issue:** [#26](https://github.com/fcoelopes/notely/issues/26)

## Contexto

O ADR 0001 colocou os PDFs em um MinIO iniciado pelo Compose do Notely. Outras
aplicações deste ambiente também precisam de armazenamento de objetos; manter um
contêiner por aplicação duplica operação e não ajuda a compartilhar a infraestrutura.
Antes da migração, o banco referenciava PDFs por URIs
`s3://notely-documents/...`; as anotações dependem desses arquivos para reabrir os
documentos.

## Decisão

O Notely passa a consumir um serviço S3 compatível externo, inicialmente um SeaweedFS
compartilhado. O Compose do Notely não inicia o serviço de objetos. Endpoint, bucket,
região e credenciais são definidos por `NOTELY_S3_*`, com identidade exclusiva do Notely
e permissões restritas ao bucket `notely`.

As chaves dos objetos permanecem iguais. O bucket compartilhado é `notely`, então os
URIs antigos `s3://notely-documents/...` passam para `s3://notely/...` somente após
verificação de todos os objetos no destino e em uma transação PostgreSQL. PostgreSQL
continua a ser a fonte de verdade dos metadados; o `storage_uri` não registra endpoint
ou credenciais.
A interface de armazenamento do core continua separando S3 do domínio. O scan ClamAV
fail-closed do ADR 0001 permanece obrigatório antes de gravar bytes.

HTTP é aceito apenas quando o endpoint está em loopback, como no contêiner compartilhado
desta máquina. Endpoints remotos exigem HTTPS. O MinIO antigo fica disponível somente
para migração e rollback e seu volume não é apagado automaticamente.

## Consequências

- A disponibilidade do Reader para PDFs já ingeridos depende do serviço compartilhado.
- O operador do serviço externo é responsável por provisionar bucket, identidade e backup.
- A migração copia e relê cada PDF, conferindo SHA-256 contra o registro PostgreSQL;
  a ativação troca somente os URIs em uma transação e pode ser repetida.
- Backup apenas do PostgreSQL não restaura os PDFs. O backup do bucket precisa fazer
  parte da operação do serviço compartilhado.
