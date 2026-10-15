import json
import boto3
import requests
from os import environ
from datetime import datetime
from uuid import uuid4
from re import sub
import base64
from pathlib import Path
from plataforma_ati import (
    lambdaReturn,
    authorizeSaaS,
    insertDynamoDB,
    validateTokenSaaS,
    validateInput,
    stripAccents,
    getSensorCatalog,
    resolveSaaSTargetScope,
    validateSensors,
    validateStates,
    validateCommands,
    getAllDynamoItems
)

AMAZON_ROOT_CA_1 = Path(__file__).with_name('AmazonRootCA1.pem').read_text(encoding='ascii')

def create_iot_resources(thing_name, region):
    """Cria Thing e Certificado no AWS IoT Core"""
    print(f"INFO: Criando recursos IoT para {thing_name}...")
    iot = boto3.client('iot', region_name=region)
    
    cert_arn = None
    cert_id = None
    thing_created = False
    try:
        # 1. Criar Certificado
        cert_res = iot.create_keys_and_certificate(setAsActive=True)
        cert_arn = cert_res['certificateArn']
        cert_id = cert_res['certificateId']
        
        # 2. Criar Thing
        iot.create_thing(thingName=thing_name)
        thing_created = True
        
        # 3. Vincular Certificado à Thing
        iot.attach_thing_principal(thingName=thing_name, principal=cert_arn)
        
        # 4. Vincular Política (usando a política padrão do sistema)
        policy_name = environ.get('IOT_POLICY', 'plataforma-ati-iot-policy')
        print(f"INFO: Vinculando política IoT '{policy_name}' ao certificado...")
        iot.attach_policy(policyName=policy_name, target=cert_arn)
        
        # 5. Obter Endpoint
        endpoint_res = iot.describe_endpoint(endpointType='iot:Data-ATS')
        
        return {
            "success": True,
            "thingName": thing_name,
            "certificateArn": cert_arn,
            "certificateId": cert_id,
            "certificatePem": cert_res['certificatePem'],
            "privateKey": cert_res['keyPair']['PrivateKey'],
            "endpoint": endpoint_res['endpointAddress']
        }
    except Exception as e:
        print(f"ERROR: Falha ao criar recursos IoT: {e}")
        try:
            if cert_arn and thing_created:
                iot.detach_thing_principal(thingName=thing_name, principal=cert_arn)
            if cert_arn:
                for policy in iot.list_attached_policies(target=cert_arn).get('policies', []):
                    iot.detach_policy(policyName=policy['policyName'], target=cert_arn)
            if cert_id:
                iot.update_certificate(certificateId=cert_id, newStatus='INACTIVE')
                iot.delete_certificate(certificateId=cert_id, forceDelete=True)
            if thing_created:
                iot.delete_thing(thingName=thing_name)
        except Exception as cleanup_error:
            print(f"ERROR: Falha ao compensar provisionamento IoT parcial: {cleanup_error}")
        return {"success": False, "error": str(e)}

