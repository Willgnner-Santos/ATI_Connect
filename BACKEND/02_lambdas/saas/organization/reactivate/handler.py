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
        print(f"ERROR: Falha ao buscar usuários da org para reativação: {e}")
        return []

def handler(event, context):
    print("INFO: Iniciando reativação de organização SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Verifica permissão 'organization:write')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'organization', 'write', environ['REGION'])

        # Apenas superadmin pode reativar organização
        if not isGlobalOrganizationRole(token_data['data'].get('organizationRole')):
            raise Exception("403::Acesso negado: role sem escopo global de organizações")

        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Organization ID não informado")
        org_id = path_params['id']

        if org_id == PLATFORM_ORGANIZATION_ID:
            raise Exception("403::A organização PLATFORM não pode ser reativada")

        validateSaaSRoleAssignment(auth.get('role'), org_id, 'viewer')

        # 4. Reativar no DynamoDB (status = 'active'), somente se estiver 'deleted'
        dynamodb = boto3.resource('dynamodb', region_name=environ['REGION'])
        table = dynamodb.Table(environ['DYNAMODB_TABLE_ORGANIZATIONS'])

        current = table.get_item(Key={'pk': f"ORG#{org_id}", 'sk': 'METADATA'})
        if 'Item' not in current:
            raise Exception("404::Organização não encontrada")
        if current['Item'].get('status') != 'deleted':
            raise Exception("400::Organização não está desativada")

        try:
            table.update_item(
                Key={'pk': f"ORG#{org_id}", 'sk': 'METADATA'},
                UpdateExpression="SET #status = :status, updatedAt = :updatedAt",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "active",
                    ":updatedAt": datetime.now().isoformat(),
                    ":deletedStatus": "deleted"
                },
                ConditionExpression="attribute_exists(pk) AND #status = :deletedStatus"
            )
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            raise Exception("404::Organização não encontrada ou não está desativada")

        # 5. Habilitar todos os usuários da organização no Cognito (se não estiverem individualmente deletados)
        config = getSaaSConfig(environ['REGION'], environ.get('STAGE', 'sandbox'))
        if config:
            cognito = boto3.client('cognito-idp', region_name=environ['REGION'])
            users = get_users_by_org(org_id, environ['REGION'], environ['DYNAMODB_TABLE_USERS'])
            for user in users:
                # Se o usuário não estiver individualmente deletado
                if user.get('status') != 'deleted':
                    try:
                        cognito.admin_enable_user(
                            UserPoolId=config['userPoolId'],
                            Username=user['userId']
                        )
                    except Exception as e:
                        print(f"WARNING: Erro ao habilitar usuário {user['userId']} no Cognito: {e}")

        return lambdaReturn({
            "statusCode": 200,
            "message": "Organização reativada com sucesso"
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
