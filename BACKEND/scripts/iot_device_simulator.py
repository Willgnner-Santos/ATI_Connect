"""
Simulador de dispositivo IoT "de verdade": roda infinito, publicando telemetria
via MQTT (AWS IoT Core) no topico telemetry/{thingId} do equipamento, exatamente
como um device real faria - passa pela TelemetryRouterTopicRule/Router Lambda de
verdade (ver template.yaml) em vez de escrever direto no DynamoDB.

Uso:
    python scripts/iot_device_simulator.py list-orgs
    python scripts/iot_device_simulator.py list-tenants --org-id <ORG_ID>
    python scripts/iot_device_simulator.py list-equipment --tenant-id <TENANT_ID>
    python scripts/iot_device_simulator.py run --tenant-id <TENANT_ID> --equipment-id <EQUIPMENT_ID> [--interval 300]

Requer credenciais AWS validas para a sandbox com permissao iot:DescribeEndpoint
e iot:Publish (mesmo profile usado por scripts/telemetry_simulator.py).
"""
import argparse
import json
import random
import signal
import sys
import time
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Attr, Key

REGION = "sa-east-1"
TABLE_PREFIX = "plataforma-ati-sandbox"

ORG_TABLE = f"{TABLE_PREFIX}-organizations"
TENANT_TABLE = f"{TABLE_PREFIX}-tenants"
EQ_TABLE = f"{TABLE_PREFIX}-tenant-equipment"

# (valor base, ruido +-) por sensorType, usados quando o sensor nao e booleano
SENSOR_DEFAULTS = {
    "temperature_celsius": (25.0, 3.0),
    "humidity_percent": (55.0, 10.0),
    "pressure_bar": (5.0, 0.5),
    "vibration_mm_s": (2.0, 0.5),
    "current_amp": (10.0, 1.0),
    "voltage_volt": (220.0, 5.0),
    "rpm": (1500.0, 50.0),
    "percentage": (50.0, 10.0),
}
DEFAULT_BASE_NOISE = (50.0, 5.0)
BOOLEAN_FLIP_PROBABILITY = 0.05  # chance de trocar de estado a cada leitura

dynamodb = boto3.resource("dynamodb", region_name=REGION)

_stop = False


def _handle_sigint(_signum, _frame):
    global _stop
    print("\nRecebido Ctrl+C, finalizando apos o envio atual...")
    _stop = True


def get_organizations():
    table = dynamodb.Table(ORG_TABLE)
    response = table.query(
        IndexName="status-index",
        KeyConditionExpression=Key("status").eq("active"),
        FilterExpression=Attr("sk").eq("METADATA"),
    )
    items = response.get("Items", [])
    return [item for item in items if item.get("organizationId") != "PLATFORM"]


def get_tenants(org_id):
    table = dynamodb.Table(TENANT_TABLE)
    response = table.query(KeyConditionExpression=Key("pk").eq(f"ORG#{org_id}#TENANTS"))
    return response.get("Items", [])


def get_equipment_list(tenant_id):
    table = dynamodb.Table(EQ_TABLE)
    response = table.query(KeyConditionExpression=Key("pk").eq(f"TENANT#{tenant_id}#EQUIPMENT"))
    return response.get("Items", [])


def get_equipment(tenant_id, equipment_id):
    for eq in get_equipment_list(tenant_id):
        eq_id = eq.get("equipmentId", eq["sk"].replace("EQUIPMENT#", ""))
        if eq_id == equipment_id:
            return eq
    return None


def get_iot_data_client(region):
    iot = boto3.client("iot", region_name=region)
    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    return boto3.client("iot-data", region_name=region, endpoint_url=f"https://{endpoint}")


def publish_telemetry(iot_data_client, topic, payload):
    iot_data_client.publish(topic=topic, qos=1, payload=json.dumps(payload))


