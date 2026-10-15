# Limitações e pendências conhecidas

Este registro separa a limpeza do repositório de mudanças que exigem
validação de infraestrutura, credenciais ou regras de negócio.

## Provisionamento de uma conta nova

`infrastructure/cloudformation/bootstrap/03-cognito.yml` preserva a configuração
original. No recurso `AWS::Cognito::UserPool`, declara `PasswordPolicy` e `Tags`
diretamente em `Properties`; o schema CloudFormation documenta
`Policies.PasswordPolicy` e `UserPoolTags`. Essa pendência deve ser corrigida e
validada antes de usar o bootstrap. Referências oficiais:
[UserPool](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-cognito-userpool.html)
e [Policies](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-cognito-userpool-policies.html).

O App Client do template usa `GenerateSecret: false`, enquanto scripts de seed
consultam e persistem `ClientSecret`. Revise a compatibilidade do conjunto com o
fluxo de autenticação desejado; isso não foi alterado na limpeza.
O bootstrap ainda aceita o stage adicional `prod`, embora a aplicação SAM
padronize `sandbox`, `staging` e `production`.

`deploy-full.ps1` mantém um caminho Windows fixo para SAM, identifica um pool
padrão no sandbox e pula atualizações de uma stack DynamoDB existente.
Nem todas as chamadas nativas têm checagem uniforme de exit code.
Esses comportamentos precisam de uma revisão operacional separada.

## Configurações locais e migrações

O ARN de layer no sandbox, os endpoints OpenAPI/Postman e os IDs de seeds,
simuladores e ferramenta push são específicos do ambiente original.
Não houve consulta à AWS para confirmar se ainda existem.

Os scripts `seed-users-sandbox.py`, `seed-tenant-roles-sandbox.py` e
`migrate-tenant-role-seeds.py` foram preservados porque o ZIP não permite
concluir que suas migrações já foram aplicadas. Há IDs fixos, senha fixa de
seed e papéis legados nesses arquivos. **Revise-os antes de qualquer execução**;
a presença no repositório não os torna o procedimento recomendado de criação.
Se senhas de exemplo já foram usadas em contas reais, alterá-las e revisar o
histórico exige uma ação separada; limpar documentação não revoga credenciais.

## Build e validação

A layer agora é construída em vez de carregar dependências instaladas no Git.
As transitivas ainda não possuem lockfile e o boto3 é declarado por faixa.
A compatibilidade completa deve ser testada em Docker/Linux/Python 3.14 antes
de publicar a nova layer, incluindo importação do Firebase Admin.

Os testes unitários verificam código e consistência estrutural. Não comprovam
deploy, permissões IAM, entrega de notificações nem sucesso da integração AWS.
