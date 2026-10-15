"""
Seed de usuários de teste — um por perfil de acesso (sandbox).

Uso:
    python scripts/seed-users-sandbox.py

Requer AWS credentials com acesso ao Cognito e DynamoDB de sandbox.
"""

import boto3
import json
from datetime import datetime

REGION = "sa-east-1"
STAGE = "sandbox"
ORGANIZATION_ID = "c8e7a6f7-7803-4e09-91b5-e7591b7f4309"
TENANT_ID = "8eb11758-c4fa-4c79-8e3d-80aca4264e12"

TABLE_USERS = f"plataforma-ati-{STAGE}-tenant-users"
TABLE_TENANTS = f"plataforma-ati-{STAGE}-tenants"

DEFAULT_PASSWORD = "Ati@2025!"

USERS = [
    {
        "name": "Superadmin ATI",
        "email": "superadmin@ati-sandbox.com",
        "role": "superadmin",
        "tenantId": "none",
        "tenantRole": "user",
    },
    {
        "name": "Collaborator ATI",
        "email": "collaborator@ati-sandbox.com",
        "role": "collaborator",
        "tenantId": "none",
        "tenantRole": "user",
    },
    {
        "name": "Admin ATI",
        "email": "admin@ati-sandbox.com",
        "role": "admin",
        "tenantId": "none",
        "tenantRole": "user",
    },
    {
        "name": "Manager ATI",
        "email": "manager@ati-sandbox.com",
        "role": "manager",
        "tenantId": "none",
        "tenantRole": "user",
    },
    {
        "name": "Viewer ATI",
        "email": "viewer@ati-sandbox.com",
        "role": "viewer",
        "tenantId": "none",
        "tenantRole": "user",
    },
    {
        "name": "Gestor ATI",
        "email": "gestor@ati-sandbox.com",
        "role": "gestor",
        "tenantId": TENANT_ID,
        "tenantRole": "gestor",
    },
    {
        "name": "Tecnico ATI",
        "email": "tecnico@ati-sandbox.com",
        "role": "tecnico",
        "tenantId": TENANT_ID,
        "tenantRole": "tecnico",
    },
    {
        "name": "Usuario ATI",
        "email": "usuario@ati-sandbox.com",
        "role": "user",
        "tenantId": TENANT_ID,
        "tenantRole": "user",
    },
]


def get_cognito_config():
    dynamo = boto3.client("dynamodb", region_name=REGION)
    resp = dynamo.get_item(
        TableName=TABLE_TENANTS,
        Key={"pk": {"S": "CONFIG"}, "sk": {"S": "COGNITO#SAAS"}},
    )
    item = resp.get("Item")
    if not item:
        raise Exception("Configuração do Cognito SaaS não encontrada no DynamoDB.")
    return {
        "userPoolId": item["userPoolId"]["S"],
    }


def create_user(cognito, dynamo, user_pool_id, user):
    email = user["email"]
    print(f"  Criando {user['role']:12s} → {email} ...", end=" ")

    try:
        resp = cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "name", "Value": user["name"]},
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:organizationId", "Value": ORGANIZATION_ID},
                {"Name": "custom:tenantId", "Value": user["tenantId"]},
                {"Name": "custom:organizationRole", "Value": user["role"]},
                {"Name": "custom:tenantRole", "Value": user["tenantRole"]},
            ],
        )
        username = resp["User"]["Username"]

        cognito.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=username,
            Password=DEFAULT_PASSWORD,
            Permanent=True,
        )

        dynamo.put_item(
            TableName=TABLE_USERS,
            Item={
                "pk": {"S": f"ORG#{ORGANIZATION_ID}#USER"},
                "sk": {"S": f"USER#{username}"},
                "userId": {"S": username},
                "organizationId": {"S": ORGANIZATION_ID},
                "tenantId": {"S": user["tenantId"]},
                "role": {"S": user["role"]},
                "tenantRole": {"S": user["tenantRole"]},
                "name": {"S": user["name"]},
                "email": {"S": email},
                "status": {"S": "active"},
                "createdAt": {"S": datetime.now().isoformat()},
                "createdBy": {"S": "seed-script"},
            },
        )

        print("OK")
        return True

    except cognito.exceptions.UsernameExistsException:
        print("já existe, pulando")
        return False
    except Exception as e:
        print(f"ERRO: {e}")
        return False


def main():
    print(f"\nSeed de usuários — sandbox")
    print(f"Organização : {ORGANIZATION_ID}")
    print(f"Tenant      : {TENANT_ID}")
    print(f"Região      : {REGION}\n")

    config = get_cognito_config()
    user_pool_id = config["userPoolId"]
    print(f"User Pool   : {user_pool_id}\n")

    cognito = boto3.client("cognito-idp", region_name=REGION)
    dynamo = boto3.client("dynamodb", region_name=REGION)

    ok = sum(create_user(cognito, dynamo, user_pool_id, u) for u in USERS)

    print(f"\n{ok}/{len(USERS)} usuários criados.")
    print(f"Senha padrão: {DEFAULT_PASSWORD}")


if __name__ == "__main__":
    main()
