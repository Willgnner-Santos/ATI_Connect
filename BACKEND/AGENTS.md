# AGENTS.md — Plataforma ATI Backend

Diretrizes para qualquer agente de IA (Claude Code, Gemini, Copilot, etc.) trabalhando neste repositório.

---

## 0. Regra Máxima: Não Assumir. Perguntar.

Esta é a regra mais importante deste documento e prevalece sobre qualquer outra conveniência de produtividade.

- **Nunca preencha lacunas de informação com suposições.** Se uma instrução do usuário for ambígua, incompleta, ou permitir mais de uma interpretação razoável, **pare e pergunte** antes de escrever código, editar arquivos ou executar comandos.
- **Não infira requisitos de negócio.** Este é um sistema multi-tenant (PLATFORM > Organization > Tenant > Users/Equipment) com regras de escopo e permissão sensíveis (veja seção 4). Se não estiver 100% claro qual escopo, role, tabela ou tenant está envolvido, pergunte — um erro aqui é uma falha de segurança/isolamento de dados entre clientes.
- **Não escolha "a opção mais provável" silenciosamente.** Se existem 2+ formas plausíveis de implementar algo (ex: qual tabela DynamoDB usar, se um campo é obrigatório, se uma mudança afeta templates de bootstrap também, qual ambiente/stage é o alvo), apresente as opções e pergunte — não decida por conta própria.
- **Peça a informação faltante em vez de contornar.** Se falta um dado necessário para agir com segurança (ex: stage de destino, se a organização é PLATFORM ou de negócio, se a rota precisa de autorização), pergunte diretamente pela informação. Não tente "adivinhar de forma inteligente" nem prosseguir com um placeholder.
- **Ambiguidade textual do usuário (PT-BR) também conta.** Se o pedido usa termos vagos ("ajusta isso aí", "corrige o bug de login", "atualiza os lambdas"), peça para especificar qual arquivo/handler/rota/ambiente antes de agir.
- **Confirme decisões arquiteturais e de segurança antes de implementar.** Mudanças em autenticação, autorização (`authorizeSaaS`, `validateSaaSRoleAssignment`, `isGlobalOrganizationRole`), estrutura de chaves do DynamoDB, ou papéis (roles) padrão exigem confirmação explícita do usuário do que é esperado, mesmo que o código pareça sugerir um caminho óbvio.
- É preferível fazer 3 perguntas e implementar certo da primeira vez do que implementar rápido e errado.

**Não se aplica a**: leituras exploratórias (grep, ler arquivos, `sam validate`, `git status`) — isso é investigação, não decisão, e deve ser feito livremente para reduzir ambiguidade antes de perguntar.

**Exemplo do que NÃO é ambíguo** (não precisa perguntar): o usuário pede para corrigir um `except` que não desmonta corretamente a string `"CODE::mensagem"` em um handler específico já indicado. O padrão de erro já está definido e documentado (seção 4, item 6), o arquivo é conhecido, e a correção segue uma convenção existente sem decisão de negócio envolvida — nesse caso, implemente diretamente.

---

## 1. Visão Geral do Projeto

Backend serverless multi-tenant (SaaS) para monitoramento de equipamentos IoT, rodando em AWS via **SAM**.

- **Runtime**: Python 3.14, arquitetura x86_64 (ver `template.yaml`)
- **IaC**: AWS SAM (`template.yaml`, `samconfig.toml`) + CloudFormation cru em `infrastructure/cloudformation/` (fora do SAM, deploy manual via CloudFormation; `make` oferece apenas atalhos locais)
- **Serviços**: Lambda, API Gateway, DynamoDB, Cognito, CloudWatch
- **Hierarquia de dados**: `PLATFORM (Organization raiz) > Organization > Tenant > Users/Equipment`

### Estrutura relevante

