# Operação e scripts

Execute comandos a partir da raiz do repositório. Escolha explicitamente
stage, perfil e região; confirme a identidade com `aws sts get-caller-identity`.
Nenhum comando de deploy, seed ou reset deve ser confundido com um teste local.

## Mapa de operações

| Script | Efeito e cuidados |
| --- | --- |
| `generate-local-template.py` | Gera arquivo local; não chama a AWS |
| `update-lambdas.ps1` | Atualiza código de uma ou todas as Lambdas via S3; não atualiza IaC/configuração |
| `update-layer.ps1` | Constrói com Docker, publica uma layer e substitui a lista de layers nas funções |
| `deploy-full.ps1` | Cria DynamoDB se ausente, faz SAM deploy e semeia PLATFORM/roles/superadmin |
| `reset-database.ps1` | Apaga dados DynamoDB/Cognito e recria estrutura; operação destrutiva |
| `delete-tables.ps1` | Exclui tabelas DynamoDB; operação destrutiva |
| `migrate-tenant-roles.ps1` | Migra papéis/dados de tenants existentes; revisar antes de executar |
| Scripts Python de seed/migração | Usam configurações específicas do sandbox; não executar indiscriminadamente |

Os scripts PowerShell foram mantidos com suas premissas de Windows. Em especial,
`deploy-full.ps1` usa um caminho fixo para `sam.cmd`. Não há validação de que
executar esses arquivos via `pwsh` em outro sistema seja suficiente.

## Stack já provisionada

Confira `LayerArn` e os demais parâmetros do ambiente em `samconfig.toml`.
Rebuild explicitamente com o template de deploy, para não publicar um build local:

```powershell
$env:AWS_PROFILE = "<perfil-sandbox>"
aws sts get-caller-identity
sam build --template-file template.yaml --config-env sandbox
sam deploy --config-env sandbox --confirm-changeset
```

Sandbox originalmente não exige confirmação no arquivo de configuração;
`--confirm-changeset` a solicita nesta execução. Em staging/production ela já
está configurada. O comando pode alterar recursos reais.

Os buckets de deploy precisam existir. `s3_bucket` é a opção adotada no
repositório; não a combine com `resolve_s3 = true`, pois o SAM rejeita essa
combinação conforme a [referência de sam deploy](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-cli-command-reference-sam-deploy.html).

## Atualizar somente código

```powershell
.\scripts\update-lambdas.ps1 -Stage sandbox -Lambda auth/login
# Ou todas as funcoes declaradas no script:
.\scripts\update-lambdas.ps1 -Stage sandbox
```

O script usa o bucket `plataforma-ati-serverless-deploys-sa-east-1`, diferente
dos buckets SAM por stage. A lista de funções é comparada ao template pelos
testes unitários. Atualizar código desta forma não aplica alterações de
permissões, rotas, filas ou variáveis: para isso, use o fluxo SAM.

## Atualizar a layer

Leia o [guia da layer](../../01_layers/plataforma_ati/README.md) e valide o build.
Para funções já existentes e após autorização para o ambiente:

```powershell
.\scripts\update-layer.ps1 -Stage sandbox
```

O script constrói para Linux/Python 3.14, publica o ZIP e usa o ARN da versão
que acabou de publicar. Ao final, copie o ARN exibido para `LayerArn` na
configuração SAM do ambiente. Ele não atualiza `samconfig.toml` automaticamente.
A substituição da lista de layers não preserva layers adicionais.

## Primeira instalação

Não há um bootstrap de conta vazia validado por este repositório.
Antes de usar `deploy-full.ps1`, resolva as
[pendências de bootstrap](known-limitations.md) e provisione Cognito, os buckets
e uma versão de layer. Preencha os parâmetros do SAM, incluindo `LayerArn`.

Com os pré-requisitos atendidos, o script exige uma senha segura e aceita o pool
explícito (obrigatório fora do sandbox):

```powershell
$senha = Read-Host "Senha inicial do superadmin" -AsSecureString
.\scripts\deploy-full.ps1 -Stage sandbox -UserPoolId "<pool-sandbox>" -SuperadminEmail "<email-admin>" -SuperadminPassword $senha
```

Ele cria PLATFORM e os papéis `superadmin`, `collaborator`, `admin`, `manager` e
`viewer`, além do usuário inicial. Não atualiza uma stack DynamoDB já existente.
Revise os efeitos de reexecutar seeds sobre um ambiente que já possui dados.

## Seeds, migrações e simuladores

`migrate-tenant-role-seeds.py`, `seed-tenant-roles-sandbox.py` e
`seed-users-sandbox.py` foram preservados como ferramentas específicas de
migração/sandbox. Contêm premissas de dados, IDs e papéis que precisam de
revisão. Não fazem parte do quick start.

`scripts/iot_device_simulator.py` publica telemetria em recursos reais de IoT.
`scripts/telemetry_simulator.py` oferece uma interface Streamlit e importa esse módulo;
os dois arquivos devem ser mantidos juntos. Para usar a interface, instale
Streamlit no ambiente local e execute
`streamlit run scripts/telemetry_simulator.py`.

Os artefatos manuais de `test-push/` foram removidos da árvore principal.
Valide notificações por testes controlados, sem manter tokens, IDs de usuário
ou configuração Firebase fixa no repositório.
