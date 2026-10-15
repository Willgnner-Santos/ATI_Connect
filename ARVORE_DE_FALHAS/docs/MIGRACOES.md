# Migrações de banco — Árvores de Falhas ATI

**Aplica-se apenas a bancos já existentes.** A criação de tabelas em uma instalação nova é feita em `src/storage/postgres_client.py`, mas o startup não substitui migrações de colunas, índices e constraints existentes.

Antes de aplicar: faça **backup testado**, confira a versão do PostgreSQL, verifique a estrutura atual (especialmente índices/constraints) e execute primeiro em uma cópia de staging. O script de `document_bindings` pode eliminar duplicatas depois de salvá-las; exige revisão especial dos dados.

## Ordem sugerida para instalações legadas

| Ordem | Script | Função | Atenção |
| --- | --- | --- | --- |
| 1 | `migrate_organization_scope.sql` | Normaliza escopo `PLATFORM` para `NULL` em documentos de modelo | Requer tabelas da ingestão previamente existentes |
| 2 | `migrate_fault_tree_versions.sql` | Cria tabelas/índices de versionamento | Inspecione tabelas já criadas pelo ORM |
| 3 | `migrate_optional_equipment_model.sql` | Torna `model_id` opcional e ajusta índices de unicidade | Pode detectar registros duplicados conflitantes |
| 4 | `migrate_fault_tree_version_supersedes.sql` | Acrescenta relacionamento CAS com versão anterior | A chave referenciada é `VARCHAR` (não UUID) |
| 5 | `migrate_fault_tree_version_progress.sql` | Acrescenta campos de progresso | Verificar se os campos já existem |
| 6 | `migrate_document_bindings_constraint.sql` | Corrige unicidade de vínculos por tenant | PostgreSQL **15+**, contém rotina de deduplicação; rode diagnóstico antes |

Os scripts individuais estão em `scripts/`. A ordem acima é um guia para schemas legados e **não é uma migração universal executável sem inspeção**. Alguns bancos podem já conter as alterações. Quando aplicável, rode cada script separadamente com a conexão de um usuário habilitado a alterar schemas, por exemplo:

```bash
psql "$POSTGRES_URI" -v ON_ERROR_STOP=1 -f scripts/migrate_fault_tree_versions.sql
```

**Não rode todos os SQL cegamente em produção.** Confira cada pré-condição e o efeito esperado, em particular `migrate_document_bindings_constraint.sql`, que possui procedimentos de diagnóstico e rollback documentados dentro do arquivo.

## Pós-aplicação

1. Revise índices e constraints criados; confira se `fault_tree_versions.supersedes_version_id` referencia `fault_tree_versions.id` com tipo compatível.
2. Verifique vínculos `document_bindings` por `org_id`, `tenant_id`, `target_type` e `target_id`.
3. Reinicie API/worker de modo controlado, acompanhe logs e rode os testes de integração em ambiente de teste.
4. Confirme o plano de restauração antes de alterar qualquer dado operacional.

Não foi executada migração contra banco real durante a limpeza deste repositório.
