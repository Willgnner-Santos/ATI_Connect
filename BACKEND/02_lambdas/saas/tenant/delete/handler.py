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
    getAllDynamoItems
)

def get_tenant_by_id(tenant_id, region, table_name):
    """Busca um tenant usando a GSI tenantId-index"""
    print(f"INFO: Buscando tenant {tenant_id} no index...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    try:
        items = getAllDynamoItems(table,
            IndexName='tenantId-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('tenantId').eq(tenant_id)
        )
        return items[0] if items else None
    except Exception as e:
        print(f"ERROR: Falha ao buscar tenant {tenant_id}: {e}")
        return None

def get_users_by_tenant(tenant_id, region, table_name):
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    try:
        return getAllDynamoItems(table,
            IndexName='tenantId-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('tenantId').eq(tenant_id)
        )
    except Exception as e:
        print(f"ERROR: Falha ao buscar usuários do tenant: {e}")
        return []

def handler(event, context):
    print("INFO: Iniciando exclusão de tenant SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Verifica permissão 'tenant:delete')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'tenant', 'delete', environ['REGION'])

        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Tenant ID não informado")
        tenant_id = path_params['id']

        # 4. Execução
        process_request(auth, tenant_id, environ['REGION'], environ.get('STAGE', 'sandbox'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Tenant deletado com sucesso"
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

def process_request(auth, tenant_id, region, stage):
    table_name = environ['DYNAMODB_TABLE_TENANTS']
    
    # 1. Buscar o tenant para obter o organizationId correspondente
    tenant_data = get_tenant_by_id(tenant_id, region, table_name)
    if not tenant_data:
        raise Exception("404::Tenant não encontrado")
        
    if tenant_data.get('status') == 'deleted':
        raise Exception("404::Tenant não encontrado")
        
    org_id = tenant_data.get('organizationId')
    
    # 2. Segurança: Apenas superadmin ou admin/membro da própria organização
    has_global_scope = isGlobalOrganizationRole(auth.get('role'))
    if not has_global_scope and org_id != auth['organizationId']:
        raise Exception("403::Acesso negado para esta organização")
        
    # 3. Soft delete do Tenant
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        table.update_item(
            Key={
                'pk': f"ORG#{org_id}#TENANTS",
                'sk': f"TENANT#{tenant_id}"
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
        raise Exception("404::Tenant não encontrado")

    # 4. Desabilitar todos os usuários do tenant no Cognito
    config = getSaaSConfig(region, stage)
    if config:
        cognito = boto3.client('cognito-idp', region_name=region)
        users = get_users_by_tenant(tenant_id, region, environ['DYNAMODB_TABLE_USERS'])
        for user in users:
            try:
                cognito.admin_disable_user(
                    UserPoolId=config['userPoolId'],
                    Username=user['userId']
                )
            except Exception as e:
                print(f"WARNING: Erro ao desabilitar usuário {user['userId']} no Cognito: {e}")
        
    return True
