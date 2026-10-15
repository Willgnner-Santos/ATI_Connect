import json
import boto3
from os import environ
from plataforma_ati import (
    lambdaReturn, authorizeSaaS, validateTokenSaaS, isGlobalOrganizationRole,
    validateSaaSRoleAssignment, getAllDynamoItems
)

def get_organization(org_id, region, table_name):
    """Obtém uma organização específica usando Boto3 get_item"""
    print(f"INFO: Buscando organização {org_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        response = table.get_item(
            Key={
                'pk': f"ORG#{org_id}",
                'sk': "METADATA"
            }
        )
        item = response.get('Item')
        if item and item.get('status') == 'deleted':
            return None
        return item
    except Exception as e:
        print(f"ERROR: Falha ao buscar organização: {e}")
        return None

def list_organizations(region, table_name, status_filter='active'):
    """Lista organizações pelo status usando o GSI status-index"""
    print(f"INFO: Listando organizações com status {status_filter}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)

    def _query_status(status_val):
        try:
            items = getAllDynamoItems(table,
                IndexName='status-index',
                KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq(status_val),
                FilterExpression=boto3.dynamodb.conditions.Attr('sk').eq('METADATA')
            )
            return [item for item in items if item.get('organizationId') != 'PLATFORM']
        except Exception as e:
            print(f"ERROR: Falha ao listar organizações (status: {status_val}): {e}")
            return []

    if status_filter == 'all':
        return _query_status('active') + _query_status('inactive') + _query_status('deleted')
    elif status_filter == 'inactive':
        return _query_status('inactive') + _query_status('deleted')
    else:
        return _query_status(status_filter)

def handler(event, context):
    print("INFO: Iniciando consulta de organizações SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (Verifica permissão 'organization:read')
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'organization', 'read', environ['REGION'])

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
    org_id_filter = query_params.get('id')
    status_filter = query_params.get('status', 'active')
    has_global_scope = isGlobalOrganizationRole(token_claims.get('organizationRole'))
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']

    # 1. Validação de Acesso Multi-tenant
    if org_id_filter:
        validateSaaSRoleAssignment(auth.get('role'), org_id_filter, 'viewer')
        if not has_global_scope and org_id_filter != auth['organizationId']:
            raise Exception("403::Acesso negado para esta organização")
        
        org_data = get_organization(org_id_filter, region, table_name)
        if not org_data:
            raise Exception("404::Organização não encontrada")
        return org_data
    else:
        # Listagem
        if has_global_scope:
            return list_organizations(region, table_name, status_filter)
        else:
            # Não-superadmins só podem ver sua própria organização na listagem
            org_data = get_organization(auth['organizationId'], region, table_name)
            if not org_data: return []
            
            # Aplica o filtro de status, se existir
            if status_filter and status_filter != 'all' and org_data.get('status') != status_filter:
                return []
            return [org_data]
