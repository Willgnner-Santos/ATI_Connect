# 🏢 Como Criar uma Nova Organização

Este documento explica como criar uma nova organização no sistema SaaS, incluindo a criação automática do admin.

---

## 📋 Endpoint

```
POST /saas/organization
```

**Autenticação:** Requer token de **superadmin**

---

## 📝 Payload

### Opção 1: Criar organização com admin (Recomendado)

```json
{
  "name": "Distribuidora Exemplo",
  "email": "contato@exemplo.com",
  "type": "distributor",
  "admin": {
    "name": "João Silva",
    "email": "joao.silva@exemplo.com",
    "password": "SenhaTemporaria123!"
  }
}
```

### Opção 2: Criar organização sem admin

```json
{
  "name": "Distribuidora Exemplo",
  "email": "contato@exemplo.com",
  "type": "distributor"
}
```

---

## 🔑 Campos

### Organização

| Campo | Tipo | Obrigatório | Descrição | Valores |
|-------|------|-------------|-----------|---------|
| `name` | string | ✅ Sim | Nome da organização | |
| `email` | string | ✅ Sim | Email de contato | Formato email válido |
| `type` | string | ❌ Não | Tipo da organização | `"ati"` ou `"distributor"` (padrão) |
| `admin` | object | ❌ Não | Dados do admin | Veja abaixo |

### Admin (campo `admin`)

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `name` | string | ✅ Sim* | Nome completo do admin |
| `email` | string | ✅ Sim* | Email do admin (usado para login) |
| `password` | string | ❌ Não | Senha inicial. Se omitido, Cognito envia email |

*Obrigatório se o campo `admin` for enviado

---

## ✨ O que Acontece Automaticamente

Quando você cria uma organização **com admin**, o sistema:

1. ✅ Cria a organização no DynamoDB
2. ✅ Cria 3 roles padrão para a organização:
   - **admin**: Acesso total à organização
   - **manager**: Gerenciamento de tenants e equipamentos
   - **viewer**: Apenas visualização
3. ✅ Cria o usuário admin no Cognito com:
   - `custom:organizationId` = ID da nova organização
   - `custom:tenantId` = "none" (não está em nenhum tenant ainda)
   - `custom:organizationRole` = "admin"
4. ✅ Cria o registro do usuário no DynamoDB
5. ✅ Define senha permanente (se `password` foi enviado)

---

## 📤 Response

### Sucesso (201 Created)

```json
{
  "statusCode": 201,
  "message": "Organização criada com sucesso (com admin)",
  "data": {
    "organizationId": "ab45e734-3de6-4ec7-bc46-8ad341e2d154",
    "adminUserId": "f36c7a9a-70b1-7032-34cf-3f76c353ee47"
  }
}
```

### Sem admin

```json
{
  "statusCode": 201,
  "message": "Organização criada com sucesso",
  "data": {
    "organizationId": "ab45e734-3de6-4ec7-bc46-8ad341e2d154",
    "adminUserId": null
  }
}
```

---

## 🔐 Roles Criadas Automaticamente

Cada nova organização recebe 3 roles padrão:

### 1. Admin
**Permissões:**
- `organization:read`, `organization:write`
- `tenant:*` (todas)
- `user:*` (todas)
- `equipment:*` (todas)
- `dashboard:read`
- `reports:*` (todas)

### 2. Manager
**Permissões:**
- `tenant:read`
- `user:read`, `user:write`
- `equipment:*` (todas)
- `dashboard:read`
- `reports:read`

### 3. Viewer
**Permissões:**
- `tenant:read`
- `equipment:read`
- `dashboard:read`

---

## 📋 Exemplo Completo (cURL)

### 1. Login como Superadmin

```bash
# Login
TOKEN=$(curl -s -X POST https://API_ENDPOINT/saas/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@plataforma-ati.com",
    "password": "<senha-configurada-no-ambiente>"
  }' | jq -r '.data.authorization.access_token')

echo "Token: $TOKEN"
```

### 2. Criar Organização com Admin

```bash
curl -X POST https://API_ENDPOINT/saas/organization \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Distribuidora Exemplo",
    "email": "contato@exemplo.com",
    "type": "distributor",
    "admin": {
      "name": "João Silva",
      "email": "joao.silva@exemplo.com",
      "password": "Exemplo123!"
    }
  }'
```

### 3. Testar Login do Admin da Nova Organização

```bash
curl -X POST https://API_ENDPOINT/saas/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "joao.silva@exemplo.com",
    "password": "Exemplo123!"
  }'
```

---

## 🧪 Testando no Postman

1. **Login como Superadmin**:
   - Request: `01. Authentication > Login`
   - Body: Credenciais do superadmin
   - Token salvo automaticamente

2. **Criar Organização**:
   - Request: `02. Organizations > Create Organization`
   - Body: JSON com dados da organização e admin
   - Copiar `organizationId` do response

3. **Login do Admin**:
   - Request: `01. Authentication > Login`
   - Body: Email e senha do admin criado
   - Verificar que `organizationId` no response é o correto

4. **Listar Tenants** (como admin):
   - Request: `03. Tenants > List Tenants`
   - Deve retornar lista vazia (organização nova)

---

## ⚠️ Observações Importantes

1. **Email Único**: O email do admin deve ser único no Cognito. Se já existir, a criação falhará.

2. **Senha**: 
   - Se você **não enviar** `password`, o Cognito envia um email para o usuário com senha temporária
   - Se você **enviar** `password`, a senha é definida como permanente imediatamente

3. **TenantId**: 
   - O admin é criado com `tenantId = "none"`
   - Para dar acesso a um tenant específico, você precisa atualizar o usuário depois

4. **Permissões**:
   - Apenas **superadmin** pode criar organizações
   - O **admin** da organização pode criar tenants e usuários

---

## 🐛 Troubleshooting

### Erro: "Acesso negado"
- Verifique se está usando token de **superadmin**
- Verifique se o token não expirou (validade: 1 hora)

### Erro: "UsernameExistsException"
- O email do admin já existe no Cognito
- Use outro email ou delete o usuário existente primeiro

### Erro: "Cognito config not found"
- A configuração do Cognito SaaS não está no DynamoDB
- O fluxo atual grava essa configuração durante `scripts/deploy-full.ps1`
- Antes de executar qualquer deploy ou seed, siga as validações e confirmações de [`docs/ops/scripts.md`](../ops/scripts.md)

### Admin criado mas não consegue fazer login
- Verifique se a senha atende aos requisitos do Cognito
- Verifique se o User Pool ID está correto no handler

---

## 📚 Arquivos Relacionados

- Handler: [`02_lambdas/saas/organization/new/handler.py`](../../02_lambdas/saas/organization/new/handler.py)
- Postman: [`plataforma-ati.postman_collection.json`](../plataforma-ati.postman_collection.json)
- Script de Deploy: [`scripts/deploy-full.ps1`](../../scripts/deploy-full.ps1)

---

**Última atualização**: 2026-05-20
