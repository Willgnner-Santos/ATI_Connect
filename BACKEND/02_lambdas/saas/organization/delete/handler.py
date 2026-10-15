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
    validateSaaSRoleAssignment,
    PLATFORM_ORGANIZATION_ID,
    getAllDynamoItems
)

def get_users_by_org(organization_id, region, table_name):
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    try:
        return getAllDynamoItems(table,
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"ORG#{organization_id}#USER")
        )
    except Exception as e:
        print(f"ERROR: Falha ao buscar usuários por org: {e}")
        return []

def handler(event, context):
    print("INFO: Iniciando exclusão de organização SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Verifica permissão 'organization:delete')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'organization', 'delete', environ['REGION'])

        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Organization ID não informado")
        org_id = path_params['id']

        # 4. Execução
        process_request(auth, org_id, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Organização deletada com sucesso"
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

def process_request(auth, org_id, region, stage):
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    # 1. Segurança: Apenas superadmin ou admin da própria organização
    if org_id == PLATFORM_ORGANIZATION_ID:
        raise Exception("403::A organização PLATFORM não pode ser deletada")

    has_global_scope = isGlobalOrganizationRole(auth.get('role'))
    validateSaaSRoleAssignment(auth.get('role'), org_id, 'viewer')
    if not has_global_scope and org_id != auth['organizationId']:
        raise Exception("403::Acesso negado para esta organização")

    try:
        table.update_item(
            Key={
                'pk': f"ORG#{org_id}",
                'sk': "METADATA"
            },
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
        raise Exception("404::Organização não encontrada")

    # 2. Desabilitar todos os usuários da organização no Cognito
    config = getSaaSConfig(region, stage)
    if config:
        cognito = boto3.client('cognito-idp', region_name=region)
        users = get_users_by_org(org_id, region, environ['DYNAMODB_TABLE_USERS'])
        for user in users:
            try:
                cognito.admin_disable_user(
                    UserPoolId=config['userPoolId'],
                    Username=user['userId']
                )
            except Exception as e:
                print(f"WARNING: Erro ao desabilitar usuário {user['userId']} no Cognito: {e}")
        
    return True
