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
    getDynamoData,
    isGlobalOrganizationRole,
    validateSaaSRoleAssignment,
    validateSaaSTargetRoleExists,
    TENANT_DEFAULT_ROLES,
    buildTenantRoleItem,
    TENANT_ROLES,
    getAllDynamoItems
)

# Role do primeiro usuário do tenant quando o body não informa uma: 'tecnico' é a
# role de tenant que opera o equipamento (o antigo default 'manager' é role de
# organização e não existe no escopo do tenant).
DEFAULT_TENANT_USER_ROLE = 'tecnico'
DEFAULT_TENANT_ROLE = 'tecnico'

def seed_tenant_roles(org_id, tenant_id, region):
    """Semeia as roles padrão do tenant em TENANT#{tenantId}#ROLES.

    Roda antes de criar o primeiro usuário: a role dele é validada nesse escopo, e um
    usuário de tenant sem role correspondente não consegue autenticar em nenhum
    endpoint (a resolução de role é estrita por tenant).
    """
    print(f"INFO: Semeando roles padrão para o Tenant {tenant_id}...")
    now = datetime.now().isoformat()
    for role_id, role_data in TENANT_DEFAULT_ROLES.items():
        item = buildTenantRoleItem(tenant_id, org_id, role_id, role_data, now)
        res = insertDynamoDB(environ['DYNAMODB_TABLE_ROLES'], item, True, region)
        if not res['success']:
            raise Exception(f"500::Erro ao semear a role '{role_id}' do tenant: {res['message']}")

def create_tenant_user(org_id, tenant_id, user_data, region, stage):
    """Cria o primeiro usuário do novo tenant"""
    print(f"INFO: Criando usuário para o tenant {tenant_id}...")

    validate = validateInput([
        {'field': 'email', 'type': 'str', 'mandatory': True, 'pattern': r'(?i)^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'},
        {'field': 'name', 'type': 'str', 'mandatory': True},
        {'field': 'password', 'type': 'str', 'mandatory': False},
        {'field': 'role', 'type': 'str', 'mandatory': False},
        {'field': 'tenantRole', 'type': 'str', 'mandatory': False}
    ], user_data)
    if not validate['success']:
        raise Exception(f"400::Dados do usuário inválidos: {validate['message']}")

    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada, não foi possível criar o usuário do tenant")

    client = boto3.client('cognito-idp', region_name=region)
    email = user_data['email']
    name = user_data['name']
    password = user_data.get('password')
    role = user_data.get('role', DEFAULT_TENANT_USER_ROLE)
    tenant_role = user_data.get('tenantRole', DEFAULT_TENANT_ROLE)
    if tenant_role not in TENANT_ROLES:
        raise Exception("400::tenantRole inválida")
    
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
                {"Name": "custom:tenantId", "Value": tenant_id},
                {"Name": "custom:organizationRole", "Value": role},
                {"Name": "custom:tenantRole", "Value": tenant_role}
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
            "tenantId": tenant_id,
            "role": role,
            "tenantRole": tenant_role,
            "name": name,
            "email": email,
            "status": "active",
            "createdAt": datetime.now().isoformat()
        }
        insert = insertDynamoDB(environ['DYNAMODB_TABLE_USERS'], user_item, False, region)
        if not insert['success']:
            raise Exception(f"500::Erro ao salvar usuário do tenant no banco: {insert['message']}")
        return user_id
    except client.exceptions.UsernameExistsException:
        raise Exception("400::Este e-mail já está cadastrado no sistema.")
    except Exception as e:
        if "::" in str(e):
            raise
        raise Exception(f"500::Falha ao criar usuário do tenant: {str(e)}")