def build_reading(sensors, bool_state):
    payload = {}
    for sensor_key, sensor_data in sensors.items():
        var_name = sensor_data.get("variable", sensor_key)
        s_type = sensor_data.get("sensorType", "number")

        if s_type == "boolean":
            current = bool_state.get(var_name, random.choice([0, 1]))
            if random.random() < BOOLEAN_FLIP_PROBABILITY:
                current = 1 - current
            bool_state[var_name] = current
            payload[var_name] = current
        else:
            base, noise = SENSOR_DEFAULTS.get(s_type, DEFAULT_BASE_NOISE)
            payload[var_name] = round(base + random.uniform(-noise, noise), 2)
    return payload


def cmd_list_orgs(_args):
    orgs = get_organizations()
    if not orgs:
        print("Nenhuma organizacao encontrada.")
        return
    for org in orgs:
        print(f"{org['organizationId']}  {org.get('name', '')}")


def cmd_list_tenants(args):
    tenants = get_tenants(args.org_id)
    if not tenants:
        print("Nenhum tenant encontrado para esta organizacao.")
        return
    for t in tenants:
        tenant_id = t.get("tenantId", t["sk"].replace("TENANT#", ""))
        print(f"{tenant_id}  {t.get('name', '')}")


def cmd_list_equipment(args):
    equipments = get_equipment_list(args.tenant_id)
    if not equipments:
        print("Nenhum equipamento encontrado para este tenant.")
        return
    for eq in equipments:
        eq_id = eq.get("equipmentId", eq["sk"].replace("EQUIPMENT#", ""))
        has_sensors = "sim" if eq.get("sensors") else "nao"
        print(f"{eq_id}  {eq.get('name', '')}  (sensors cadastrados: {has_sensors})")


def cmd_run(args):
    equipment = get_equipment(args.tenant_id, args.equipment_id)
    if not equipment:
        print(f"Equipamento {args.equipment_id} nao encontrado no tenant {args.tenant_id}.")
        sys.exit(1)

    sensors = equipment.get("sensors", {})
    if not sensors:
        print("Este equipamento nao possui 'sensors' cadastrados. Nada para simular.")
        sys.exit(1)

    topic = equipment.get("topic")
    if not topic:
        print("Este equipamento nao possui 'topic' cadastrado (provisionamento IoT incompleto).")
        sys.exit(1)

    iot_data_client = get_iot_data_client(REGION)

    print(f"Publicando via MQTT em '{topic}' a cada {args.interval}s. Ctrl+C para parar.")
    print(f"Sensores: {', '.join(sensors.keys())}")

    signal.signal(signal.SIGINT, _handle_sigint)
    bool_state = {}
    sent = 0

    while not _stop:
        payload = build_reading(sensors, bool_state)
        try:
            publish_telemetry(iot_data_client, topic, payload)
            sent += 1
            dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{dt_str}] #{sent} publicado em {topic}: {payload}")
        except Exception as e:
            print(f"Erro ao publicar telemetria (mantendo o loop): {e}")

        for _ in range(int(args.interval * 10)):
            if _stop:
                break
            time.sleep(0.1)

    print(f"Finalizado. Total de leituras publicadas: {sent}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-orgs", help="Lista organizacoes ativas")

    p_tenants = sub.add_parser("list-tenants", help="Lista tenants de uma organizacao")
    p_tenants.add_argument("--org-id", required=True)

    p_eq = sub.add_parser("list-equipment", help="Lista equipamentos de um tenant")
    p_eq.add_argument("--tenant-id", required=True)

    p_run = sub.add_parser("run", help="Envia telemetria em loop infinito")
    p_run.add_argument("--tenant-id", required=True)
    p_run.add_argument("--equipment-id", required=True)
    p_run.add_argument("--interval", type=float, default=300.0, help="Segundos entre leituras (default: 300 = 5min)")

    args = parser.parse_args()

    if args.command == "list-orgs":
        cmd_list_orgs(args)
    elif args.command == "list-tenants":
        cmd_list_tenants(args)
    elif args.command == "list-equipment":
        cmd_list_equipment(args)
    elif args.command == "run":
        cmd_run(args)


if __name__ == "__main__":
    main()
