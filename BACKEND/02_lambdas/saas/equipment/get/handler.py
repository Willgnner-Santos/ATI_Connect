import json
import boto3
from os import environ
from plataforma_ati import (
    lambdaReturn,
    authorizeSaaS,
    validateTokenSaaS,
    isGlobalOrganizationRole,
    filterVariablesForRole,
    filterValuesForRole,
    getAllDynamoItems
)

def apply_grants(equipments, auth):
    """Aplica os grants de sensor/estado/command da role do tenant a cada item da lista."""
    for equipment in equipments:
        sensors = equipment.get('sensors', {})
        states = equipment.get('states', {})
        if 'lastValues' in equipment:
            equipment['lastValues'] = filterValuesForRole(equipment['lastValues'], sensors, states, auth)
        equipment['sensors'] = filterVariablesForRole(sensors, auth)
        equipment['states'] = filterVariablesForRole(states, auth)
        equipment['commands'] = filterVariablesForRole(equipment.get('commands') or {}, auth)
    return equipments

def list_equipment_by_tenant(tenant_id, region, table_name):
    """Lista equipamentos de um tenant específico usando a PK direta"""
    print(f"INFO: Listando equipamentos do Tenant {tenant_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        return getAllDynamoItems(table,
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"TENANT#{tenant_id}#EQUIPMENT")
        )
    except Exception as e:
        print(f"ERROR: Falha ao buscar equipamentos por tenant: {e}")
        return []

def list_equipment_by_org(organization_id, region, table_name):
    """Lista equipamentos de uma organização usando o GSI organizationId-tenantId-index"""
    print(f"INFO: Listando equipamentos da Org {organization_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        return getAllDynamoItems(table,
            IndexName='organizationId-tenantId-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('organizationId').eq(organization_id)
        )
    except Exception as e:
        print(f"ERROR: Falha ao buscar equipamentos por org: {e}")
        return []

def handler(event, context):
    print("INFO: Iniciando listagem de Equipamentos SaaS...")
    try:
        # 1. Autenticação
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'equipment', 'read', environ['REGION'])
        
        # 3. Parâmetros de Filtro
        query_params = event.get('queryStringParameters') or {}
        
        # 4. Execução
        result = apply_grants(
            process_request(auth, query_params, token_data['data'], environ['REGION']),
            auth
        )

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
    table_name = environ['DYNAMODB_TABLE_EQUIPMENT']
    
    has_global_scope = isGlobalOrganizationRole(token_claims.get('organizationRole'))
    
    if has_global_scope:
        # Superadmin decide o escopo
        target_org = org_id_filter or token_claims.get('organizationId')
        if tenant_id_filter:
            return list_equipment_by_tenant(tenant_id_filter, region, table_name)
        else:
            return list_equipment_by_org(target_org, region, table_name)
    else:
        # Usuário comum
        my_org = auth['organizationId']
        my_tenant = auth['tenantId']
        
        # Se filtrou por org que não é a dele, bloqueia
        if org_id_filter and org_id_filter != my_org:
            raise Exception("403::Acesso negado para esta organização")
            
        # Se o usuário tem tenant fixo, ele SÓ vê o dele
        if my_tenant != 'none':
            return list_equipment_by_tenant(my_tenant, region, table_name)
            
        # Se o usuário é admin da Org (tenant='none'), ele pode filtrar por tenant da org
        if tenant_id_filter:
            # Segurança: confirmar se o tenant pertence à org
            equipments = list_equipment_by_tenant(tenant_id_filter, region, table_name)
            return [e for e in equipments if e.get('organizationId') == my_org]
        else:
            return list_equipment_by_org(my_org, region, table_name)
