import json
import boto3
from os import environ
import traceback
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn,
    isGlobalOrganizationRole,
    filterVariablesForRole,
    getAllDynamoItems
)

def handler(event, context):
    print("INFO: Iniciando listagem de Modelos de Equipamentos...")
    try:
        # 1. Autenticação
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização (Qualquer usuário da org com permissão de leitura de equipment pode ver)
        auth = authorizeSaaS(
            token_data['data'], 
            environ['DYNAMODB_TABLE_ROLES'], 
            'equipment', 
            'read', 
            environ['REGION']
        )
        
        # 3. Determinar organizationId alvo
        query = event.get('queryStringParameters') or {}
        target_org_id = query.get('organizationId')
        
        has_global_scope = isGlobalOrganizationRole(auth.get('role'))
        
        if not target_org_id:
            target_org_id = auth['organizationId']
            
        if not has_global_scope:
            if target_org_id != auth['organizationId']:
                raise Exception("403::Acesso negado para esta organização")
                
        # 4. Execução
        result = process_request(target_org_id, auth, environ['REGION'])

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

def process_request(organization_id, auth, region):
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    print(f"INFO: Buscando modelos de equipamentos para Org {organization_id}...")
    try:
        items = getAllDynamoItems(table,
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"ORG#{organization_id}") & 
                                   boto3.dynamodb.conditions.Key('sk').begins_with("EQUIPMENTMODEL#")
        )
        
        # Limpa pk e sk do retorno para ficar mais amigável ao frontend e aplica os
        # grants: o modelo declara os mesmos sensores/ações do equipamento, então sem
        # filtrar aqui a role do tenant descobriria pelo modelo o que o equipamento
        # esconde dela.
        cleaned_items = []
        for item in items:
            cleaned = dict(item)
            cleaned.pop('pk', None)
            cleaned.pop('sk', None)
            cleaned['sensors'] = filterVariablesForRole(cleaned.get('sensors', {}), auth)
            cleaned['states'] = filterVariablesForRole(cleaned.get('states', {}), auth)
            cleaned['commands'] = filterVariablesForRole(cleaned.get('commands') or {}, auth)
            cleaned_items.append(cleaned)

        return cleaned_items
    except Exception as e:
        print(f"ERROR: Falha ao buscar modelos de equipamentos: {e}")
        return []
