import json
import boto3
from os import environ
from datetime import datetime
import base64
from plataforma_ati import (
    validateInput,
    lambdaReturn,
    authorizeSaaS,
    insertDynamoDB,
    validateTokenSaaS,
    getSensorCatalog,
    isGlobalOrganizationRole,
    validateSensors,
    validateStates,
    validateCommands,
    getAllDynamoItems
)

def handler(event, context):
    print("INFO: Iniciando criação de Modelo de Equipamento SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização: Requer permissão de escrita de equipment na organização
        auth = authorizeSaaS(
            token_data['data'], 
            environ['DYNAMODB_TABLE_ROLES'], 
            'equipment', 
            'write', 
            environ['REGION']
        )
            
        # 3. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 4. Validação de Input
        validate = validateInput([
            {'field': 'modelId', 'type': 'str', 'mandatory': True, 'pattern': r'^[a-z0-9_]+$'},
            {'field': 'name', 'type': 'str', 'mandatory': True},
            {'field': 'equipmentType', 'type': 'str', 'mandatory': True},
            {'field': 'sensors', 'type': 'dict', 'mandatory': True},
            {'field': 'states', 'type': 'dict', 'mandatory': False},
            {'field': 'commands', 'type': 'dict', 'mandatory': False},
            {'field': 'organizationId', 'type': 'str', 'mandatory': False}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")
            
        # 5. Execução
        result = process_request(auth, body, token_data['data']['sub'], environ['REGION'])

        return lambdaReturn({
            "statusCode": 201,
            "message": "Modelo de equipamento cadastrado com sucesso",
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

def process_request(auth, body, user_sub, region):
    has_global_scope = isGlobalOrganizationRole(auth.get('role'))
    target_org_id = body.get('organizationId')
    
    if not target_org_id:
        target_org_id = auth['organizationId']
        
    if not has_global_scope:
        if target_org_id != auth['organizationId']:
            raise Exception("403::Acesso negado para esta organização")

    model_id = body['modelId']
    now = datetime.now().isoformat()
    
    # 1. Validação profunda dos sensores (usando Catálogo Global de Sensores)
    sensors = body['sensors']
    if sensors:
        validateSensors(sensors, getSensorCatalog(region, environ['DYNAMODB_TABLE_ORGANIZATIONS']))

    # 1.1 Validação de Estados (interpretação livre, sem catálogo)
    states = body.get('states') or {}
    validateStates(states)

    commands = body.get('commands') or {}
    validateCommands(commands)

    # 2. Checar se já existe o modelo nesta organização (ID e Nome)
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    # 2.1 Validação de ID
    response = table.get_item(
        Key={
            'pk': f"ORG#{target_org_id}",
            'sk': f"EQUIPMENTMODEL#{model_id}"
        }
    )
    if 'Item' in response:
         raise Exception(f"400::Já existe um modelo de equipamento cadastrado com o ID '{model_id}' nesta organização.")

    # 2.2 Validação de Nome Único
    existing_models = getAllDynamoItems(table,
        KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"ORG#{target_org_id}") & 
                               boto3.dynamodb.conditions.Key('sk').begins_with("EQUIPMENTMODEL#")
    )
    for existing_item in existing_models:
        if existing_item.get('name', '').lower() == body['name'].lower():
            raise Exception(f"400::Já existe um modelo de equipamento cadastrado com o nome '{body['name']}' nesta organização.")

    # 3. Gravar Modelo de Equipamento
    item = {
        "pk": f"ORG#{target_org_id}",
        "sk": f"EQUIPMENTMODEL#{model_id}",
        "modelId": model_id,
        "organizationId": target_org_id,
        "name": body['name'],
        "equipmentType": body['equipmentType'],
        "sensors": sensors,
        "states": states,
        "commands": commands,
        "createdAt": now,
        "createdBy": user_sub
    }
    
    db_res = insertDynamoDB(table_name, item, False, region)
    if not db_res['success']:
        raise Exception(f"500::Erro ao salvar modelo de equipamento no banco: {db_res['message']}")
        
    return {
        "modelId": model_id
    }
