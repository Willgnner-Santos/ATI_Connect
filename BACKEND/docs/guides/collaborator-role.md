# Role global `collaborator`

## Objetivo

`collaborator` é uma role da organização técnica `PLATFORM`, com `tenantId=none`.
Ela fica imediatamente abaixo de `superadmin` e administra todas as organizações
de negócio.

## Matriz de acesso

| Recurso | collaborator | superadmin |
|---|---:|---:|
| Listar, criar, excluir e reativar organizações de negócio | Sim | Sim |
| Administrar tenants de qualquer organização | Sim | Sim |
| Administrar usuários de qualquer organização de negócio | Sim | Sim |
| Administrar equipamentos e modelos por organização | Sim | Sim |
| Dashboard, relatórios e técnico virtual de qualquer organização | Sim | Sim |
| Administrar a organização `PLATFORM` | Não | Sim |
| Criar, promover, editar, excluir ou reativar `superadmin`/`collaborator` | Não | Sim |
| Criar ou excluir tipos de sensor do catálogo global | Sim | Sim |

As permissões armazenadas no DynamoDB continuam definindo **o que** a role
pode executar. O backend também reconhece `collaborator` como role de escopo
global para definir **onde** ela pode operar.

## Registro da role

Novos ambientes recebem a role pelos scripts `scripts/deploy-full.ps1` e
`scripts/reset-database.ps1`, que já gravam o item abaixo na tabela
`plataforma-ati-{stage}-organization-roles` antes da criação do usuário:

```json
{
  "pk": "ORG#PLATFORM#ROLES",
  "sk": "ROLE#collaborator",
  "organizationId": "PLATFORM",
  "roleId": "collaborator",
  "roleName": "Colaborador",
  "permissions": [
    "organization:*",
    "tenant:*",
    "user:*",
    "equipment:*",
    "action:*",
    "dashboard:read",
    "report:read",
    "catalog:*",
    "virtual-technician:*"
  ],
  "description": "Colaborador da plataforma com administração sobre todas as organizações",
  "isSystemRole": true
}
```

## Criação do usuário

Um `superadmin` cria a conta pelo endpoint existente:

```http
POST /saas/user
Authorization: Bearer {token-superadmin}
Content-Type: application/json

{
  "name": "Nome do colaborador",
  "email": "colaborador@example.com",
  "organizationId": "PLATFORM",
  "tenantId": "none",
  "role": "collaborator",
  "password": "uma-senha-forte"
}
```

O Cognito deve ficar com:

```text
custom:organizationId = PLATFORM
custom:tenantId = none
custom:organizationRole = collaborator
```

## Uso de endpoints tenant-scoped

Quando um endpoint opera em um tenant específico, a conta global informa o
alvo explicitamente:

- relatórios: `organizationId` e `tenantId` na query string;
- visualizar/excluir técnico virtual: `organizationId` e `tenantId` na query;
- criar/atualizar técnico virtual: `organizationId` e `tenantId` no body.

Isso evita que uma conta global opere acidentalmente no pseudoescopo
`PLATFORM/none`.
