import json
import boto3
from os import environ
from datetime import datetime
import base64
from plataforma_ati import (
    validateInput, 
    validateTokenSaaS, 
    authorizeSaaS, 
    lambdaReturn, 
    getSaaSConfig, 
    insertDynamoDB,
    isGlobalOrganizationRole,
    validateSaaSRoleAssignment,
    validateSaaSTargetRoleExists,
    TENANT_ROLES,
    getSaaSRole
)

def create_cognito_user(payload, organization_id, tenant_id, role, tenant_role, region, stage):
    print(f"INFO: Criando usuário no Cognito SaaS para Org {organization_id}...")
    
    # 1. Obter Config do Cognito do Layer
    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada")
        
    client = boto3.client('cognito-idp', region_name=region)
    user_pool_id = config['userPoolId']
    
    password = payload.get('password')
    email = payload['email']
    name = payload['name']
    
    try:
        # 2. Criar usuário
        create_params = {
            "UserPoolId": user_pool_id,
            "Username": email,
            "UserAttributes": [
                {"Name": "name", "Value": name},
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:organizationId", "Value": organization_id},
                {"Name": "custom:tenantId", "Value": tenant_id},
                {"Name": "custom:organizationRole", "Value": role},
                {"Name": "custom:tenantRole", "Value": tenant_role}
            ]
        }
        
        if password:
            create_params["MessageAction"] = "SUPPRESS"
            
        response = client.admin_create_user(**create_params)
        username = response['User']['Username']
        
        # 3. Se houver senha, define como permanente
        if password:
            print("INFO: Definindo senha pré-setada como permanente...")
            client.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=username,
                Password=password,
                Permanent=True
            )
            
        return username
    except client.exceptions.UsernameExistsException:
        raise Exception("400::Este e-mail já está cadastrado no sistema.")
    except Exception as e:
        raise Exception(f"500::Erro ao criar usuário no Cognito: {str(e)}")

def handler(event, context):
    print("INFO: Iniciando criação de usuário SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'user', 'write', environ['REGION'])
        
        # 3. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)
        
        # 4. Validação de Input
        validate = validateInput([
            {'field': 'name', 'type': 'str', 'mandatory': True},
            {'field': 'email', 'type': 'str', 'mandatory': True, 'pattern': r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'},
            {'field': 'organizationId', 'type': 'str', 'mandatory': True},
            {'field': 'tenantId', 'type': 'str', 'mandatory': False},
            {'field': 'role', 'type': 'str', 'mandatory': True},
            {'field': 'tenantRole', 'type': 'str', 'mandatory': False},
            {'field': 'password', 'type': 'str', 'mandatory': False}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")
            
        # 5. Execução
        result = process_request(auth, body, token_data['data'], environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 201,
            "message": "Usuário criado com sucesso",
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

def process_request(auth, body, token_claims, region, stage):
    organization_id = body['organizationId']
    tenant_id = body.get('tenantId', 'none')
    role_name = body['role']
    tenant_role = body.get('tenantRole') or ('tecnico' if tenant_id != 'none' else 'user')
    if tenant_role not in TENANT_ROLES:
        raise Exception("400::tenantRole deve ser gestor, tecnico ou user")

    # 1. Segurança: Verificação de Escopo (checar org/tenant antes de revelar
    # se a role existe na organização alvo, evitando oráculo de enumeração cross-org)
    caller_role = token_claims.get('organizationRole')
    validateSaaSRoleAssignment(caller_role, organization_id, role_name)

    has_global_scope = isGlobalOrganizationRole(caller_role)
    if not has_global_scope:
        if organization_id != auth['organizationId']:
            raise Exception("403::Você só pode criar usuários na sua própria organização.")
        if auth['tenantId'] != 'none' and tenant_id != auth['tenantId']:
             raise Exception("403::Você só pode criar usuários no seu próprio tenant.")

    # Usuário com tenant tem a role resolvida em TENANT#{tenantId}#ROLES, então é lá
    # que ela precisa existir — validar na organização deixaria passar uma role que o
    # usuário nunca conseguiria usar para autenticar
    validateSaaSTargetRoleExists(
        environ['DYNAMODB_TABLE_ROLES'], organization_id, role_name, region, tenant_id
    )

    # 2. Criar no Cognito
    userId = create_cognito_user(body, organization_id, tenant_id, role_name, tenant_role, region, stage)

    # 3. Salvar no DynamoDB (com rollback no Cognito se falhar)
    user_data = {
        "pk": f"ORG#{organization_id}#USER",
        "sk": f"USER#{userId}",
        "userId": userId,
        "organizationId": organization_id,
        "tenantId": tenant_id,
        "role": role_name,
        "tenantRole": tenant_role,
        "name": body['name'],
        "email": body['email'],
        "status": "active",
        "createdAt": datetime.now().isoformat(),
        "createdBy": token_claims.get('username')
    }

    insert = insertDynamoDB(environ['DYNAMODB_TABLE_USERS'], user_data, False, region)
    if not insert['success']:
        config = getSaaSConfig(region, stage)
        if config:
            try:
                boto3.client('cognito-idp', region_name=region).admin_delete_user(
                    UserPoolId=config['userPoolId'],
                    Username=userId
                )
                print(f"INFO: Rollback - usuário {userId} removido do Cognito após falha no DynamoDB")
            except Exception as rollback_err:
                print(f"ERROR: Falha ao fazer rollback do usuário {userId} no Cognito: {rollback_err}")
        raise Exception(f"500::Erro ao salvar usuário no banco: {insert['message']}")

    return {
        "userId": userId,
        "organizationId": organization_id,
        "tenantId": tenant_id
    }
