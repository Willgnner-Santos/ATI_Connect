import json
import boto3
from os import environ
import plataforma_ati
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn
)

def handler(event, context):
    print("INFO: Iniciando exclusão de Tipo de Sensor SaaS...")
    try:
        # 1. Autenticação
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização (via RBAC dinâmico, mesmo padrão do resto do sistema)
        authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'catalog', 'write', environ['REGION'])

        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Type ID não informado")
        type_id = path_params['id']
        
        # 4. Execução
        result = process_request(type_id, environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Tipo de Sensor deletado com sucesso"
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

def process_request(type_id, region):
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    # NOTA DE SEGURANÇA: 
    # Num sistema em produção real, antes de excluir, o ideal seria buscar na tabela
    # de equipamentos se existe algum usando este type_id para evitar exclusão em cascata quebrada.
    # Como é um catálogo inicial, manteremos a deleção simples.
    
    try:
        table.delete_item(
            Key={
                'pk': 'SYSTEM#CATALOG',
                'sk': f"SENSORTYPE#{type_id}"
            },
            ConditionExpression="attribute_exists(pk)"
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        raise Exception("404::Tipo de Sensor não encontrado")

    # Invalida o cache em memória do catálogo (só afeta este container Lambda)
    plataforma_ati._SENSOR_CATALOG_CACHE = None

    return True
