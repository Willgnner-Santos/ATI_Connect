import json
import boto3
from os import environ
from plataforma_ati import (
    lambdaReturn, authorizeSaaS, validateTokenSaaS, resolveSaaSTargetScope,
    isGlobalOrganizationRole, getAllDynamoItems
)

def list_tenants_by_org(organization_id, region, table_name, status_filter=None):
    """Lista todos os tenants de uma organização usando Query do Boto3"""
    print(f"INFO: Listando tenants da Org {organization_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        filter_expr = None
        if not status_filter:
            filter_expr = boto3.dynamodb.conditions.Attr('status').ne('deleted')
        elif status_filter == 'inactive':
            filter_expr = boto3.dynamodb.conditions.Attr('status').is_in(['inactive', 'deleted'])
        elif status_filter != 'all':
            filter_expr = boto3.dynamodb.conditions.Attr('status').eq(status_filter)

        query_kwargs = {
            'KeyConditionExpression': boto3.dynamodb.conditions.Key('pk').eq(f"ORG#{organization_id}#TENANTS")
        }
        if filter_expr is not None:
            query_kwargs['FilterExpression'] = filter_expr

        return getAllDynamoItems(table, **query_kwargs)
    except Exception as e:
        print(f"ERROR: Falha ao buscar tenants por org: {e}")
        return []

def handler(event, context):
    print("INFO: Iniciando consulta de tenants SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Verifica permissão 'tenant:read')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'tenant', 'read', environ['REGION'])
        
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
    status_filter = query_params.get('status')
    table_name = environ["DYNAMODB_TABLE_TENANTS"]

    # 1. Validação de Escopo (bloqueia fuga de organização e, quando aplicável, de tenant)
    target_scope = resolveSaaSTargetScope(auth, org_id_filter, tenant_id_filter, require_tenant=False)
    target_org = target_scope['organizationId']

    # 2. Execução
    tenants = list_tenants_by_org(target_org, region, table_name, status_filter)

    # 3. Se o chamador está restrito a um tenant específico (não é superadmin/collaborator),
    # garantir que só veja o seu próprio tenant.
    if not isGlobalOrganizationRole(auth.get('role')):
        caller_tenant = auth.get('tenantId')
        if caller_tenant and caller_tenant != 'none':
            tenants = [t for t in tenants if t.get('tenantId') == caller_tenant]

    return tenants
