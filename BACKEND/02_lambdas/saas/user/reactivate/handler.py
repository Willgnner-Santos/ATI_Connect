import json
import boto3
import traceback
from os import environ
from datetime import datetime
from plataforma_ati import (
    validateTokenSaaS, 
    authorizeSaaS, 
    lambdaReturn,
    getSaaSConfig,
    isGlobalOrganizationRole,
    validateSaaSRoleAssignment
)

def handler(event, context):
    print("INFO: Iniciando reativação de usuário SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Verifica permissão 'user:write')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'user', 'write', environ['REGION'])

        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::User ID não informado")
        user_id = path_params['id']

        # 4. Execução
        process_request(auth, user_id, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Usuário reativado com sucesso"
        })

    except Exception as e:
        traceback.print_exc()
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

def process_request(auth, user_id, region, stage):
    config = getSaaSConfig(region, stage)
    if not config:
        raise Exception("500::Configuração do Cognito SaaS não encontrada")
        
    cognito = boto3.client('cognito-idp', region_name=region)
    try:
        user_info = cognito.admin_get_user(
            UserPoolId=config['userPoolId'],
            Username=user_id
        )
    except cognito.exceptions.UserNotFoundException:
        raise Exception("404::Usuário não encontrado")
    except Exception as e:
        raise Exception(f"500::Erro ao buscar usuário no Cognito: {str(e)}")

    # Extrair atributos customizados do usuário alvo
    target_attrs = {}
    for attr in user_info['UserAttributes']:
        if attr['Name'].startswith('custom:'):
            key = attr['Name'].replace('custom:', '')
            target_attrs[key] = attr['Value']

    target_org_id = target_attrs.get('organizationId', 'none')
    target_tenant_id = target_attrs.get('tenantId', 'none')
    target_role = target_attrs.get('organizationRole', 'guest')

    # Validação de escopo de segurança
    caller_org_id = auth.get('organizationId')
    caller_tenant_id = auth.get('tenantId')
    caller_role = auth.get('role')

    validateSaaSRoleAssignment(caller_role, target_org_id, target_role)
    has_global_scope = isGlobalOrganizationRole(caller_role)
    if not has_global_scope:
        # 1. Mesma organização
        if target_org_id != caller_org_id:
            raise Exception("403::Acesso negado para esta organização")

        # 2. Se for admin de tenant (ou manager), só pode reativar do mesmo tenant
        is_org_admin = caller_tenant_id in (None, 'none')
        if not is_org_admin:
            if target_tenant_id != caller_tenant_id:
                raise Exception("403::Acesso negado para este tenant")

    # Habilitar primeiro no Cognito; se o DynamoDB falhar, compensar desabilitando.
    print("INFO: Habilitando usuário no Cognito...")
    try:
        cognito.admin_enable_user(
            UserPoolId=config['userPoolId'],
            Username=user_id
        )
    except Exception as e:
        raise Exception(f"500::Erro ao habilitar usuário no Cognito: {str(e)}")

    # Reativar no DynamoDB (status = 'active')
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_USERS'])
    pk = f"ORG#{target_org_id}#USER"

    try:
        table.update_item(
            Key={'pk': pk, 'sk': f"USER#{user_id}"},
            UpdateExpression="SET #status = :status, updatedAt = :updatedAt",
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":status": "active",
                ":updatedAt": datetime.now().isoformat()
            },
            ConditionExpression="attribute_exists(pk)"
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        try:
            cognito.admin_disable_user(UserPoolId=config['userPoolId'], Username=user_id)
        except Exception as rollback_error:
            print(f"ERROR: Falha ao compensar reativação no Cognito: {rollback_error}")
        raise Exception("404::Usuário não encontrado")
    except Exception as e:
        try:
            cognito.admin_disable_user(UserPoolId=config['userPoolId'], Username=user_id)
        except Exception as rollback_error:
            print(f"ERROR: Falha ao compensar reativação no Cognito: {rollback_error}")
        raise Exception(f"500::Erro ao reativar usuário no DynamoDB: {str(e)}")

    return True
