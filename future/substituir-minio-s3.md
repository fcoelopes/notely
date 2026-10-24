# Substituir MinIO por SeaweedFS externo via S3

**Status:** Concluído em 2026-10-24
**Issue:** [#26](https://github.com/fcoelopes/notely/issues/26)

## Objetivo

Remover o MinIO do caminho normal do Notely e conectar a aplicação a um contêiner
SeaweedFS compartilhado, administrado fora deste repositório. O cliente usa o protocolo
S3 e continua configurável por endpoint para permitir outro serviço no futuro.

## Decisões

- Não iniciar nem administrar SeaweedFS no Compose do Notely. O ambiente local sobe
  somente PostgreSQL e ClamAV; MinIO fica em perfil de migração/rollback.
- Migrar URIs `s3://notely-documents/<chave>` para `s3://notely/<chave>` após
  verificar todos os objetos no bucket compartilhado `notely`, mantendo as chaves
  `documents/<prefixo-sha>/<sha256>.pdf`. A troca de URIs é atômica no PostgreSQL.
- Usar boto3 com assinatura S3 v4. Endpoint, bucket, região e credenciais entram por
  `NOTELY_S3_*`; não compartilhar credenciais em arquivos versionados.
- Aceitar HTTP apenas para endpoint explicitamente configurado em loopback. Produção e
  endpoints remotos devem usar HTTPS.
- Manter o scan ClamAV antes do upload e extração do corpus via outbox.
- Migrar os PDFs referenciados pelo banco com leitura da origem MinIO, verificação
  SHA-256, cópia e leitura/verificação do destino. Não alterar o banco nem apagar origem.

## Ordem de implementação

1. Introduzir provider S3 e testes de upload, leitura e erro.
2. Conectar API e worker de corpus ao provider e retirar MinIO do startup.
3. Criar ferramenta idempotente de migração e documentação para o operador.
4. Configurar acesso ao contêiner compartilhado e bucket exclusivo do Notely.
5. Migrar, verificar os PDFs, ativar os novos URIs em transação, validar API e worker,
   parar MinIO sem remover seu volume.
6. Atualizar ADR, README e rollback; executar testes completos.

## Estado da execução

- Provider S3, API, worker, Compose e ferramenta de cópia preparados; o fluxo normal
  não inicia MinIO. O MinIO legado usa a porta 19001; SeaweedFS usa 9000.
- O bucket `notely` e uma credencial exclusiva estão disponíveis no serviço compartilhado.
  A configuração foi gravada no `.env` local ignorado pelo Git.
- Os oito PDFs (6.367.506 bytes) foram copiados e relidos no SeaweedFS com SHA-256
  igual ao PostgreSQL. Os oito URIs agora apontam para `s3://notely/...`.
- Os oito downloads pela API passaram, assim como leitura e extração pelo worker de
  corpus. O MinIO foi parado; seu volume permanece para rollback.
- A suíte completa do backend, os 104 testes do frontend e o build passaram. O
  `--to-legacy --dry-run` confirmou os oito objetos preservados na origem.

## Riscos e validação

- O contêiner compartilhado é dependência externa do Notely; sua disponibilidade e
  backups precisam ser geridos pelo serviço que o opera.
- Os URIs antigos e novos usam buckets distintos; a ferramenta valida cada referência
  e troca todos os URIs apenas após verificar os objetos do destino.
- A cópia não apaga a origem nem altera o banco; pode ser repetida. A ativação altera
  apenas `documents.storage_uri` em uma transação. O MinIO só deve ser parado após
  validação do destino.
- Rollback: copiar e verificar no sentido inverso (`--to-legacy`), ativar os URIs
  antigos e só então apontar a aplicação para o MinIO.
- Validar upload, download, corpus, erros do serviço e hashes do acervo migrado;
  executar a suíte completa do backend e o build do frontend.
