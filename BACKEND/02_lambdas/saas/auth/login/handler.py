import json
import boto3
import base64
from os import environ
from plataforma_ati import (
    lambdaReturn, 
    validateInput, 
    getSaaSConfig, 
    genSecretHash, 
    getDynamoData
)

def get_user_profile(organization_id, tenant_id, region, stage):
    """Busca nomes amigáveis da Org e Tenant para enriquecer o login e valida status.

    Falha real de infraestrutura (success=False) é fail-closed: bloqueia o login.
    'Não encontrado' (success=True, data={}) mantém os defaults e permite login.
    """
    profile = {
        "organizationName": "Desconhecida",
        "tenantName": "Nenhum",
        "isOrgDeleted": False,
        "isTenantDeleted": False
    }

    # Buscar Org
    org_table = f"plataforma-ati-{stage}-organizations"
    org_res = getDynamoData(org_table, "pk", f"ORG#{organization_id}", {"key": "sk", "value": "METADATA"}, region)
    if not org_res.get('success'):
        raise Exception(f"500::Falha ao verificar status da organização: {org_res.get('message')}")
    if org_res.get('data'):
        org_data = org_res['data']
        if org_data.get('status') == 'deleted':
            profile["isOrgDeleted"] = True
        profile["organizationName"] = org_data.get('name', 'Desconhecida')

    # Buscar Tenant (se houver)
    if tenant_id and tenant_id != 'none':
        tenant_table = f"plataforma-ati-{stage}-tenants"
        tenant_res = getDynamoData(tenant_table, "pk", f"ORG#{organization_id}#TENANTS", {"key": "sk", "value": f"TENANT#{tenant_id}"}, region)
        if not tenant_res.get('success'):
            raise Exception(f"500::Falha ao verificar status do tenant: {tenant_res.get('message')}")
        if tenant_res.get('data'):
            tenant_data = tenant_res['data']
            if tenant_data.get('status') == 'deleted':
                profile["isTenantDeleted"] = True
            profile["tenantName"] = tenant_data.get('name', 'Nenhum')

    return profile

def handler(event, context):
    print("INFO: Iniciando Login SaaS...")
    try:
        # 1. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 2. Validação de Input
        validate = validateInput([
            {'field': 'email', 'type': 'str', 'mandatory': True},
            {'field': 'password', 'type': 'str', 'mandatory': True}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        # 3. Execução da lógica
        result = process_request(body, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Login realizado com sucesso",
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

def process_request(body, region, stage):
    email = body['email']
    password = body['password']

    # 1. Obter Config do Cognito do Layer
    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada")
        
    client = boto3.client('cognito-idp', region_name=region)
    
    # 2. Preparar parâmetros de autenticação
    auth_params = {
        'USERNAME': email,
        'PASSWORD': password
    }
    
    # Se houver Client Secret, gerar o SECRET_HASH usando o layer
    if config.get('clientSecret'):
        auth_params['SECRET_HASH'] = genSecretHash(config['clientId'], config['clientSecret'], email)
        
    # 3. Autenticação no Cognito
    try:
        auth_response = client.initiate_auth(
            ClientId=config['clientId'],
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters=auth_params
        )
    except client.exceptions.NotAuthorizedException:
        raise Exception("401::Usuário ou senha incorretos")
    except client.exceptions.UserNotFoundException:
        raise Exception("401::Usuário ou senha incorretos")
    except Exception as e:
        raise Exception(f"500::Erro na autenticação: {str(e)}")

    # 3.1 Challenges (ex: NEW_PASSWORD_REQUIRED, MFA) não têm AuthenticationResult
    if 'AuthenticationResult' not in auth_response:
        challenge_name = auth_response.get('ChallengeName', 'DESCONHECIDO')
        raise Exception(f"400::Ação adicional necessária: {challenge_name}")

    # 4. Obter Atributos Customizados do usuário
    access_token = auth_response['AuthenticationResult']['AccessToken']
    user_info = client.get_user(AccessToken=access_token)
    
    custom_attrs = {}
    name = ""
    for attr in user_info['UserAttributes']:
        if attr['Name'] == 'name':
            name = attr['Value']
        elif attr['Name'].startswith('custom:'):
            key = attr['Name'].replace('custom:', '')
            custom_attrs[key] = attr['Value']
    
    org_id = custom_attrs.get('organizationId', 'none')
    tenant_id = custom_attrs.get('tenantId', 'none')
    role = custom_attrs.get('organizationRole', 'guest')
    
    # 5. Enriquecer com nomes do DynamoDB e verificar se foram deletados/desativados
    profile = get_user_profile(org_id, tenant_id, region, stage)
    if profile.get('isOrgDeleted'):
        raise Exception("401::Acesso negado: Organização desativada")
    if profile.get('isTenantDeleted'):
        raise Exception("401::Acesso negado: Tenant desativado")
    
    return {
        "authorization": {
            "access_token": access_token,
            "id_token": auth_response['AuthenticationResult']['IdToken'],
            "refresh_token": auth_response['AuthenticationResult'].get('RefreshToken'),
            "expires_in": auth_response['AuthenticationResult']['ExpiresIn'],
            "token_type": auth_response['AuthenticationResult']['TokenType']
        },
        "user": {
            "name": name,
            "email": email,
            "username": user_info['Username'],
            "organizationId": org_id,
            "organizationName": profile["organizationName"],
            "tenantId": tenant_id,
            "tenantName": profile["tenantName"],
            "role": role
        }
    }
