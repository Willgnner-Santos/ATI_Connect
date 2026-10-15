"""
Seed das roles de tenant no DynamoDB para um tenant existente.

Uso:
    python scripts/seed-tenant-roles-sandbox.py

Necessário quando o tenant foi criado antes das roles atuais (gestor/tecnico/user)
ou quando as roles antigas (client/technician) precisam ser substituídas.
"""

import boto3
from datetime import datetime

REGION = "sa-east-1"
STAGE = "sandbox"
ORGANIZATION_ID = "c8e7a6f7-7803-4e09-91b5-e7591b7f4309"
TENANT_ID = "8eb11758-c4fa-4c79-8e3d-80aca4264e12"

TABLE_ROLES = f"plataforma-ati-{STAGE}-organization-roles"

TENANT_DEFAULT_ROLES = {
    "gestor": {
        "roleName": "Gestor",
        "description": "Gerencia técnicos e usuários do tenant, sem operar equipamentos",
        "permissions": [
            "user:read",
            "user:write",
            "equipment:read",
            "dashboard:read",
            "report:read",
            "catalog:read"
        ],
        "isSystemRole": True
    },
    "tecnico": {
        "roleName": "Técnico",
        "description": "Opera o equipamento: envia comandos liberados para a role",
        "permissions": [
            "equipment:read",
            "equipment:update",
            "action:read",
            "action:execute",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:read",
            "virtual-technician:write",
            "virtual-technician:delete"
        ],
        "isSystemRole": True
    },
    "user": {
        "roleName": "Usuário",
        "description": "Apenas observa: vê sensores liberados, sem enviar comandos",
        "permissions": [
            "equipment:read",
            "action:read",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:read"
        ],
        "isSystemRole": True
    }
}


def main():
    print(f"\nSeed de roles de tenant — sandbox")
    print(f"Tenant      : {TENANT_ID}")
    print(f"Organização : {ORGANIZATION_ID}")
    print(f"Tabela      : {TABLE_ROLES}\n")

    dynamo = boto3.client("dynamodb", region_name=REGION)
    timestamp = datetime.now().isoformat()

    for role_id, role_data in TENANT_DEFAULT_ROLES.items():
        print(f"  Seeding {role_id:10s} ...", end=" ")
        try:
            dynamo.put_item(
                TableName=TABLE_ROLES,
                Item={
                    "pk": {"S": f"TENANT#{TENANT_ID}#ROLES"},
                    "sk": {"S": f"ROLE#{role_id}"},
                    "tenantId": {"S": TENANT_ID},
                    "organizationId": {"S": ORGANIZATION_ID},
                    "roleId": {"S": role_id},
                    "roleName": {"S": role_data["roleName"]},
                    "description": {"S": role_data["description"]},
                    "permissions": {"L": [{"S": p} for p in role_data["permissions"]]},
                    "isSystemRole": {"BOOL": role_data["isSystemRole"]},
                    "createdAt": {"S": timestamp},
                    "updatedAt": {"S": timestamp},
                },
            )
            print("OK")
        except Exception as e:
            print(f"ERRO: {e}")

    print(f"\nPronto. Roles seeded: {', '.join(TENANT_DEFAULT_ROLES.keys())}")


if __name__ == "__main__":
    main()
