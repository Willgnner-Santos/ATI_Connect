# Infraestrutura

A aplicação usa SAM, mas nem todos os recursos pertencem à mesma stack.
Este diretório guarda somente os templates auxiliares que ainda fazem parte
do fluxo documentado. Remover um arquivo antigo do repositório **não exclui**
uma stack já existente na AWS.

## Responsabilidades

| Arquivo | Responsabilidade |
| --- | --- |
| [../template.yaml](../template.yaml) | Lambdas, API Gateway, filas/DLQs e recursos/policies IoT/IAM da aplicação |
| [active/02-dynamodb-saas.yml](cloudformation/active/02-dynamodb-saas.yml) | 9 tabelas DynamoDB |
| [bootstrap/01-s3-deploy-bucket.yml](cloudformation/bootstrap/01-s3-deploy-bucket.yml) | Bucket usado por `scripts/update-lambdas.ps1` |
| [bootstrap/03-cognito.yml](cloudformation/bootstrap/03-cognito.yml) | Referência de User Pool/App Client; revisar antes de provisionar |

A layer é publicada separadamente e seu ARN versionado é passado ao SAM.
Os antigos templates separados de API/SQS/IoT e o deploy via Serverless Framework
foram retirados do fluxo. Não recrie stacks paralelas para recursos já geridos
pelo template SAM sem um plano explícito de migração.

## Tabelas

Os nomes seguem `${Project}-${Stage}-<sufixo>`, com `pk` e `sk` como chaves.
Consulte o template para os índices e configurações de TTL.

| Sufixo | Uso |
| --- | --- |
| `organizations` | Organizações e metadados |
| `tenants` | Tenants e configuração de autenticação |
| `tenant-users` | Usuários e vínculos |
| `tenant-equipment` | Equipamentos, comandos e estado |
| `tenant-equipment-telemetry` | Histórico de telemetria |
| `organization-roles` | Definições de roles/permissões |
| `audit-log` | Auditoria |
| `virtual-technician` | Automações e registros do Técnico Virtual |
| `push-subscriptions` | Inscrições de dispositivos para push |

## Antes de provisionar ou atualizar

Verifique conta, perfil, região e stage. Revise o changeset e evite executar
scripts de reset para corrigir problemas de deploy.

`deploy-full.ps1` cria a stack DynamoDB **somente se ela ainda não existe**;
não aplica alterações ao template de uma stack existente. Para essas mudanças,
use uma operação CloudFormation específica com revisão do changeset.
O script também não cria Cognito, buckets ou a layer.

O bucket `plataforma-ati-serverless-deploys-<regiao>` do bootstrap é usado
pelo atualizador rápido de Lambdas. Ele **não é** o bucket
`plataforma-ati-deploy-<stage>` configurado no `samconfig.toml`; provisionar um
não provisiona o outro.

O template Cognito contém pendências de schema e configuração que precisam
ser resolvidas antes de ser usado em uma conta nova. Consulte
[limitações conhecidas](../docs/ops/known-limitations.md).
As configurações de autenticação não foram alteradas como parte da limpeza.

As instruções operacionais e os riscos de cada script estão em
[docs/ops/scripts.md](../docs/ops/scripts.md).