def handler(event, context):
    print("INFO: Iniciando criação de tenant SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'tenant', 'create', environ['REGION'])
        
        # 3. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 4. Validação de Input
        validate = validateInput([
            {'field': 'organizationId', 'type': 'str', 'mandatory': True},
            {'field': 'name', 'type': 'str', 'mandatory': True},
            {'field': 'cnpj', 'type': 'str', 'mandatory': False, 'pattern': r'(?i)^([A-Z0-9]{14}|[A-Z0-9]{2}\.[A-Z0-9]{3}\.[A-Z0-9]{3}/[A-Z0-9]{4}-[A-Z0-9]{2})$'},
            {'field': 'cpf', 'type': 'str', 'mandatory': False, 'pattern': r'^(\d{11}|\d{3}\.\d{3}\.\d{3}-\d{2})$'},
            {'field': 'user', 'type': 'dict', 'mandatory': False}
        ], body)

        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        if not body.get('cnpj') and not body.get('cpf'):
            raise Exception("400::Informe o CNPJ (pessoa jurídica) ou o CPF (pessoa física).")
        if body.get('cnpj') and body.get('cpf'):
            raise Exception("400::Informe apenas CNPJ ou CPF, não ambos.")
            
        # 5. Execução
        result = process_request(auth, body, token_data['data'], environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 201,
            "message": "Tenant criado com sucesso",
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
    return ''.join(c for c in cnpj_raw if c.isalnum()).upper()

def _normalize_cpf(cpf_raw):
    return ''.join(c for c in cpf_raw if c.isdigit())

def process_request(auth, body, token_claims, region, stage):
    org_id = body['organizationId']
    tenant_name = body['name']
    cnpj = _normalize_cnpj(body['cnpj']) if body.get('cnpj') else None
    cpf = _normalize_cpf(body['cpf']) if body.get('cpf') else None

    # 1. Segurança: Verificação de Escopo
    has_global_scope = isGlobalOrganizationRole(token_claims.get('organizationRole'))
    if not has_global_scope and org_id != auth['organizationId']:
        raise Exception("403::Acesso negado para esta organização")

    # 1.1 Para chamadores globais, validar que a organização alvo existe e está ativa
    if has_global_scope:
        org_res = getDynamoData(environ['DYNAMODB_TABLE_ORGANIZATIONS'], "pk", f"ORG#{org_id}", {"key": "sk", "value": "METADATA"}, region)
        if not org_res.get('success'):
            raise Exception(f"500::Falha ao verificar organização: {org_res.get('message')}")
        if not org_res.get('data'):
            raise Exception("404::Organização não encontrada")
        if org_res['data'].get('status') == 'deleted':
            raise Exception("400::Organização está desativada")

    # 2. Verificar nome duplicado dentro da organização
    dynamodb = boto3.resource('dynamodb', region_name=region)
    tenant_table = dynamodb.Table(environ['DYNAMODB_TABLE_TENANTS'])
    existing = getAllDynamoItems(tenant_table,
        KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"ORG#{org_id}#TENANTS")
    )
    for item in existing:
        if item.get('name', '').lower() == tenant_name.lower():
            raise Exception(f"400::Já existe um tenant cadastrado com o nome '{tenant_name}' nesta organização.")
        if cnpj and item.get('cnpj') == cnpj:
            raise Exception("400::Já existe um tenant cadastrado com o CNPJ informado nesta organização.")
        if cpf and item.get('cpf') == cpf:
            raise Exception("400::Já existe um tenant cadastrado com o CPF informado nesta organização.")

    # 3. Gravar Tenant
    tenant_id = str(uuid4())
    now = datetime.now().isoformat()
    
    item = {
        "pk": f"ORG#{org_id}#TENANTS",
        "sk": f"TENANT#{tenant_id}",
        "tenantId": tenant_id,
        "organizationId": org_id,
        "name": tenant_name,
        "status": "active",
        "createdAt": now
    }
    if cnpj:
        item['cnpj'] = cnpj
    if cpf:
        item['cpf'] = cpf
    
    db_res = insertDynamoDB(environ['DYNAMODB_TABLE_TENANTS'], item, False, region)
    if not db_res['success']:
        raise Exception(f"500::Erro ao salvar tenant no banco: {db_res['message']}")
    
    # 3. Semear as roles do tenant (escopo em que os grants de sensor/ação são avaliados)
    seed_tenant_roles(org_id, tenant_id, region)

    # 4. Criar usuário opcional
    user_id = None
    if 'user' in body:
        user_role = body['user'].get('role', DEFAULT_TENANT_USER_ROLE)
        validateSaaSRoleAssignment(
            token_claims.get('organizationRole'), org_id, user_role
        )
        # Usuário de tenant → a role tem que existir no tenant, não na organização
        validateSaaSTargetRoleExists(
            environ['DYNAMODB_TABLE_ROLES'], org_id, user_role, region, tenant_id
        )
        user_id = create_tenant_user(org_id, tenant_id, body['user'], region, stage)
        
    return {
        "tenantId": tenant_id,
        "userId": user_id
    }
