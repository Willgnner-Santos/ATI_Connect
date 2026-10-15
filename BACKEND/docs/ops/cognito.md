# Cognito: consultas de diagnóstico

Use um perfil autorizado e selecione explicitamente o pool. Não copie IDs de
outros ambientes nem registre senhas neste arquivo.

```powershell
$env:AWS_PROFILE = "<perfil-autorizado>"
$Region = "sa-east-1"
$UserPoolId = "<pool-do-ambiente>"
$Username = "<usuario-do-ambiente>"

aws sts get-caller-identity
aws cognito-idp list-user-pools --max-results 20 --region $Region
aws cognito-idp list-user-pool-clients --user-pool-id $UserPoolId --region $Region
aws cognito-idp admin-get-user --user-pool-id $UserPoolId --username $Username --region $Region
```

Esses comandos consultam identidades reais e podem retornar dados pessoais.
Não publique suas saídas sem revisão. Criação de usuários, confirmação
de cadastro e redefinição de senha são operações distintas e exigem
autorização explícita para o ambiente/usuário alvo.

O bootstrap e os limites dos scripts estão descritos em
[infraestrutura](../../infrastructure/README.md) e [operação](scripts.md).