```
01_layers/plataforma_ati/python/plataforma_ati.py   # Layer compartilhada: validação, auth, DynamoDB helpers
02_lambdas/saas/<dominio>/<acao>/handler.py           # Handlers Lambda (auth, organization, tenant, user, equipment, catalog, dashboard, report, virtual-technician)
infrastructure/cloudformation/active/                 # DynamoDB; deploy-full cria a stack apenas se ausente
infrastructure/cloudformation/bootstrap/               # Setup manual único por ambiente (S3 deploy bucket, Cognito User Pool)
docs/                                                 # Documentação funcional (roles, validações, etc.)
scripts/*.ps1                                         # Scripts PowerShell de deploy/reset — ver seção 3
```

---

## 2. Comandos e Fluxo de Desenvolvimento

```powershell
# Unitarios: nao chamam a AWS
python -m pip install -r requirements-test.txt
python -m pytest

# Template de deploy
sam build --template-file template.yaml --config-env sandbox
sam validate --template-file template.yaml --region sa-east-1

# Local: requer Docker; handlers acessam recursos AWS reais
python scripts/generate-local-template.py
sam build --template-file template.local.yaml --config-env sandbox --use-container
sam local start-api --template-file .aws-sam/build/template.yaml --config-env sandbox

# Deploy: somente com autorizacao explicita e parametros revisados
# Reconstruir antes com template.yaml; nunca publicar o template local.
sam build --template-file template.yaml --config-env sandbox
sam deploy --config-env sandbox --confirm-changeset
```

- `pytest` sem argumentos executa somente `tests_unit/`. A integração em
  `tests/` é opt-in, usa recursos reais e exige autorização para o sandbox.
- Dependências da layer são instaladas pelo build em `.aws-sam/`, nunca na
  pasta de fontes. Consulte `01_layers/plataforma_ati/README.md`.

Ver `docs/ops/local-testing.md` para detalhes de por que o template local é necessário e como testar handlers HTTP vs. workers SQS.

- Ambientes válidos: `sandbox`, `staging`, `production` (definidos em `samconfig.toml`). **Nunca assuma qual ambiente é o alvo** de um comando — pergunte se não foi dito explicitamente. `sandbox` não pede confirmação de changeset; `staging`/`production` pedem.
- Há uma suíte de testes de integração (`pytest tests/`, ver `docs/ops/local-testing.md`) que cobre os endpoints HTTP dos handlers SaaS contra `sam local start-api` e recursos reais do stage `sandbox`. Ao alterar `plataforma_ati.py` ou qualquer handler, rode os unitários e, somente com autorização para recursos reais, a integração do domínio afetado; relate o que não foi validado, e atualize/adicione testes se o contrato do endpoint mudou.

---

## 3. Zona de Risco: Scripts e Operações Destrutivas

Vários scripts em `scripts/` operam diretamente sobre infraestrutura AWS e **podem ser irreversíveis**:

| Script | Risco |
|---|---|
| `scripts/delete-tables.ps1` | Deleta tabelas DynamoDB — irreversível, perda de dados |
| `scripts/reset-database.ps1` | Limpa DynamoDB + Cognito e recria estrutura base |
| `scripts/deploy-full.ps1` | Deploy completo de infra + lambdas + seed |
| `scripts/update-lambdas.ps1` | Atualiza Lambdas (todas ou uma via `-Lambda`) em produção/staging via S3 |
| `scripts/update-layer.ps1` | Publica nova versão da Lambda Layer e atualiza todas as funções |

Regras:
- **Nunca execute esses scripts (ou `sam deploy`, `sam delete`, `aws cloudformation ...`, `aws dynamodb ...` destrutivo) sem confirmação explícita do usuário**, incluindo qual `-Stage` é o alvo. Confirmar uma vez para `sandbox` não autoriza rodar em `staging`/`production`.
- **Nunca infira o stage por padrão.** Se o usuário pedir "atualiza o lambda" sem dizer o ambiente, pergunte qual stage.
- Antes de rodar qualquer comando que apague, sobrescreva ou reseje algo (tabelas, stacks, usuários Cognito), explique o que o comando vai fazer e espere confirmação — mesmo que o próprio script já tenha flags de segurança (`-Force`, etc.).
- Credenciais AWS: o projeto usa o perfil `ati-sandbox-user`. Se variáveis de ambiente de outro perfil (ex: `niaa-bedrock`) estiverem ativas, isso é uma causa conhecida de `AccessDeniedException` — mas não assuma qual credencial está ativa, verifique com `aws sts get-caller-identity` antes de agir.

