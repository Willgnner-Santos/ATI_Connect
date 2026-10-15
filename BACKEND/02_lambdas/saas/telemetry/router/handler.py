import time
from os import environ
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key
from plataforma_ati import getAllDynamoItems

NON_SENSOR_KEYS = {'thingId', 'ruleTimestamp', 'type', 'token', 'recurso', 'valor'}

DEADBAND_PCT = float(environ.get('DEADBAND_PCT', '0.05'))
HEARTBEAT_SECONDS = int(environ.get('HEARTBEAT_SECONDS', '300'))
MIN_INTERVAL_SECONDS = int(environ.get('MIN_INTERVAL_SECONDS', '2'))


def _to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _is_numeric(value):
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def resolve_equipment(thing_id, region):
    table = boto3.resource('dynamodb', region_name=region).Table(environ['DYNAMODB_TABLE_EQUIPMENT'])
    items = getAllDynamoItems(table,
        IndexName='thingId-index',
        KeyConditionExpression=Key('thingId').eq(thing_id)
    )
    if len(items) > 1:
        print(f"WARNING: múltiplos equipamentos encontrados para thingId={thing_id}, usando o primeiro")
    return items[0] if items else None


def _exceeds_deadband(new_val, last_val) -> bool:
    if not _is_numeric(new_val) or not _is_numeric(last_val):
        return new_val != last_val
    new_f, last_f = float(new_val), float(last_val)
    if last_f == 0:
        return new_f != 0
    return abs(new_f - last_f) / abs(last_f) >= DEADBAND_PCT


def _changed_vars(data: dict, last_values: dict) -> dict:
    """Retorna apenas as variáveis que cruzaram o deadband ou são novas."""
    return {k: v for k, v in data.items()
            if last_values.get(k) is None or _exceeds_deadband(v, last_values.get(k))}


def _is_heartbeat_expired(equipment: dict, now_ts: float) -> bool:
    last_telemetry_at = equipment.get('lastTelemetryAt')
    if not last_telemetry_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_telemetry_at)
        return (datetime.fromtimestamp(now_ts, tz=timezone.utc) - last_dt).total_seconds() >= HEARTBEAT_SECONDS
    except Exception:
        return True


def update_last_seen(equipment, now_iso: str, region):
    table = boto3.resource('dynamodb', region_name=region).Table(environ['DYNAMODB_TABLE_EQUIPMENT'])
    table.update_item(
        Key={
            'pk': f"TENANT#{equipment['tenantId']}#EQUIPMENT",
            'sk': f"EQUIPMENT#{equipment['equipmentId']}"
        },
        UpdateExpression="SET lastSeen = :ts",
        ExpressionAttributeValues={':ts': now_iso}
    )


def try_claim_telemetry_slot(equipment, full_data: dict, now_iso: str, region) -> bool:
    """Atomic slot claim. full_data é gravado em lastValues (snapshot completo)."""
    table = boto3.resource('dynamodb', region_name=region).Table(environ['DYNAMODB_TABLE_EQUIPMENT'])
    min_ts = (datetime.fromisoformat(now_iso) - timedelta(seconds=MIN_INTERVAL_SECONDS)).isoformat()
    try:
        table.update_item(
            Key={
                'pk': f"TENANT#{equipment['tenantId']}#EQUIPMENT",
                'sk': f"EQUIPMENT#{equipment['equipmentId']}"
            },
            UpdateExpression="SET lastSeen = :ts, lastValues = :vals, lastTelemetryAt = :ts",
            ConditionExpression=(
                "attribute_not_exists(lastTelemetryAt) OR lastTelemetryAt <= :min_ts"
            ),
            ExpressionAttributeValues={':ts': now_iso, ':vals': full_data, ':min_ts': min_ts}
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def handler(event, context):
    print(f"INFO: Router recebeu evento com campos: {sorted(event.keys())}")

    thing_id = event.get('thingId')
    if not thing_id:
        print("ERROR: evento sem thingId — SQL da rule não extraiu topic(2) corretamente")
        return

    if event.get('type') == 'send':
        print(f"INFO: Ignorando eco de comando send para {thing_id}")
        return

    region = environ['REGION']
    equipment = resolve_equipment(thing_id, region)
    if not equipment:
        print(f"WARNING: Nenhum equipamento encontrado para thingId={thing_id} — descartando")
        return

    equipment_id = equipment['equipmentId']
    data = {k: _to_decimal(v) for k, v in event.items() if k not in NON_SENSOR_KEYS}
    timestamp_ms = int(event.get('ruleTimestamp') or time.time() * 1000)
    now_iso = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()

    last_values = equipment.get('lastValues') or {}
    heartbeat = _is_heartbeat_expired(equipment, timestamp_ms / 1000)
    changed = _changed_vars(data, last_values)

    if not heartbeat and not changed:
        update_last_seen(equipment, now_iso, region)
        print(f"INFO: Telemetria filtrada (deadband) para equipment={equipment_id}")
        return

    # Heartbeat: snapshot completo para garantir continuidade histórica.
    # Mudança normal: só as variáveis que cruzaram o deadband (sparse record).
    record_data = data if heartbeat else changed

    if try_claim_telemetry_slot(equipment, data, now_iso, region):
        telemetry_table = boto3.resource('dynamodb', region_name=region).Table(environ['DYNAMODB_TABLE_TELEMETRY'])
        telemetry_table.put_item(Item={
            "pk": f"EQUIPMENT#{equipment_id}",
            "sk": timestamp_ms,
            "data": record_data
        })
        print(f"INFO: Telemetria gravada para equipment={equipment_id} sk={timestamp_ms} vars={list(record_data)}")
    else:
        update_last_seen(equipment, now_iso, region)
        print(f"INFO: Telemetria filtrada (race) para equipment={equipment_id}")
