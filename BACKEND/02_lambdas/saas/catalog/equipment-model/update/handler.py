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
    print("INFO: Iniciando atualização de Modelo de Equipamento SaaS...")
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
            
        # 3. Path Parameters & Query Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Model ID não informado")
        model_id = path_params['id']
        
        query = event.get('queryStringParameters') or {}

        # 4. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 5. Validação de Input
        validate = validateInput([
            {'field': 'name', 'type': 'str', 'mandatory': False},
            {'field': 'equipmentType', 'type': 'str', 'mandatory': False},
            {'field': 'sensors', 'type': 'dict', 'mandatory': False},
            {'field': 'states', 'type': 'dict', 'mandatory': False},
            {'field': 'commands', 'type': 'dict', 'mandatory': False},
            {'field': 'organizationId', 'type': 'str', 'mandatory': False}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")
            
        # 6. Execução
        result = process_request(auth, model_id, body, query, environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Modelo de equipamento atualizado com sucesso",
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

def process_request(auth, model_id, body, query, region):
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    tenant_org_id = auth['organizationId']
    has_global_scope = isGlobalOrganizationRole(auth.get('role'))
    
    # Determinar a organização alvo (query string, body ou token)
    target_org_id = query.get('organizationId') or body.get('organizationId')
    
    if has_global_scope:
        if not target_org_id:
            raise Exception("400::Superadmin deve informar organizationId via query parameter ou corpo da requisição para atualizar modelos de equipamentos")
    else:
        # Usuário comum: sempre usa a org do token e ignora/bloqueia se tentar outra org
        if target_org_id and target_org_id != tenant_org_id:
            raise Exception("403::Acesso negado para esta organização")
        target_org_id = tenant_org_id

    # 1. Obter o item existente
    existing_res = table.get_item(
        Key={
            'pk': f"ORG#{target_org_id}",
            'sk': f"EQUIPMENTMODEL#{model_id}"
        }
    )
    if 'Item' not in existing_res:
        raise Exception("404::Modelo de equipamento não encontrado nesta organização.")
        
    existing_item = existing_res['Item']

    # 2. Validação profunda dos sensores se fornecidos
    sensors = body.get('sensors')
    if sensors is not None:
        validateSensors(sensors, getSensorCatalog(region, environ['DYNAMODB_TABLE_ORGANIZATIONS']))
    else:
        sensors = existing_item.get('sensors')

    # 2.1 Validação de Estados (se enviados) — interpretação livre, sem catálogo
    states = body.get('states')
    if states is not None:
        validateStates(states)
    else:
        states = existing_item.get('states')

    commands = body.get('commands')
    if commands is not None:
        validateCommands(commands)
    else:
        commands = existing_item.get('commands') or {}

    # 3. Validação de Unicidade do Nome
    new_name = body.get('name')
    if new_name and new_name.lower() != existing_item.get('name', '').lower():
        existing_models = getAllDynamoItems(table,
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"ORG#{target_org_id}") & 
                                   boto3.dynamodb.conditions.Key('sk').begins_with("EQUIPMENTMODEL#")
        )
        for existing_item_in_org in existing_models:
            if existing_item_in_org.get('modelId') != model_id and existing_item_in_org.get('name', '').lower() == new_name.lower():
                raise Exception(f"400::Já existe um modelo de equipamento cadastrado com o nome '{new_name}' nesta organização.")
    else:
        new_name = existing_item.get('name')

    # 4. Mesclar e Salvar
    now = datetime.now().isoformat()
    updated_item = {
        "pk": f"ORG#{target_org_id}",
        "sk": f"EQUIPMENTMODEL#{model_id}",
        "modelId": model_id,
        "organizationId": target_org_id,
        "name": new_name,
        "equipmentType": body.get('equipmentType') or existing_item.get('equipmentType'),
        "sensors": sensors,
        "states": states,
        "commands": commands,
        "createdAt": existing_item.get('createdAt'),
        "createdBy": existing_item.get('createdBy'),
        "updatedAt": now
    }
    
    db_res = insertDynamoDB(table_name, updated_item, True, region)
    if not db_res['success']:
        raise Exception(f"500::Erro ao salvar modelo de equipamento no banco: {db_res['message']}")
        
    return {
        "modelId": model_id
    }
