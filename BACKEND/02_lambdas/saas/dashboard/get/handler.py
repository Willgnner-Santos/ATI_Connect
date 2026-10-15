import json
import boto3
from os import environ
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from plataforma_ati import (
    validateTokenSaaS, authorizeSaaS, lambdaReturn, resolveSaaSTargetScope,
    filterVariablesForRole, getAllDynamoItems
)

# Tempo sem telemetria a partir do qual o equipamento é considerado "stale"
# (offline/parado) em vez de só "sem leitura no período" — distingue ausência
# de dado real de ausência de dado por falta de atividade do device.
STALE_THRESHOLD_SECONDS = 600

def get_sts_credentials(region, role_arn, session_user_id):
    """Get temporary AWS credentials for MQTT access.

    RoleSessionName inclui o sub do usuário para rastreabilidade no CloudTrail
    (quem gerou qual credencial) — não restringe o escopo do acesso MQTT em si,
    que ainda depende da IAM policy da role assumida.
    """
    if not role_arn:
        print("WARNING: IOT_ASSUME_ROLE environment variable not set")
        return None

    try:
        print(f"INFO: Assuming role {role_arn}...")
        client = boto3.client('sts', region_name=region)
        session_name = f"dashboard-{session_user_id}"[:64]
        response = client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=3600
        )
        return {
            "accessKeyId": response['Credentials']['AccessKeyId'],
            "secretAccessKey": response['Credentials']['SecretAccessKey'],
            "sessionToken": response['Credentials']['SessionToken'],
            "expiration": response['Credentials']['Expiration'].isoformat()
        }
    except Exception as e:
        print(f"ERROR: Failed to assume IoT role: {str(e)}")
        return None

def get_equipment_by_scoping(auth, query_params, region, table_name):
    """Busca equipamentos respeitando a hierarquia multi-tenant e filtros.

    Usa resolveSaaSTargetScope para resolver organizationId/tenantId de forma
    centralizada — roles globais (superadmin/collaborator) são obrigadas a
    informar organizationId explicitamente, o que impede que um tenantId seja
    usado isoladamente para enumerar dados de qualquer tenant do sistema.
    """
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)

    tenant_id_filter = query_params.get('tenantId')
    org_id_filter = query_params.get('organizationId')

    target_scope = resolveSaaSTargetScope(auth, org_id_filter, tenant_id_filter, require_tenant=False)
    target_org = target_scope['organizationId']
    target_tenant = target_scope['tenantId']

    try:
        if target_tenant == 'none':
            # Todos os equipamentos da organização (todos os tenants), via GSI
            print(f"INFO: Listando equipamentos da Org {target_org}")
            return getAllDynamoItems(table,
                IndexName='organizationId-tenantId-index',
                KeyConditionExpression=boto3.dynamodb.conditions.Key('organizationId').eq(target_org),
                FilterExpression='#status <> :inactive',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':inactive': 'inactive'}
            )

        # Equipamentos de um tenant específico
        pk = f"TENANT#{target_tenant}#EQUIPMENT"
        print(f"INFO: Buscando equipamentos na PK {pk}")
        items = getAllDynamoItems(table,
            KeyConditionExpression='pk = :pk',
            FilterExpression='#status <> :inactive',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':pk': pk, ':inactive': 'inactive'}
        )

        # Cross-check final: garante que o tenant realmente pertence à organização
        # resolvida, mesmo se o tenantId informado for de outra organização
        items = [i for i in items if i.get('organizationId') == target_org]

        return items

    except Exception as e:
        print(f"ERROR: Falha ao buscar equipamentos no Dashboard: {e}")
        raise Exception(f"500::Erro ao buscar equipamentos: {str(e)}")

