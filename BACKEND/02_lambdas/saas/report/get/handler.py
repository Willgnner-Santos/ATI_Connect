import json
import boto3
from os import environ
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from plataforma_ati import (
    validateTokenSaaS, authorizeSaaS, lambdaReturn, resolveSaaSTargetScope,
    filterVariablesForRole, getAllDynamoItems
)

def parse_datetime(date_str):
    """Parse ISO datetime string to timestamp in milliseconds"""
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)
    except Exception as e:
        raise Exception(f"400::Formato de data inválido. Use ISO 8601 (ex: 2026-05-21T10:00:00Z)")

def get_tenant_equipment(tenant_id, equipment_id, region):
    """Get equipment for report"""
    print(f"INFO: Getting equipment for tenant {tenant_id}...")
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_EQUIPMENT'])

    try:
        if equipment_id:
            # Get specific equipment
            response = table.get_item(
                Key={
                    'pk': f"TENANT#{tenant_id}#EQUIPMENT",
                    'sk': f"EQUIPMENT#{equipment_id}"
                }
            )

            if 'Item' not in response:
                raise Exception("404::Equipamento não encontrado")

            equipment_list = [response['Item']]
        else:
            # Get all active equipment
            equipment_list = getAllDynamoItems(table,
                KeyConditionExpression='pk = :pk',
                FilterExpression='#status <> :inactive',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':pk': f"TENANT#{tenant_id}#EQUIPMENT",
                    ':inactive': 'inactive'
                }
            )

        # Sort by name
        equipment_list.sort(key=lambda x: x.get('name') or '')
        return equipment_list

    except Exception as e:
        if "404::" in str(e):
            raise
        raise Exception(f"500::Erro ao buscar equipamentos: {str(e)}")

def get_telemetry_range(equipment_id, start_timestamp, end_timestamp, region):
    """Get telemetry data for time range.

    Levanta exceção em falha real de infraestrutura — não retorna [] silenciosamente.
    Sinaliza truncamento se houver mais dados do que o Limit permite retornar.
    """
    print(f"INFO: Getting telemetry for {equipment_id} from {start_timestamp} to {end_timestamp}...")

    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(environ['DYNAMODB_TABLE_TELEMETRY'])

    try:
        response = table.query(
            KeyConditionExpression='pk = :pk AND sk BETWEEN :start AND :end',
            ExpressionAttributeValues={
                ':pk': f"EQUIPMENT#{equipment_id}",
                ':start': start_timestamp,
                ':end': end_timestamp
            },
            ScanIndexForward=False,  # Most recent first
            Limit=10000  # Limit to prevent memory issues
        )

        telemetry_items = response.get('Items', [])
        truncated = 'LastEvaluatedKey' in response

        # Format telemetry data
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

def filter_variables(sensors, variable_filter):
    """Filter sensors by variable names. Aceita sensors como dict ou list."""
    if not variable_filter:
        return sensors

    variables_list = [v.strip() for v in variable_filter.split(',')]

    if isinstance(sensors, dict):
        return {k: v for k, v in sensors.items() if k in variables_list}

    return [s for s in sensors if s.get('variable') in variables_list]

def map_sensor_values(sensors, telemetry_data):
    """Map sensor values from telemetry data. Aceita sensors como dict ou list."""
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

def build_report_variable_values(variables, telemetry_data):
    """Aplica a série de telemetria a um dict/lista de variáveis (sensors ou states —
    ambos compartilham o mesmo formato). Sem fallback de último valor conhecido aqui
    (diferente do dashboard): é um relatório de intervalo fixo, fora dele é 'sem dado'."""
    if variables and telemetry_data:
        return map_sensor_values(variables, telemetry_data)
    values = variables.values() if isinstance(variables, dict) else variables
    for v in values:
        v['values'] = []
    return variables

