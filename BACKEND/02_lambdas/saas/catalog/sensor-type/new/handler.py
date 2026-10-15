import json
import boto3
from os import environ
from datetime import datetime
import base64
import plataforma_ati
from plataforma_ati import (
    validateInput,
    lambdaReturn,
    authorizeSaaS,
    insertDynamoDB,
    validateTokenSaaS
)

def handler(event, context):
    print("INFO: Iniciando criação de Tipo de Sensor SaaS...")
    try:
        # 1. Validação de Token
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Autorização (via RBAC dinâmico, mesmo padrão do resto do sistema)
        authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'catalog', 'write', environ['REGION'])

        # 3. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)

        # 4. Validação de Input
        validate = validateInput([
            {'field': 'typeId', 'type': 'str', 'mandatory': True, 'pattern': r'^[a-z0-9_]+$'},
            {'field': 'name', 'type': 'str', 'mandatory': True},
            {'field': 'category', 'type': 'str', 'mandatory': True, 'pattern': r'^(analog|digital|relay|string)$'},
            {'field': 'defaultUnit', 'type': 'str', 'mandatory': False},
            {'field': 'description', 'type': 'str', 'mandatory': False}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")
            
        # 5. Execução
        result = process_request(body, environ['REGION'])

        return lambdaReturn({
            "statusCode": 201,
            "message": "Tipo de Sensor cadastrado com sucesso",
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

def process_request(body, region):
    type_id = body['typeId']
    now = datetime.now().isoformat()
    
    # 1. Checar se já existe
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    response = table.get_item(
        Key={
            'pk': 'SYSTEM#CATALOG',
            'sk': f"SENSORTYPE#{type_id}"
        }
    )
    if 'Item' in response:
         raise Exception(f"400::Já existe um tipo de sensor cadastrado com o ID '{type_id}'")

    # 2. Gravar Tipo de Sensor
    item = {
        "pk": "SYSTEM#CATALOG",
        "sk": f"SENSORTYPE#{type_id}",
        "typeId": type_id,
        "name": body['name'],
        "category": body['category'],
        "createdAt": now
    }
    
    if 'defaultUnit' in body:
        item['defaultUnit'] = body['defaultUnit']
    if 'description' in body:
        item['description'] = body['description']
    
    db_res = insertDynamoDB(table_name, item, False, region)
    if not db_res['success']:
        raise Exception(f"500::Erro ao salvar tipo de sensor no banco: {db_res['message']}")

    # Invalida o cache em memória do catálogo (só afeta este container Lambda;
    # containers já "quentes" seguem servindo o catálogo antigo até um cold start)
    plataforma_ati._SENSOR_CATALOG_CACHE = None

    return {
        "typeId": type_id
    }