---

## 4. Convenções de Código dos Handlers

Todo handler Lambda em `02_lambdas/saas/**/handler.py` segue o mesmo esqueleto. Ao criar ou editar handlers, siga o padrão existente — não introduza uma abordagem alternativa sem alinhar antes:

1. **Import da layer compartilhada** (`from plataforma_ati import ...`) — nunca duplique lógica de validação/auth/DynamoDB que já existe na layer (`01_layers/plataforma_ati/python/plataforma_ati.py`). Se precisar de uma função nova de propósito genérico, pergunte se ela deve ir na layer ou ficar local ao handler.
2. **Validação de token**: `validateTokenSaaS(auth_header, region)`.
3. **Autorização**: `authorizeSaaS(token_data, roles_table, <module>, <action>, region)` — módulo/ação seguem o formato `recurso:acao` (ex.: `user:write`). Roles globais (`superadmin`, `collaborator`) são verificadas via `isGlobalOrganizationRole`. Ao atribuir/validar um role de destino, use `validateSaaSTargetRoleExists(roles_table, organization_id, role, region)` para confirmar que o role existe na organização antes de seguir.
4. **Parse do body**: trata `isBase64Encoded`, faz `json.loads`.
5. **Validação de input**: `validateInput(fields_spec, body)`, com spec declarativa (`field`, `type`, `mandatory`, `pattern`).
6. **Erros como `"CODE::mensagem"`**: exceptions são levantadas como string `"<http_status>::<mensagem>"` e desmontadas no `except` do handler. Mantenha esse padrão — não troque por outro esquema de erro sem confirmar.
7. **Escopo multi-tenant**: toda operação que cruza organização/tenant deve validar escopo (`validateSaaSRoleAssignment`, `resolveSaaSTargetScope`, comparação com `auth['organizationId']`/`auth['tenantId']`). **Isso é a parte mais sensível do sistema** — qualquer mudança aqui exige confirmação explícita do usuário sobre a regra de negócio esperada, porque um erro permite acesso cross-tenant.
8. **Padrões de chave DynamoDB**: `PK: ORG#{organizationId}#USER` / `SK: USER#{userId}`, `PK: ORG#{organizationId}#ROLES` / `SK: ROLE#{roleId}`, `PK: TENANT#{tenantId}#EQUIPMENT` / `SK: EQUIPMENT#{equipmentId}`, `PK: ORG#{organizationId}#TENANTS` / `SK: TENANT#{tenantId}`. Não invente um novo padrão de chave sem confirmar — isso afeta queries existentes.
9. **Resposta**: sempre via `lambdaReturn({...})`.
10. Tipos `Decimal` do DynamoDB são serializados via `DecimalEncoder` — use-o ao invés de conversões manuais ad-hoc.

---

## 5. Segurança e Dados Sensíveis

- Nunca commite credenciais, tokens, ou senhas reais (inclusive credenciais de sandbox; não reutilize senhas fixas presentes em scripts de seed).
- Ao lidar com Cognito (`custom:organizationId`, `custom:tenantId`, `custom:organizationRole`), qualquer mudança nos atributos customizados ou no fluxo de criação/senha deve ser confirmada — afeta login de usuários existentes.
- `bleach.clean` / `sanitizeInput` são usados para sanitização de input — não remova sanitização existente para "simplificar" código.

---

## 6. Estilo Geral

- Siga o restante das diretrizes gerais de engenharia (sem comentários óbvios, sem abstrações prematuras, sem mudanças fora do escopo pedido).
- PT-BR é a língua predominante em mensagens de erro, docs e nomes de variáveis de negócio (`organizationId`, `tenantId` em inglês, mas mensagens e docs em PT-BR) — mantenha consistência com o que já existe no arquivo que está sendo editado.
- Ao terminar uma tarefa, se algo ficou ambíguo durante a execução e você teve que tomar uma decisão pequena (não bloqueante) para continuar, **relate essa decisão explicitamente ao usuário** ao final, em vez de deixá-la silenciosa.
