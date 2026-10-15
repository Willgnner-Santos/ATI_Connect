# Plataforma ATI · Backend SaaS

Backend serverless multi-tenant para gestão e monitoramento de equipamentos IoT.
Reúne autenticação, organizações, tenants, usuários, catálogos,
telemetria, relatórios e automações do Técnico Virtual.

**Python 3.14 · AWS SAM · Lambda · DynamoDB · Cognito · IoT Core**

## Comece por aqui

Para desenvolver, prepare o ambiente e execute os **testes unitários** abaixo.
Para operar uma instalação, consulte o [guia de operação](docs/ops/scripts.md)
e a [infraestrutura](infrastructure/README.md) antes de executar comandos AWS.

> A execução via SAM local **não isola os serviços AWS**. Os handlers e
> os testes de integração acessam recursos reais. Não use credenciais de
> produção para testes, seeds ou simuladores.

## O que existe hoje

O inventário abaixo descreve os arquivos deste repositório, não uma consulta
à conta AWS. A fonte de verdade para funções e rotas é o [template SAM](template.yaml).

| Componente | Implementação |
| --- | --- |
| Execução | 45 funções Lambda, Python 3.14, arquitetura x86_64 |
| API | 41 operações HTTP em API Gateway REST |
| Processamento sem rota HTTP | Roteador de telemetria + validator, executor e notifier do Técnico Virtual |
| Persistência | 9 tabelas DynamoDB no template de infraestrutura ativo |
| Mensageria | 4 filas SQS de trabalho + 4 dead-letter queues |
| Código compartilhado | Layer com `plataforma_ati.py` e o pacote `notifications/` |

A API cobre autenticação, organizações, tenants, usuários, equipamentos,
comandos, catálogo de sensores/modelos, dashboard, relatórios, Técnico Virtual
e inscrições push. Os contratos ficam no [OpenAPI](docs/openapi.yaml)
e na [collection Postman](docs/plataforma-ati.postman_collection.json).

## Arquitetura

```text
Frontend / clientes HTTP
          |
      API Gateway ----> Lambdas SaaS ----> Cognito / DynamoDB
                              |
                       Comandos / automacoes
                              |
Dispositivos <-----------> AWS IoT Core
                              |
                     Roteador de telemetria ---> DynamoDB
                              |
                 Regras IoT / EventBridge Scheduler
                              |
                  SQS ---> Tecnico Virtual
                         validator / executor / notifier
                              |
                    IoT / e-mail / SMS / Telegram / push
```

A hierarquia de acesso é `PLATFORM > Organization > Tenant > Users/Equipment`.
A layer centraliza validação, autorização e helpers DynamoDB. Regras de escopo,
roles e chaves de dados devem ser preservadas em qualquer alteração de handler.

O deploy não é uma stack única para tudo: o SAM cuida da aplicação;
DynamoDB possui um template separado; Cognito e buckets exigem provisionamento
prévio. A layer é publicada separadamente e informada por `LayerArn`.

## Estrutura

```text
01_layers/plataforma_ati/
    python/plataforma_ati.py       # Validacao, autorizacao e acesso a dados
    python/notifications/         # Provedores de notificacao
    requirements.txt              # Dependencias da layer
    Makefile                      # Build Linux via SAM + Docker
02_lambdas/saas/                   # Handlers por dominio e operacao
infrastructure/cloudformation/
    active/                       # Tabelas DynamoDB
    bootstrap/                    # Templates de apoio para S3 e Cognito
scripts/                          # Deploy, atualizacao, migracoes, seeds e simuladores
    iot_device_simulator.py      # Simulador de publicacao IoT
    telemetry_simulator.py       # Interface Streamlit para simulacao
tests_unit/                      # Testes sem chamadas AWS
tests/                           # Integracao com recursos reais do sandbox
docs/                             # API, guias, operacao e historico
template.yaml                    # Aplicacao SAM
samconfig.toml                   # Configuracao dos ambientes
```

As dependências instaladas e os binários da layer **não são versionados**.
Os artefatos de build ficam em `.aws-sam/`. Não instale pacotes com `pip -t`
dentro de `01_layers/plataforma_ati/python/`.

## Desenvolvimento

### Pré-requisitos

