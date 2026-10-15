import json
import boto3
from os import environ
from plataforma_ati import (
    validateTokenSaaS, 
    authorizeSaaS,
    lambdaReturn,
    isGlobalOrganizationRole
)

def handler(event, context):
    print("INFO: Iniciando exclusão de Modelo de Equipamento SaaS...")
    try:
        # 1. Autenticação
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
        
        # 4. Execução
        result = process_request(auth, model_id, query, environ['REGION'])

        return lambdaReturn({
            "statusCode": 200,
            "message": "Modelo de equipamento deletado com sucesso"
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

def process_request(auth, model_id, query, region):
    table_name = environ['DYNAMODB_TABLE_ORGANIZATIONS']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    tenant_org_id = auth['organizationId']
    has_global_scope = isGlobalOrganizationRole(auth.get('role'))
    target_org_id = query.get('organizationId')
    
    if has_global_scope:
        if not target_org_id:
            raise Exception("400::Superadmin deve informar organizationId via query parameter para deletar modelos de equipamentos")
    else:
        # Usuário comum: sempre usa a org do token e ignora/bloqueia se tentar outra org
        if target_org_id and target_org_id != tenant_org_id:
            raise Exception("403::Acesso negado para esta organização")
        target_org_id = tenant_org_id
        
    try:
        table.delete_item(
            Key={
                'pk': f"ORG#{target_org_id}",
                'sk': f"EQUIPMENTMODEL#{model_id}"
            },
            ConditionExpression="attribute_exists(pk)"
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        raise Exception("404::Modelo de equipamento não encontrado nesta organização")

    return True
