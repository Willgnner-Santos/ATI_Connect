import json
from os import environ
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn,
    findSaaSEquipment,
    filterVariablesForRole
)

def handler(event, context):
    print("INFO: Iniciando listagem de Comandos do Equipamento...")
    try:
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'action', 'read', environ['REGION'])

        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Equipment ID não informado")
        equipment_id = path_params['id']

        result = process_request(auth, equipment_id, environ['REGION'])

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

def process_request(auth, equipment_id, region):
    equipment = findSaaSEquipment(environ['DYNAMODB_TABLE_EQUIPMENT'], equipment_id, auth, region)

    commands = filterVariablesForRole(equipment.get('commands', {}), auth)

    return {
        "equipmentId": equipment.get('equipmentId'),
        "tenantId": equipment.get('tenantId'),
        "organizationId": equipment.get('organizationId'),
        "name": equipment.get('name'),
        "status": equipment.get('status', 'active'),
        "commands": list(commands.values()) if isinstance(commands, dict) else (commands or [])
    }