def copy_fault_tree(model_id, equipment_id, tenant_id, org_id):
    """Copia a árvore de falhas do modelo para o equipamento recém-criado.
    Falha silenciosa — não bloqueia a criação do equipamento."""
    url = environ.get('FAULT_TREE_SERVICE_URL', '').rstrip('/')
    api_key = environ.get('FAULT_TREE_INTERNAL_KEY', '')
    if not url or not api_key:
        return
    try:
        resp = requests.post(
            f"{url}/fault-trees/model/{model_id}/copy",
            json={"equipment_id": equipment_id, "tenant_id": tenant_id, "org_id": org_id},
            headers={"X-Internal-Key": api_key},
            timeout=5
        )
        if resp.status_code not in (201, 404, 409):
            print(f"WARNING: Falha ao copiar árvore de falhas: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"WARNING: Erro ao chamar serviço de árvore de falhas: {e}")


def rollback_iot_resources(thing_name, cert_arn, region):
    """Desfaz os recursos IoT criados quando o registro no DynamoDB falha,
    evitando Thing/certificado órfãos na AWS."""
    print(f"WARNING: Fazendo rollback dos recursos IoT de {thing_name}...")
    iot = boto3.client('iot', region_name=region)
    try:
        cert_id = cert_arn.split('/')[-1]
        iot.detach_thing_principal(thingName=thing_name, principal=cert_arn)
        policies = iot.list_attached_policies(target=cert_arn)
        for policy in policies.get('policies', []):
            iot.detach_policy(policyName=policy['policyName'], target=cert_arn)
        iot.update_certificate(certificateId=cert_id, newStatus='INACTIVE')
        iot.delete_certificate(certificateId=cert_id, forceDelete=True)
        iot.delete_thing(thingName=thing_name)
        print(f"INFO: Rollback dos recursos IoT de {thing_name} concluído")
    except Exception as e:
        print(f"ERROR: Falha ao fazer rollback dos recursos IoT de {thing_name} — recursos podem ter ficado órfãos: {e}")

def handler(event, context):
    print("INFO: Iniciando cadastro de Equipamento SaaS...")
    try:
        # 1. Autenticação e Autorização de Token
        if "Authorization" not in event['headers']:
            raise Exception("401::Token não informado")
            
        token_data = validateTokenSaaS(event['headers']['Authorization'], environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Parse Body
        body_raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body_raw = base64.b64decode(body_raw).decode('utf-8')
        body = json.loads(body_raw)
        
        # 3. Validação de Input
        validate = validateInput([
            {'field': 'organizationId', 'type': 'str', 'mandatory': True},
            {'field': 'tenantId', 'type': 'str', 'mandatory': True},
            {'field': 'name', 'type': 'str', 'mandatory': True},
            {'field': 'equipmentType', 'type': 'str', 'mandatory': True},
            {'field': 'model', 'type': 'str', 'mandatory': True},
            {'field': 'modelId', 'type': 'str', 'mandatory': False, 'pattern': r'^[a-z0-9_]+$'},
            {'field': 'local', 'type': 'str', 'mandatory': True},
            {'field': 'sensors', 'type': 'dict', 'mandatory': True},
            {'field': 'states', 'type': 'dict', 'mandatory': False},
            {'field': 'commands', 'type': 'dict', 'mandatory': False}
        ], body)
        
        if not validate['success']:
            raise Exception(f"400::{validate['message']}")

        # 4. Autorização SaaS (RBAC)
        auth = authorizeSaaS(
            token_data['data'], 
            environ['DYNAMODB_TABLE_ROLES'], 
            'equipment',
            'write',
            environ['REGION']
        )
        
        # 5. Execução
        result = process_request(auth, body, token_data['data'], environ['REGION'])

        return lambdaReturn({
            "statusCode": 201,
            "message": "Equipamento cadastrado com sucesso",
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

def validate_tenant_belongs_to_org(tenant_id, org_id, region):
    """Confirma que o tenant informado realmente pertence à organização resolvida,
    evitando que um admin de organização grave um equipamento sob um tenant de
    outra organização."""
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_TENANTS'])
    response = table.get_item(Key={'pk': f"ORG#{org_id}#TENANTS", 'sk': f"TENANT#{tenant_id}"})
    if 'Item' not in response:
        raise Exception("403::Tenant não pertence à organização informada")

def process_request(auth, body, token_claims, region):
    # 1. Validação de Escopo (Segurança Multi-tenant) — resolve organizationId/tenantId
    # de forma centralizada, sem permitir que um admin de organização injete um
    # tenantId de outra organização.
    target_scope = resolveSaaSTargetScope(auth, body['organizationId'], body['tenantId'], require_tenant=True)
    org_id = target_scope['organizationId']
    tenant_id = target_scope['tenantId']
    validate_tenant_belongs_to_org(tenant_id, org_id, region)

    # 2. Validar modelId se fornecido
    model_id = body.get('modelId')
    if model_id:
        dynamodb = boto3.resource('dynamodb', region_name=region)
        org_table = dynamodb.Table(environ['DYNAMODB_TABLE_ORGANIZATIONS'])
        model_item = org_table.get_item(
            Key={'pk': f"ORG#{org_id}", 'sk': f"EQUIPMENTMODEL#{model_id}"}
        )
        if 'Item' not in model_item:
            raise Exception(f"400::Modelo de equipamento '{model_id}' não encontrado na organização.")

    # 3. Validação profunda dos sensores (usando Catálogo Global)
    sensors = body['sensors']
    if sensors:
        validateSensors(sensors, getSensorCatalog(region, environ['DYNAMODB_TABLE_ORGANIZATIONS']))

    # 2.1 Validação de Estados (interpretação livre, sem catálogo — só tipo primitivo)
    states = body.get('states') or {}
    validateStates(states)

    commands = body.get('commands') or {}
    validateCommands(commands)

    # 4. Verificar nome duplicado dentro do tenant
    dynamodb = boto3.resource('dynamodb', region_name=region)
    eq_table = dynamodb.Table(environ['DYNAMODB_TABLE_EQUIPMENT'])
    existing = getAllDynamoItems(eq_table,
        KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(f"TENANT#{tenant_id}#EQUIPMENT")
    )
    for item in existing:
        if item.get('name', '').lower() == body['name'].lower():
            raise Exception(f"400::Já existe um equipamento cadastrado com o nome '{body['name']}' neste tenant.")

    # 5. Gerar Identidade do Equipamento
    equipment_id = str(uuid4())
    clean_model = sub(r'[\s]', '_', stripAccents(body['model']).lower())
    thing_name = f"ati-{clean_model}-{equipment_id[:8]}"
    
    # 6. Criar Recursos IoT Core
    iot_resources = create_iot_resources(thing_name, region)
    if not iot_resources['success']:
        raise Exception(f"500::Erro ao configurar IoT Core: {iot_resources['error']}")

    # 7. Gravar no DynamoDB
    now = datetime.now().isoformat()
    item = {
        "pk": f"TENANT#{tenant_id}#EQUIPMENT",
        "sk": f"EQUIPMENT#{equipment_id}",
        "equipmentId": equipment_id,
        "organizationId": org_id,
        "tenantId": tenant_id,
        "name": body['name'],
        "type": body['equipmentType'],
        "model": body['model'],
        "local": body['local'],
        "sensors": body['sensors'],
        "states": states,
        "commands": commands,
        # --- FIX: Nomes de campos compatíveis com Dashboard ---
        "thingId": thing_name,
        "endpoint": iot_resources['endpoint'],
        "topic": f"telemetry/{thing_name}",
        # ------------------------------------------------------
        "status": "active",
        "createdAt": now,
        "lastSeen": None,
        "iotMetadata": {
            "certificateArn": iot_resources['certificateArn'],
            "certificateId": iot_resources['certificateId']
        }
    }
    if model_id:
        item["modelId"] = model_id

    db_res = insertDynamoDB(environ['DYNAMODB_TABLE_EQUIPMENT'], item, False, region)
    if not db_res['success']:
        rollback_iot_resources(thing_name, iot_resources['certificateArn'], region)
        raise Exception(f"500::Erro ao salvar no banco: {db_res['message']}")

    # 8. Copiar árvore de falhas do modelo (best-effort, não bloqueia)
    if model_id:
        copy_fault_tree(model_id, equipment_id, tenant_id, org_id)

    return {
        "equipmentId": equipment_id,
        "thingName": thing_name,
        "iot": {
            "endpoint": iot_resources['endpoint'],
            "certificatePem": iot_resources['certificatePem'],
            "privateKey": iot_resources['privateKey'],
            "amazonRootCA1": AMAZON_ROOT_CA_1
        }
    }