Use Python **3.14** para alinhar o ambiente ao runtime declarado. AWS CLI v2,
SAM CLI com suporte a esse runtime e Docker são necessários para builds locais
da layer e integração. Os scripts `.ps1` e a fixture de integração mantêm
premissas de Windows; os testes unitários não dependem dessas ferramentas.

### Ambiente e testes unitários

Na raiz do repositório, em PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
python -m pytest
```

Em Linux/macOS, a ativação é `source .venv/bin/activate`.
`pytest` sem argumentos seleciona apenas `tests_unit/`. A suíte de integração
é opt-in: `python -m pytest tests/ -v` e possui requisitos adicionais descritos
no [guia de testes](docs/ops/local-testing.md).

### Build e execução local

Com Docker ativo e um perfil AWS de sandbox configurado:

```powershell
python scripts/generate-local-template.py
sam build --template-file template.local.yaml --config-env sandbox --use-container
sam local start-api --template-file .aws-sam/build/template.yaml --config-env sandbox
```

A API local fica em `http://127.0.0.1:3000`, sem o prefixo de stage.
O gerador substitui `!Ref LayerArn` pela layer local e resolve o nome da policy IoT.
Use o **template construído**, em `.aws-sam/build/`, para carregar as dependências.
Nunca faça deploy de `template.local.yaml` nem de seu resultado de build.

Para construir somente a layer, veja o [guia da layer](01_layers/plataforma_ati/README.md).
O `Makefile` da raiz oferece atalhos locais: `make help`, `make test`,
`make build-local` e `make layer`. Ele não executa deploys implicitamente.

## Configuração e deploy

| Ambiente | Stack SAM | Região configurada |
| --- | --- | --- |
| `sandbox` | `plataforma-ati-saas-sandbox` | `sa-east-1` |
| `staging` | `plataforma-ati-saas-staging` | `sa-east-1` |
| `production` | `plataforma-ati-saas-production` | `sa-east-1` |

Revise `samconfig.toml` antes de operar: o sandbox contém um ARN de layer
específico do ambiente original. Staging e production precisam de `LayerArn`
explícito. A configuração no arquivo não comprova que o recurso existe.

Os parâmetros principais são `Stage`, `Project`, `LayerArn`, `CorsAllowOrigin`,
`TelegramBotToken` e `FirebaseServiceAccountSsmParam`. Configure segredos por
mecanismos seguros; não os grave em exemplos, commits ou arquivos de teste.
O parâmetro Firebase aponta para um JSON de service account no SSM SecureString.

Para uma stack já provisionada, depois de revisar identidade AWS e parâmetros:

```powershell
# Rebuild com o template de DEPLOY, nao com o template local.
sam build --template-file template.yaml --config-env sandbox
sam deploy --config-env sandbox --confirm-changeset
```

Esse comando altera recursos reais e reutiliza os parâmetros do `samconfig.toml`.
A primeira instalação, publicação de layer e os scripts de atualização estão
no [guia de operação](docs/ops/scripts.md). Os templates de bootstrap têm
[pendências conhecidas](docs/ops/known-limitations.md); não trate `deploy-full.ps1`
como provisionamento completo e validado de uma conta vazia.

## Documentação

| Assunto | Referência |
| --- | --- |
| Índice e contratos da API | [docs/README.md](docs/README.md) |
| Testes unitários, SAM local e integração | [local-testing.md](docs/ops/local-testing.md) |
| Deploy, atualização, seeds e simuladores | [scripts.md](docs/ops/scripts.md) |
| Infraestrutura e tabelas | [infrastructure/README.md](infrastructure/README.md) |
| Criação de organização e roles | [criar-organizacao.md](docs/guides/criar-organizacao.md) |
| Papel collaborator | [collaborator-role.md](docs/guides/collaborator-role.md) |
| Automações do Técnico Virtual | [virtual-technician.md](docs/guides/virtual-technician.md) |
| Limitações e itens a revisar | [known-limitations.md](docs/ops/known-limitations.md) |

## Cuidados ao contribuir

Preserve o isolamento entre organizações/tenants e o contrato dos handlers.
Atualize OpenAPI e Postman quando mudar uma rota. Rode os testes unitários;
execute integração apenas com ambiente e credenciais autorizados.
Consulte [AGENTS.md](AGENTS.md) para convenções de código e limites operacionais.