def get_telemetry_data(equipment_id, last_minutes, region, table_name):
    """Get recent telemetry data for equipment.

    Levanta exceção em falha real de infraestrutura — não retorna [] silenciosamente
    (isso mascararia um erro de permissão/throttling como "sem dados no período").
    Limita a página e sinaliza truncamento se houver mais dados do que o Limit.
    """
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)

    try:
        threshold_time = datetime.now(timezone.utc) - timedelta(minutes=last_minutes)
        threshold_timestamp = int(threshold_time.timestamp() * 1000)

        response = table.query(
            KeyConditionExpression='pk = :pk AND sk >= :timestamp',
            ExpressionAttributeValues={
                ':pk': f"EQUIPMENT#{equipment_id}",
                ':timestamp': threshold_timestamp
            },
            ScanIndexForward=False,
            Limit=5000
        )

        telemetry_items = response.get('Items', [])
        truncated = 'LastEvaluatedKey' in response
        telemetry_data = []
        for item in telemetry_items:
            timestamp_ms = int(item['sk'])
            data_point = {
                'timestamp': timestamp_ms,
                'datetime': datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                'data': item.get('data', {})
            }
            telemetry_data.append(data_point)

        return telemetry_data, truncated
    except Exception as e:
        raise Exception(f"500::Falha ao buscar telemetria para {equipment_id}: {str(e)}")

def build_stale_value_entry(equipment, variable):
    """Retorna o último valor conhecido de uma variável (fora do período consultado,
    vindo do lastValues/lastSeen mantidos pelo Router), marcado como stale=True.
    None se essa variável nunca foi reportada."""
    last_seen = equipment.get('lastSeen')
    last_values = equipment.get('lastValues') or {}
    if not last_seen or variable not in last_values:
        return None
    last_seen_dt = datetime.fromisoformat(last_seen)
    return {
        'timestamp': int(last_seen_dt.timestamp() * 1000),
        'datetime': last_seen_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'value': last_values[variable],
        'stale': True
    }

def map_sensor_values(sensors, telemetry_data):
    """Map sensor values from telemetry data"""
    # Se sensors for uma lista, convertemos temporariamente em dict para facilitar o mapeamento
    sensors_dict = sensors if isinstance(sensors, dict) else {s.get('variable'): s for s in sensors if s.get('variable')}
    
    for variable, sensor in sensors_dict.items():
        sensor['values'] = []
        for data_point in telemetry_data:
            value = data_point['data'].get(variable, 'N/A')
            sensor['values'].append({
                'timestamp': data_point['timestamp'],
                'datetime': data_point['datetime'],
                'value': value
            })

    return list(sensors_dict.values()) if isinstance(sensors, list) else sensors_dict

def build_variable_values(variables, telemetry, equipment):
    """Aplica a série de telemetria (ou o fallback de último valor conhecido) a um
    dict/lista de variáveis declaradas no equipamento — usado tanto para sensors
    quanto para states, já que ambos compartilham o mesmo formato e a mesma fonte
    de dados (o blob 'data' gravado pelo Router)."""
    if not variables:
        return variables
    if telemetry:
        return map_sensor_values(variables, telemetry)
    v_dict = variables if isinstance(variables, dict) else {v.get('variable'): v for v in variables if v.get('variable')}
    for variable, v in v_dict.items():
        fallback = build_stale_value_entry(equipment, variable)
        v['values'] = [fallback] if fallback else []
    return list(v_dict.values()) if isinstance(variables, list) else v_dict

