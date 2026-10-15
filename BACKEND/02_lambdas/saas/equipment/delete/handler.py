import json
import boto3
import requests
from os import environ
from plataforma_ati import (
    validateTokenSaaS,
    authorizeSaaS,
    lambdaReturn,
    isGlobalOrganizationRole,
    getAllDynamoItems
)

def delete_fault_tree(equipment_id, org_id):
    """Remove a árvore de falhas do equipamento. Best-effort — não bloqueia a deleção."""
    url = environ.get('FAULT_TREE_SERVICE_URL', '').rstrip('/')
    api_key = environ.get('FAULT_TREE_INTERNAL_KEY', '')
    if not url or not api_key:
        return
    try:
        resp = requests.delete(
            f"{url}/fault-trees/equipment/{equipment_id}",
            headers={"X-Internal-Key": api_key},
            params={"org_id": org_id},
            timeout=5
        )
        if resp.status_code not in (204, 404):
            print(f"WARNING: Falha ao deletar árvore de falhas: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"WARNING: Erro ao chamar serviço de árvore de falhas: {e}")


def cleanup_iot_resources(thing_name, region):
    """Remove recursos associados no AWS IoT Core"""
    print(f"INFO: Limpando recursos IoT para {thing_name}...")
    iot = boto3.client('iot', region_name=region)
    
    try:
        # 1. Listar e desvincular certificados
        principals = iot.list_thing_principals(thingName=thing_name)
        for cert_arn in principals.get('principals', []):
            # Desvincular da Thing
            iot.detach_thing_principal(thingName=thing_name, principal=cert_arn)
            
            # Obter ID do certificado para inativar e deletar
            cert_id = cert_arn.split('/')[-1]
            
            # Desvincular políticas do certificado antes de deletar
            policies = iot.list_attached_policies(target=cert_arn)
            for policy in policies.get('policies', []):
                iot.detach_policy(policyName=policy['policyName'], target=cert_arn)
                
            # Inativar e Deletar Certificado
            iot.update_certificate(certificateId=cert_id, newStatus='INACTIVE')
            iot.delete_certificate(certificateId=cert_id, forceDelete=True)
            
        # 2. Deletar a Thing
        iot.delete_thing(thingName=thing_name)
        return True
    except Exception as e:
        print(f"WARNING: Falha parcial ao limpar recursos IoT: {e}")
        return False

def handler(event, context):
    print("INFO: Iniciando exclusão de Equipamento SaaS...")
    try:
        # 1. Autenticação
        auth_header = event['headers'].get('Authorization') or event['headers'].get('authorization')
        if not auth_header:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(auth_header, environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")
            
        # 2. Autorização
        auth = authorizeSaaS(token_data['data'], environ['DYNAMODB_TABLE_ROLES'], 'equipment', 'delete', environ['REGION'])
        
        # 3. Path Parameters
        path_params = event.get('pathParameters') or {}
        if 'id' not in path_params:
            raise Exception("400::Equipment ID não informado")
        equipment_id = path_params['id']
        
        # 4. Execução
        iot_cleanup_ok = process_request(auth, equipment_id, environ['REGION'])

        message = "Equipamento deletado com sucesso"
        if not iot_cleanup_ok:
            message = "Equipamento deletado, mas a limpeza de recursos IoT falhou parcialmente — verifique manualmente"

        return lambdaReturn({
            "statusCode": 200,
            "message": message
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
    table_name = environ['DYNAMODB_TABLE_EQUIPMENT']
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    # 1. Buscar o item para obter o thingId e garantir o Tenant correto
    tenant_id = auth['tenantId']
    has_global_scope = isGlobalOrganizationRole(auth.get('role'))
    target_tenant = tenant_id
    
    if has_global_scope or tenant_id == 'none':
        if has_global_scope:
            items = getAllDynamoItems(table, 'scan',
                FilterExpression=boto3.dynamodb.conditions.Attr('equipmentId').eq(equipment_id)
            )
            if not items:
                raise Exception("404::Equipamento não encontrado")
            equipment = items[0]
            target_tenant = equipment['tenantId']
        else:
            items = getAllDynamoItems(table,
                IndexName='organizationId-tenantId-index',
                KeyConditionExpression=boto3.dynamodb.conditions.Key('organizationId').eq(auth['organizationId']),
                FilterExpression=boto3.dynamodb.conditions.Attr('equipmentId').eq(equipment_id)
            )
            if not items:
                raise Exception("404::Equipamento não encontrado nesta organização")
            equipment = items[0]
            target_tenant = equipment['tenantId']
    else:
        response = table.get_item(Key={'pk': f"TENANT#{tenant_id}#EQUIPMENT", 'sk': f"EQUIPMENT#{equipment_id}"})
        if 'Item' not in response:
            raise Exception("404::Equipamento não encontrado neste tenant")
        equipment = response['Item']

    key = {'pk': f"TENANT#{target_tenant}#EQUIPMENT", 'sk': f"EQUIPMENT#{equipment_id}"}

    # 2. Marcar como deleting antes de tocar nos recursos externos.
    try:
        table.update_item(
            Key=key,
            UpdateExpression='SET #status = :deleting',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':deleting': 'deleting'},
            ConditionExpression="attribute_exists(pk)"
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        raise Exception("404::Equipamento não encontrado para exclusão")

    # 3. Limpar IoT Core antes de remover a fonte de verdade.
    thing_id = equipment.get('thingId') or equipment.get('thingName')
    if thing_id and not cleanup_iot_resources(thing_id, region):
        table.update_item(
            Key=key,
            UpdateExpression='SET #status = :error',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':error': 'delete_error'}
        )
        raise Exception("502::Falha ao limpar recursos IoT; equipamento mantido para nova tentativa")

    # 4. Limpar dependências secundárias e remover o registro.
    org_id = equipment.get('organizationId', '')
    delete_fault_tree(equipment_id, org_id)
    table.delete_item(Key=key, ConditionExpression="attribute_exists(pk)")

    return True