def handler(event, context):
    """
    Get Report Data

    GET /saas/report
    Query params (required):
      - start: ISO datetime (ex: 2026-05-21T10:00:00Z)
      - end: ISO datetime (ex: 2026-05-21T18:00:00Z)

    Query params (optional):
      - equipmentId: Filter by specific equipment
      - variables: Comma-separated list of variables (ex: temperature,humidity)

    Returns: Equipment list with telemetry data in time range
    """
    print("INFO: Starting report get...")
    try:
        # 1. Validate token
        if "Authorization" not in event['headers']:
            raise Exception("401::Token não informado")

        token_data = validateTokenSaaS(
            event['headers']['Authorization'],
            environ['REGION']
        )

        if not token_data['success']:
            raise Exception(f"401::{token_data['message']}")

        # 2. Authorize
        auth = authorizeSaaS(
            token_data['data'],
            environ['DYNAMODB_TABLE_ROLES'],
            'report',
            'read',
            environ['REGION']
        )

        # 3. Parse query parameters
        query_params = event.get('queryStringParameters') or {}

        start_str = query_params.get('start')
        end_str = query_params.get('end')
        equipment_id = query_params.get('equipmentId')
        variables_filter = query_params.get('variables')
        target_scope = resolveSaaSTargetScope(
            auth,
            query_params.get('organizationId'),
            query_params.get('tenantId'),
            require_tenant=True
        )

        if not start_str or not end_str:
            raise Exception("400::Parâmetros start e end são obrigatórios")

        # Parse timestamps
        start_timestamp = parse_datetime(start_str)
        end_timestamp = parse_datetime(end_str)

        # Validate range
        if start_timestamp >= end_timestamp:
            raise Exception("400::Data inicial deve ser anterior à data final")

        # Limit to reasonable range (30 days)
        max_range_ms = 30 * 24 * 60 * 60 * 1000
        if (end_timestamp - start_timestamp) > max_range_ms:
            raise Exception("400::Período máximo de 30 dias")

        # 4. Get equipment list
        equipment_list = get_tenant_equipment(
            target_scope['tenantId'],
            equipment_id,
            environ['REGION']
        )
        equipment_list = [
            equipment for equipment in equipment_list
            if equipment.get('organizationId') == target_scope['organizationId']
        ]

        # 5. For each equipment, get telemetry and map sensor values
        report_data = []
        total_data_points = 0
        telemetry_truncated = False

        for equipment in equipment_list:
            equipment_id_curr = equipment.get('equipmentId')

            # Get telemetry data for time range
            telemetry, truncated = get_telemetry_range(
                equipment_id_curr,
                start_timestamp,
                end_timestamp,
                environ['REGION']
            )
            telemetry_truncated = telemetry_truncated or truncated

            # Grants da role do tenant primeiro: o filtro de 'variables' é escolha do
            # usuário, o grant não é — pedir explicitamente uma variável restrita não
            # pode liberá-la
            sensors = filterVariablesForRole(equipment.get('sensors', {}), auth)
            states = filterVariablesForRole(equipment.get('states', {}), auth)

            # Filter sensors/states if requested
            if variables_filter:
                sensors = filter_variables(sensors, variables_filter)
                states = filter_variables(states, variables_filter)

            # Map sensor/state values
            sensors = build_report_variable_values(sensors, telemetry)
            states = build_report_variable_values(states, telemetry)

            # Count data points
            sensors_list = list(sensors.values()) if isinstance(sensors, dict) else sensors
            states_list = list(states.values()) if isinstance(states, dict) else states
            for sensor in sensors_list:
                total_data_points += len(sensor.get('values', []))
            for state in states_list:
                total_data_points += len(state.get('values', []))

            # Build equipment response
            equipment_data = {
                'equipmentId': equipment_id_curr,
                'name': equipment.get('name'),
                'type': equipment.get('type'),
                'model': equipment.get('model'),
                'modelName': equipment.get('modelName'),
                'local': equipment.get('local'),
                'status': equipment.get('status', 'active'),
                'sensors': sensors_list,
                'states': states_list
            }

            report_data.append(equipment_data)

        print(f"INFO: Report generated: {len(report_data)} equipment, {total_data_points} data points")

        return lambdaReturn({
            "statusCode": 200,
            "message": "Relatório gerado com sucesso",
            "data": {
                "equipment": report_data,
                "period": {
                    "start": start_str,
                    "end": end_str,
                    "startTimestamp": start_timestamp,
                    "endTimestamp": end_timestamp
                },
                "filters": {
                    "equipmentId": equipment_id,
                    "variables": variables_filter.split(',') if variables_filter else None
                },
                "summary": {
                    "totalEquipment": len(report_data),
                    "totalDataPoints": total_data_points,
                    "truncated": telemetry_truncated
                }
            }
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
