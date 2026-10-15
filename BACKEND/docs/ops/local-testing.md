# Testes e desenvolvimento local

Há dois níveis de teste. Os **unitários** não chamam a AWS; os de
**integração** executam handlers locais que acessam recursos reais do sandbox.

## Testes unitários

```powershell
python -m pip install -r requirements-test.txt
python -m pytest
# Equivalente explicito:
python -m pytest tests_unit -v
```

O `pytest.ini` seleciona apenas `tests_unit/` por padrão. A suíte inclui
regressões de autorização, validação de comandos e consistência do repositório.
Não requer credenciais, SAM ou Docker.

## SAM local

Use Python 3.14, Docker ativo e SAM CLI compatível com esse runtime.
No template de deploy, `Globals.Function.Layers` usa `!Ref LayerArn` para uma
layer publicada. O gerador cria uma layer local a partir do código próprio e
`requirements.txt`, com `BuildMethod: makefile`.

```powershell
python scripts/generate-local-template.py
sam build --template-file template.local.yaml --config-env sandbox --use-container
sam local start-api --template-file .aws-sam/build/template.yaml --config-env sandbox
```

Use `.aws-sam/build/template.yaml` nas invocações: apontar para o template-fonte
local ignora o resultado do build da layer. `template.local.yaml` e `.aws-sam/`
são gerados e não devem ser commitados. Regenere quando `template.yaml` mudar.
**Nunca publique o template local.**

A API manual escuta em `http://127.0.0.1:3000`. Para invocar uma função:

```powershell
sam local invoke AuthLoginFunction --event event.json --template-file .aws-sam/build/template.yaml --config-env sandbox
```

Prepare o evento conforme o contrato do handler. Handlers HTTP recebem um evento
API Gateway; os workers de Técnico Virtual recebem `Records` com `body` JSON:

```json
{
  "Records": [
    {
      "messageId": "teste-local",
      "body": "{\"tenantId\":\"<tenant-de-teste>\",\"actionId\":\"<acao-de-teste>\",\"equipmentId\":\"<equipamento-de-teste>\"}"
    }
  ]
}
```

Mesmo uma invocação manual pode escrever no DynamoDB, publicar no IoT ou enviar
notificações. Eventos de exemplo não criam recursos fictícios automaticamente.

## Integração com sandbox

A fixture em `tests/conftest.py` foi escrita para **Windows**, usando `cmd`,
`taskkill` e o caminho padrão do SAM CLI. Antes de executar, confira esse caminho,
a disponibilidade de Docker e o perfil AWS. A fixture fixa `STAGE=sandbox`,
a região `sa-east-1` e a porta local `3001`.

A senha do superadmin vem de `ATI_TEST_SUPERADMIN_PASSWORD`. Em PowerShell:

```powershell
$env:AWS_PROFILE = "<perfil-sandbox>"
aws sts get-caller-identity
$senha = Read-Host "Senha do superadmin de testes" -AsSecureString
$env:ATI_TEST_SUPERADMIN_PASSWORD = [System.Net.NetworkCredential]::new('', $senha).Password
try {
    python -m pytest tests/ -v
} finally {
    Remove-Item Env:ATI_TEST_SUPERADMIN_PASSWORD -ErrorAction SilentlyContinue
}
```

A fixture remove `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` e `AWS_SESSION_TOKEN`
do processo de teste; por isso, depende do perfil/configuração AWS e não deve
ser tratada como uma suíte genérica para qualquer provedor de credenciais.
O e-mail do superadmin também permanece configurado no código da fixture.

Ela gera o template, constrói a aplicação com Docker e inicia a API local.
Os testes criam organizações, tenants e usuários com prefixo de QA, além de
recursos IoT/Scheduler quando necessário. O teardown inclui exclusões reais via
API, DynamoDB e Cognito. Uma interrupção pode deixar resíduos: revise o ambiente.

No encerramento, a fixture remove **toda a pasta `.aws-sam/`** e o template local.
Não execute simultaneamente com outro build/deploy na mesma cópia do repositório.

## Limites da validação

SAM local não substitui testes com as roles IAM das Lambdas implantadas.
A suíte HTTP não cobre de ponta a ponta os workers SQS, entrega de notificações,
concorrência ou carga. Consulte também os
[roteiros manuais](../validation/validation.md) e as
[pendências operacionais](known-limitations.md).
