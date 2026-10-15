import json
from os import environ
import boto3
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn,
    isGlobalOrganizationRole,
    getSaaSRoleLevel,
    getAllDynamoItems
)

def get_users_by_org(organization_id, region, table_name):
    """Lista todos os usuários de uma organização usando Query do Boto3"""
    print(f"INFO: Listando usuários da Org {organization_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        return getAllDynamoItems(table,
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"ORG#{organization_id}#USER"),
            FilterExpression=boto3.dynamodb.conditions.Attr('status').ne('deleted') & boto3.dynamodb.conditions.Attr('tenantId').eq('none')
        )
    except Exception as e:
        print(f"ERROR: Falha ao buscar usuários por org: {e}")
        return []

def get_users_by_tenant(tenant_id, region, table_name):
    """Lista usuários de um tenant específico usando o GSI tenantId-index"""
    print(f"INFO: Listando usuários do Tenant {tenant_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        return getAllDynamoItems(table,
            IndexName='tenantId-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('tenantId').eq(tenant_id),
            FilterExpression=boto3.dynamodb.conditions.Attr('status').ne('deleted')
        )
    except Exception as e:
        print(f"ERROR: Falha ao buscar usuários por tenant: {e}")
        return []

def handler(event, context):
    print("INFO: Iniciando consulta de usuários SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização (Verifica permissão 'user:read')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'user', 'read', environ['REGION'])
        
        # 3. Parâmetros de Filtro
        query_params = event.get('queryStringParameters') or {}
        
        # 4. Execução
        result = process_request(auth, query_params, token_data['data'], environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Ok",
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

def process_request(auth, query_params, token_claims, region):
    org_id_filter = query_params.get('organizationId')
    tenant_id_filter = query_params.get('tenantId')

    caller_role = token_claims.get('organizationRole')
    has_global_scope = isGlobalOrganizationRole(caller_role)
    table_name = environ["DYNAMODB_TABLE_USERS"]

    if has_global_scope:
        # Superadmin/collaborator pode filtrar por qualquer org ou ver da própria
        target_org = org_id_filter or token_claims.get('organizationId')
        if tenant_id_filter:
            users = get_users_by_tenant(tenant_id_filter, region, table_name)
        else:
            users = get_users_by_org(target_org, region, table_name)
    else:
        # Admin de Org só vê a própria Org
        my_org = auth['organizationId']
        my_tenant = auth.get('tenantId')
        if tenant_id_filter:
            # Se o chamador está restrito a um tenant, só pode filtrar pelo próprio tenant
            if my_tenant and my_tenant != 'none' and tenant_id_filter != my_tenant:
                raise Exception("403::Acesso negado para este tenant")
            users = get_users_by_tenant(tenant_id_filter, region, table_name)
            # Filtro de segurança: garantir que os usuários retornados são da org do admin
            users = [u for u in users if u.get('organizationId') == my_org]
        else:
            users = get_users_by_org(my_org, region, table_name)

        # Filtro de hierarquia: não expor usuários com role acima do caller
        caller_level = getSaaSRoleLevel(caller_role)
        users = [u for u in users if getSaaSRoleLevel(u.get('role', '')) <= caller_level]

    return users
