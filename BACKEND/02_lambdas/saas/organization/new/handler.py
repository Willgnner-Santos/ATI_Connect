import json
import boto3
from os import environ
from uuid import uuid4
from datetime import datetime
import base64
from plataforma_ati import (
    validateInput, 
    lambdaReturn, 
    authorizeSaaS, 
    insertDynamoDB, 
    validateTokenSaaS, 
    getSaaSConfig,
    getAllDynamoItems
)

DEFAULT_ROLES = {
    "admin": {
        "roleName": "Administrador",
        "description": "Administrador da organização",
        "permissions": [
            "organization:read",
            "organization:write",
            "tenant:*",
            "user:*",
            "equipment:*",
            "action:*",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:*"
        ],
        "isSystemRole": True
    },
    "manager": {
        "roleName": "Gerente",
        "description": "Gerente de tenant",
        "permissions": [
            "tenant:read",
            "user:read",
            "user:write",
            "equipment:*",
            "action:*",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:*"
        ],
        "isSystemRole": True
    },
    "viewer": {
        "roleName": "Visualizador",
        "description": "Visualizador com acesso apenas leitura",
        "permissions": [
            "tenant:read",
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

def create_first_admin(org_id, admin_data, region, stage):
    """Cria o primeiro usuário admin para a nova organização"""
    print(f"INFO: Criando primeiro admin para Org {org_id}...")

    validate = validateInput([
        {'field': 'email', 'type': 'str', 'mandatory': True, 'pattern': r'(?i)^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'},
        {'field': 'name', 'type': 'str', 'mandatory': True},
        {'field': 'password', 'type': 'str', 'mandatory': False}
    ], admin_data)
    if not validate['success']:
        raise Exception(f"400::Dados do admin inválidos: {validate['message']}")

    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada, não foi possível criar o admin")

    client = boto3.client('cognito-idp', region_name=region)
    email = admin_data['email']
    name = admin_data['name']
    password = admin_data.get('password')

    try:
        # 1. Criar no Cognito
        create_params = {
            "UserPoolId": config['userPoolId'],
            "Username": email,
            "UserAttributes": [
                {"Name": "name", "Value": name},
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:organizationId", "Value": org_id},
                {"Name": "custom:tenantId", "Value": "none"},
                {"Name": "custom:organizationRole", "Value": "admin"}
            ]
        }
        if password: 
            create_params["MessageAction"] = "SUPPRESS"
            
        response = client.admin_create_user(**create_params)
        user_id = response['User']['Username']
        
        # 2. Senha permanente
        if password:
            client.admin_set_user_password(
                UserPoolId=config['userPoolId'],
                Username=user_id,
                Password=password,
                Permanent=True
            )
            
        # 3. Salvar no DynamoDB
        user_item = {
            "pk": f"ORG#{org_id}#USER",
            "sk": f"USER#{user_id}",
            "userId": user_id,
            "organizationId": org_id,
            "tenantId": "none",
            "role": "admin",
            "name": name,
            "email": email,
            "status": "active",
            "createdAt": datetime.now().isoformat()
        }
        insert = insertDynamoDB(environ['DYNAMODB_TABLE_USERS'], user_item, False, region)
        if not insert['success']:
            raise Exception(f"500::Erro ao salvar admin no banco: {insert['message']}")
        return user_id
    except client.exceptions.UsernameExistsException:
        raise Exception("400::Este e-mail já está cadastrado no sistema.")
    except Exception as e:
        if "::" in str(e):
            raise
        raise Exception(f"500::Falha ao criar admin da organização: {str(e)}")

def seed_organization_roles(org_id, region):
    """Semeia as roles padrão para a nova organização"""
    print(f"INFO: Semeando roles padrão para Org {org_id}...")
    table_name = environ['DYNAMODB_TABLE_ROLES']
    now = datetime.now().isoformat()

    for role_id, role_data in DEFAULT_ROLES.items():
        item = {
            "pk": f"ORG#{org_id}#ROLES",
            "sk": f"ROLE#{role_id}",
            "organizationId": org_id,
            "roleId": role_id,
            "roleName": role_data['roleName'],
            "description": role_data['description'],
            "permissions": role_data['permissions'],
            "isSystemRole": role_data['isSystemRole'],
            "createdAt": now,
            "updatedAt": now
        }
        insertDynamoDB(table_name, item, True, region)

def handler(event, context):
    print("INFO: Iniciando criação de organização SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Apenas superadmin pode criar organizações)
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'organization', 'create', environ['REGION'])
        
        # 3. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)
        
        # 4. Validação de Input
        validate = validateInput([
            {'field': 'name', 'type': 'str', 'mandatory': True},
            {'field': 'email', 'type': 'str', 'mandatory': True, 'pattern': r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'},
            {'field': 'cnpj', 'type': 'str', 'mandatory': True, 'pattern': r'(?i)^([A-Z0-9]{14}|[A-Z0-9]{2}\.[A-Z0-9]{3}\.[A-Z0-9]{3}/[A-Z0-9]{4}-[A-Z0-9]{2})$'},
            {'field': 'type', 'type': 'str', 'mandatory': False, 'pattern': r'^(ati|distributor)$'},
            {'field': 'admin', 'type': 'dict', 'mandatory': False}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")
            
        # 5. Execução
        result = process_request(auth, body, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 201,
            "message": "Organização criada com sucesso",
            "data": result
        })
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        try:
            err = str(e).split("::")
            return lambdaReturn({
                "statusCode": int(err[0]),
                "message": err[1]
            })
        except:
            return lambdaReturn({
                "statusCode": 500,
                "message": f"Erro interno: {str(e)}"
            })

def _normalize_cnpj(cnpj_raw):
    """Remove máscara (. / -) e normaliza para maiúsculas — suporta formato alfanumérico."""
    return ''.join(c for c in cnpj_raw if c.isalnum()).upper()

def process_request(auth, body, region, stage):
    cnpj = _normalize_cnpj(body['cnpj']) if body.get('cnpj') else None

    # 1. Verificar nome e CNPJ duplicados entre organizações ativas
    dynamodb = boto3.resource('dynamodb', region_name=region)
    org_table = dynamodb.Table(environ['DYNAMODB_TABLE_ORGANIZATIONS'])
    existing = getAllDynamoItems(org_table,
        IndexName='status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('active'),
        FilterExpression=boto3.dynamodb.conditions.Attr('sk').eq('METADATA')
    )
    for item in existing:
        if item.get('name', '').lower() == body['name'].lower():
            raise Exception(f"400::Já existe uma organização cadastrada com o nome '{body['name']}'.")
        if cnpj and item.get('cnpj') == cnpj:
            raise Exception(f"400::Já existe uma organização cadastrada com o CNPJ informado.")

    org_id = str(uuid4())
    now = datetime.now().isoformat()

    # 2. Gravar Organização
    item = {
        "pk": f"ORG#{org_id}",
        "sk": "METADATA",
        "organizationId": org_id,
        "type": body.get('type', 'distributor'),
        "name": body['name'],
        "email": body['email'],
        "status": "active",
        "createdAt": now
    }
    if cnpj:
        item['cnpj'] = cnpj
    
    db_res = insertDynamoDB(environ['DYNAMODB_TABLE_ORGANIZATIONS'], item, False, region)
    if not db_res['success']:
        raise Exception(f"500::Erro ao salvar organização no banco: {db_res['message']}")
    
    # 2. Semeia roles padrão
    seed_organization_roles(org_id, region)
    
    # 3. Criar primeiro admin se fornecido
    admin_user_id = None
    if 'admin' in body:
        admin_user_id = create_first_admin(org_id, body['admin'], region, stage)
        
    return {
        "organizationId": org_id,
        "adminUserId": admin_user_id
    }
