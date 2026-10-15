import json
import boto3
from os import environ
from datetime import datetime
import base64
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn,
    validateInput,
    getSensorCatalog,
    findSaaSEquipment,
    validateSensors,
    validateStates,
    validateCommands,
    getAllDynamoItems
)

def handler(event, context):
    print("INFO: Iniciando atualização de Equipamento SaaS...")
    try:
        # 1. Autenticação
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'equipment', 'update', environ['REGION'])
        
        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Equipment ID não informado")
        equipment_id = path_params['id']
        
        # 4. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # Alterações de schema/grants exigem permissão administrativa. O técnico
        # mantém equipment:update para campos operacionais já suportados pelo front.
        if any(field in body for field in ('sensors', 'states', 'commands')):
            authorizeSaaS(
                token_data['data'], environ['DYNAMODB_TABLE_ROLES'],
                'equipment', 'configure', environ['REGION']
            )
        
        # 5. Validação de Input
        validate = validateInput([
            {'field': 'name', 'type': 'str', 'mandatory': False},
            {'field': 'model', 'type': 'str', 'mandatory': False},
            {'field': 'equipmentType', 'type': 'str', 'mandatory': False},
            {'field': 'local', 'type': 'str', 'mandatory': False},
            {'field': 'sensors', 'type': 'dict', 'mandatory': False},
            {'field': 'states', 'type': 'dict', 'mandatory': False},
            {'field': 'commands', 'type': 'dict', 'mandatory': False},
            {'field': 'status', 'type': 'str', 'mandatory': False, 'pattern': r'^(active|inactive)$'}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")
            
        # 6. Execução
        result = process_request(auth, equipment_id, body, environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Equipamento atualizado com sucesso",
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

def process_request(auth, equipment_id, body, region):
    table_name = environ['DYNAMODB_TABLE_EQUIPMENT']
    
    # 1. Localizar o item primeiro para garantir a PK correta (Tenant)
    equipment = findSaaSEquipment(table_name, equipment_id, auth, region)
    target_tenant = equipment['tenantId']

    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)

    # 2. Verificar nome duplicado dentro do tenant (somente se nome mudou)
    new_name = body.get('name')
    if new_name and new_name.lower() != equipment.get('name', '').lower():
        existing = getAllDynamoItems(table,
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"TENANT#{target_tenant}#EQUIPMENT")
        )
        for item in existing:
            if item.get('equipmentId') != equipment_id and item.get('name', '').lower() == new_name.lower():
                raise Exception(f"400::Já existe um equipamento cadastrado com o nome '{new_name}' neste tenant.")

    # 3. Filtrar campos permitidos ('equipmentType' no body é gravado como 'type',
    # mesmo nome de campo interno usado por equipment/new)
    if 'equipmentType' in body:
        body['type'] = body['equipmentType']

    allowed_fields = ['name', 'model', 'sensors', 'states', 'commands', 'status', 'local', 'type']
    update_data = {k: v for k, v in body.items() if k in allowed_fields}

    if not update_data:
        raise Exception("400::Nenhum campo válido para atualização")

    # 2.1 'sensors'/'states' vazio apagaria toda a configuração sem aviso — rejeitar
    if 'sensors' in update_data and not update_data['sensors']:
        raise Exception("400::Sensors não pode ser um objeto vazio")
    if 'states' in update_data and not update_data['states']:
        raise Exception("400::States não pode ser um objeto vazio")

    # 3. Validação de Sensores (se enviados) usando Catálogo Global
    if 'sensors' in update_data:
        validateSensors(update_data['sensors'], getSensorCatalog(region, environ['DYNAMODB_TABLE_ORGANIZATIONS']))

    # 3.1 Validação de Estados (se enviados) — interpretação livre, sem catálogo
    if 'states' in update_data:
        validateStates(update_data['states'])

    if 'commands' in update_data:
        validateCommands(update_data['commands'])

    # 4. Atualizar no DynamoDB
    update_expr = "SET " + ", ".join([f"#{k} = :{k}" for k in update_data.keys()])
    update_expr += ", #updatedAt = :updatedAt"
    
    expr_attr_names = {f"#{k}": k for k in update_data.keys()}
    expr_attr_names['#updatedAt'] = 'updatedAt'
    
    expr_attr_values = {f":{k}": v for k, v in update_data.items()}
    expr_attr_values[':updatedAt'] = datetime.now().isoformat()

    try:
        table.update_item(
            Key={
                'pk': f"TENANT#{target_tenant}#EQUIPMENT",
                'sk': f"EQUIPMENT#{equipment_id}"
            },
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ConditionExpression="attribute_exists(pk)"
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        raise Exception("404::Equipamento não encontrado para atualização")
        
    return {"equipmentId": equipment_id}
