import json
from os import environ
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn,
    findSaaSEquipment,
    filterVariablesForRole,
    filterValuesForRole
)

def handler(event, context):
    print("INFO: Iniciando visualização de detalhes de Equipamento SaaS...")
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
        
        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Equipment ID não informado")
        equipment_id = path_params['id']
        
        # 4. Execução
        result = process_request(auth, equipment_id, token_data['data'], environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Equipamento encontrado",
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

def process_request(auth, equipment_id, token_claims, region):
    # 1. Localização respeitando o escopo do chamador (tenant / org / global)
    equipment = findSaaSEquipment(environ['DYNAMODB_TABLE_EQUIPMENT'], equipment_id, auth, region)

    # 2. Grants: a role do tenant só vê os sensores/estados/commands liberados.
    sensors = filterVariablesForRole(equipment.get('sensors', {}), auth)
    states = filterVariablesForRole(equipment.get('states', {}), auth)
    last_values = filterValuesForRole(
        equipment.get('lastValues', {}),
        equipment.get('sensors', {}),
        equipment.get('states', {}),
        auth
    )

    # 3. Sanitização/Formatação
    return {
        "equipmentId": equipment.get('equipmentId'),
        "tenantId": equipment.get('tenantId'),
        "organizationId": equipment.get('organizationId'),
        "type": equipment.get('type'),
        "name": equipment.get('name'),
        "model": equipment.get('model'),
        "local": equipment.get('local'),
        "sensors": sensors,
        "states": states,
        "commands": filterVariablesForRole(equipment.get('commands') or {}, auth),
        "thingId": equipment.get('thingId'),
        "endpoint": equipment.get('endpoint'),
        "topic": equipment.get('topic'),
        "status": equipment.get('status', 'active'),
        # Mantidos pelo Router a cada telemetria recebida — sem eles, "device offline"
        # e "sem leitura no período" ficam indistinguíveis para quem consome o detalhe.
        "lastSeen": equipment.get('lastSeen'),
        "lastValues": last_values,
        "createdAt": equipment.get('createdAt'),
        "updatedAt": equipment.get('updatedAt')
    }
