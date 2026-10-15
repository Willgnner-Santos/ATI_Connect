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
    print("INFO: Iniciando exclusão de usuário SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Verifica permissão 'user:delete')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'user', 'delete', environ['REGION'])

        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::User ID não informado")
        user_id = path_params['id']

        # 4. Execução
        process_request(auth, user_id, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Usuário deletado com sucesso"
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

        # 2. Se for admin de tenant (ou manager), só pode deletar do mesmo tenant
        is_org_admin = caller_tenant_id in (None, 'none')
        if not is_org_admin:
            if target_tenant_id != caller_tenant_id:
                raise Exception("403::Acesso negado para este tenant")

    # Soft Delete no DynamoDB (status = 'deleted')
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
                ":status": "deleted",
                ":updatedAt": datetime.now().isoformat()
            },
            ConditionExpression="attribute_exists(pk)"
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        raise Exception("404::Usuário não encontrado")

    # Desabilitar no Cognito (soft delete) — falha aqui não pode ser silenciosa:
    # o registro já foi marcado como 'deleted' no DynamoDB, então o usuário deve
    # de fato perder acesso. Se o disable falhar, reportamos erro em vez de sucesso.
    print("INFO: Desabilitando usuário no Cognito...")
    try:
        cognito.admin_disable_user(
            UserPoolId=config['userPoolId'],
            Username=user_id
        )
    except Exception as e:
        raise Exception(f"500::Usuário marcado como deletado no banco, mas falha ao desabilitar no Cognito — acesso pode continuar ativo: {str(e)}")

    return True