def handler(event, context):
    print("INFO: Starting dashboard get...")
    try:
        # 1. Validate token
        if "Authorization" not in event['headers']:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(event['headers']['Authorization'], environ['REGION'])
        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Authorize
        auth = authorizeSaaS(
            token_data['data'],
            environ['DYNAMODB_TABLE_ROLES'],
            'dashboard',
            'read',
            environ['REGION']
        )

        # 3. Parse query parameters
        query_params = event.get('queryStringParameters') or {}

        # 4. Process Request
        result = process_request(auth, query_params, environ['REGION'], token_data['data'].get('sub', 'unknown'))

        return lambdaReturn({
            "statusCode": 200,
            "message": "Dashboard carregado com sucesso",
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

def process_request(auth, query_params, region, session_user_id):
    last_minutes = int(query_params.get('lastMinutes', 5))
    if last_minutes < 1: last_minutes = 1
    elif last_minutes > 1440: last_minutes = 1440

    equipment_id_filter = query_params.get('equipmentId')

    # 1. Get MQTT credentials
    credentials = get_sts_credentials(region, environ.get('IOT_ASSUME_ROLE'), session_user_id)

    # 2. Get equipment list based on scoping/filters
    equipment_list = get_equipment_by_scoping(auth, query_params, region, environ['DYNAMODB_TABLE_EQUIPMENT'])

    # Se passou equipmentId, filtra a lista para retornar apenas ele
    if equipment_id_filter:
        equipment_list = [e for e in equipment_list if e.get('equipmentId') == equipment_id_filter]

    equipment_list.sort(key=lambda x: x.get('name') or '')

    now_utc = datetime.now(timezone.utc)

    # 3. For each equipment, get telemetry and map sensor values
    dashboard_data = []
    telemetry_truncated = False
    for equipment in equipment_list:
        equipment_id = equipment.get('equipmentId')

        # Apenas busca a série de telemetria se o filtro de equipamento único for usado.
        # Caso contrário, retorna apenas a definição dos sensores vazia (sem as séries históricas).
        telemetry = []
        if equipment_id_filter:
            telemetry, truncated = get_telemetry_data(equipment_id, last_minutes, region, environ['DYNAMODB_TABLE_TELEMETRY'])
            telemetry_truncated = telemetry_truncated or truncated

        # Grants antes de montar as séries: o valor só é lido para as variáveis que a
        # role do tenant pode ver, então filtrar a declaração aqui já esconde o dado.
        visible_sensors = filterVariablesForRole(equipment.get('sensors', {}), auth)
        visible_states = filterVariablesForRole(equipment.get('states', {}), auth)

        # Sem leitura no período: cai pro último valor conhecido (lastValues),
        # marcado como stale=True, em vez de devolver um array vazio. Mesma lógica
        # pra sensors e states — ambos vêm do mesmo blob 'data' gravado pelo Router.
        sensors = build_variable_values(visible_sensors, telemetry, equipment)
        states = build_variable_values(visible_states, telemetry, equipment)
        commands = filterVariablesForRole(equipment.get('commands', {}), auth)

        last_seen = equipment.get('lastSeen')
        seconds_since_last_seen = None
        if last_seen:
            seconds_since_last_seen = (now_utc - datetime.fromisoformat(last_seen)).total_seconds()
        is_stale = seconds_since_last_seen is None or seconds_since_last_seen > STALE_THRESHOLD_SECONDS

        equipment_data = {
            'equipmentId': equipment_id,
            'name': equipment.get('name'),
            'type': equipment.get('type'),
            'model': equipment.get('model'),
            'status': equipment.get('status', 'active'),
            'thingId': equipment.get('thingId'),
            'endpoint': equipment.get('endpoint'),
            'topic': equipment.get('topic'),
            'sensors': list(sensors.values()) if isinstance(sensors, dict) else sensors,
            'states': list(states.values()) if isinstance(states, dict) else states,
            'commands': list(commands.values()) if isinstance(commands, dict) else (commands or []),
            'region': region,
            'lastSeen': last_seen,
            'secondsSinceLastSeen': seconds_since_last_seen,
            'isStale': is_stale
        }

        if credentials:
            equipment_data['credentials'] = credentials

        dashboard_data.append(equipment_data)

    now_utc = datetime.now(timezone.utc)
    return {
        "equipment": dashboard_data,
        "telemetryWindow": {
            "minutes": last_minutes,
            "from": (now_utc - timedelta(minutes=last_minutes)).isoformat(),
            "to": now_utc.isoformat(),
            "truncated": telemetry_truncated
        },
        "totalEquipment": len(dashboard_data)
    }
