import json
import boto3
from os import environ
import traceback
from plataforma_ati import (
    validateTokenSaaS, 
    authorizeSaaS, 
    lambdaReturn,
    getAllDynamoItems
)

def handler(event, context):
    print("INFO: Iniciando listagem do Catálogo de Sensores...")
    try:
        # 1. Autenticação
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização (Qualquer usuário logado com permissão catalog:read pode ver)
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'catalog', 'read', environ['REGION'])
        
        # 3. Execução
        result = process_request(environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Ok",
            "data": result
        })
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        traceback.print_exc()
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

def process_request(region):
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    # 1. Buscar Catálogo Global (só a faixa de sensores: tipos de ação dividem a
    # mesma partição SYSTEM#CATALOG e apareceriam aqui sem o filtro de prefixo)
    print("INFO: Buscando tipos de sensores globais...")
    try:
        return getAllDynamoItems(table,
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq('SYSTEM#CATALOG') &
                                   boto3.dynamodb.conditions.Key('sk').begins_with('SENSORTYPE#')
        )
    except Exception as e:
        print(f"ERROR: Falha ao buscar catálogo: {e}")
        return []
