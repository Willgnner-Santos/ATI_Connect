"""Módulo de comandos (commands) e grants de sensor/comando.

Cobre o cadastro de 'commands' em equipamento e modelo, o envio síncrono via
IoT Core e a aplicação dos grants:
  - 'viewRoles' / 'allowedRoles' em sensors/states  -> quem vê a variável e o valor;
  - 'allowedRoles' em commands -> quem envia o comando.
Os grants só se aplicam a usuário de tenant; admin de organização e roles globais
configuram os grants e por isso enxergam tudo.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from helpers import api_delete, api_get, api_post, api_put

GRANTED_CMD = "relay1"
BLOCKED_CMD = "relay_admin"
OPEN_SENSOR = "temp1"
RESTRICTED_SENSOR = "temp_secreto"


def _commands_payload():
    """Um comando liberado para 'tecnico' e um bloqueado para qualquer role de tenant."""
    return {
        GRANTED_CMD: {"name": "Relay 1", "resource": GRANTED_CMD, "dataType": "bool",
                      "allowedRoles": ["tecnico"]},
        BLOCKED_CMD: {"name": "Relay Admin", "resource": BLOCKED_CMD, "dataType": "bool",
                      "allowedRoles": []},
    }


def _sensors_payload():
    return {
        OPEN_SENSOR: {"sensorType": "analog"},
        RESTRICTED_SENSOR: {"sensorType": "analog", "viewRoles": ["supervisor"]},
    }


def _make_equipment(equipment_factory, admin_token, test_org, test_tenant, **overrides):
    payload = {
        "organizationId": test_org["organizationId"],
        "tenantId": test_tenant["tenantId"],
        "sensors": _sensors_payload(),
        "commands": _commands_payload(),
    }
    payload.update(overrides)
    status, body = equipment_factory(admin_token, **payload)
    assert status == 201, f"Falha ao criar equipamento de teste: {body}"
    return body["data"]["equipmentId"]


# ============================================
# Cadastro de comandos no equipamento
# ============================================

def test_equipment_new_with_commands_success(base_url, org_admin_token, test_org, test_tenant,
                                              equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)
    assert equipment_id


def test_equipment_new_command_missing_name_rejected(base_url, org_admin_token, test_org,
                                                      test_tenant, equipment_factory):
    status, _ = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        commands={GRANTED_CMD: {"resource": GRANTED_CMD, "dataType": "bool"}},
    )
    assert status == 400


def test_equipment_new_command_invalid_data_type_rejected(base_url, org_admin_token, test_org,
                                                           test_tenant, equipment_factory):
    status, _ = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        commands={GRANTED_CMD: {"name": "Relay", "resource": GRANTED_CMD, "dataType": "invalid"}},
    )
    assert status == 400


def test_equipment_new_sensor_view_roles_must_be_list(base_url, org_admin_token, test_org,
                                                       test_tenant, equipment_factory):
    status, _ = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        sensors={OPEN_SENSOR: {"sensorType": "analog", "viewRoles": "tecnico"}},
    )
    assert status == 400


# ============================================
# Grants na leitura (equipamento, lista, comandos)
# ============================================

def test_equipment_view_applies_grants(base_url, org_admin_token, tenant_tecnico_token, test_org,
                                        test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}", token=tenant_tecnico_token)
    assert status == 200
    assert OPEN_SENSOR in body["data"]["sensors"]
    assert RESTRICTED_SENSOR not in body["data"]["sensors"]
    assert GRANTED_CMD in body["data"]["commands"]
    assert BLOCKED_CMD not in body["data"]["commands"]


def test_equipment_view_as_org_admin_sees_everything(base_url, org_admin_token, test_org,
                                                      test_tenant, equipment_factory):
    """Quem configura o grant precisa ver o que configurou."""
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}", token=org_admin_token)
    assert status == 200
    assert RESTRICTED_SENSOR in body["data"]["sensors"]
    assert BLOCKED_CMD in body["data"]["commands"]


def test_equipment_view_filters_last_values(base_url, org_admin_token, tenant_tecnico_token,
                                             test_org, test_tenant, equipment_factory,
                                             equipment_table):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    equipment_table.update_item(
        Key={"pk": f"TENANT#{test_tenant['tenantId']}#EQUIPMENT", "sk": f"EQUIPMENT#{equipment_id}"},
        UpdateExpression="SET lastValues = :v",
        ExpressionAttributeValues={":v": {OPEN_SENSOR: Decimal("21"),
                                          RESTRICTED_SENSOR: Decimal("99")}},
    )

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}", token=tenant_tecnico_token)
    assert status == 200
    assert OPEN_SENSOR in body["data"]["lastValues"]
    assert RESTRICTED_SENSOR not in body["data"]["lastValues"]

    status, body = api_get(base_url, "/saas/equipment", token=tenant_tecnico_token)
    assert status == 200
    listed = [e for e in body["data"] if e["equipmentId"] == equipment_id]
    assert len(listed) == 1
    assert RESTRICTED_SENSOR not in listed[0]["sensors"]
    assert RESTRICTED_SENSOR not in listed[0].get("lastValues", {})
    assert BLOCKED_CMD not in listed[0]["commands"]


def test_equipment_command_list_returns_only_granted(base_url, org_admin_token, tenant_tecnico_token,
                                                      test_org, test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}/action",
                           token=tenant_tecnico_token)
    assert status == 200
    variables = [cmd["variable"] for cmd in body["data"]["commands"]]
    assert variables == [GRANTED_CMD]


def test_equipment_command_list_empty_for_role_without_grant(base_url, org_admin_token,
                                                              tenant_user_token, test_org, test_tenant,
                                                              equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}/action",
                           token=tenant_user_token)
    assert status == 200
    assert body["data"]["commands"] == []


def test_equipment_command_list_unknown_equipment_not_found(base_url, tenant_tecnico_token):
    status, _ = api_get(base_url, "/saas/equipment/00000000-0000-0000-0000-000000000000/action",
                        token=tenant_tecnico_token)
    assert status == 404


# ============================================
# Envio do comando
# ============================================

def test_equipment_action_send_success(base_url, org_admin_token, tenant_tecnico_token, test_org,
                                        test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                            token=tenant_tecnico_token, json={"command": GRANTED_CMD, "value": True})
    assert status == 200, body
    assert body["data"]["command"] == GRANTED_CMD
    assert body["data"]["value"] is True
    assert body["data"]["topic"].startswith("telemetry/")
    assert body["data"]["sentAt"]


def test_equipment_action_send_blocked_command_forbidden(base_url, org_admin_token,
                                                          tenant_tecnico_token, test_org,
                                                          test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, _ = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                         token=tenant_tecnico_token, json={"command": BLOCKED_CMD, "value": True})
    assert status == 403


def test_equipment_action_send_forbidden_for_role_without_execute(base_url, org_admin_token,
                                                                    tenant_user_token, test_org,
                                                                    test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, _ = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                         token=tenant_user_token, json={"command": GRANTED_CMD, "value": True})
    assert status == 403


def test_equipment_action_send_unknown_command_not_found(base_url, org_admin_token,
                                                          tenant_tecnico_token, test_org,
                                                          test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, _ = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                         token=tenant_tecnico_token, json={"command": "nao_cadastrado", "value": True})
    assert status == 404


def test_equipment_action_send_wrong_value_type_rejected(base_url, org_admin_token,
                                                          tenant_tecnico_token, test_org,
                                                          test_tenant, equipment_factory):
    """dataType 'bool' não aceita 'on' nem 1."""
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    for value in ("on", 1):
        status, _ = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                             token=tenant_tecnico_token,
                             json={"command": GRANTED_CMD, "value": value})
        assert status == 400, f"valor {value!r} deveria ser rejeitado para dataType bool"


def test_equipment_action_send_missing_value_rejected(base_url, org_admin_token,
                                                       tenant_tecnico_token, test_org, test_tenant,
                                                       equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, _ = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                         token=tenant_tecnico_token, json={"command": GRANTED_CMD})
    assert status == 400


def test_equipment_action_send_to_unknown_equipment_not_found(base_url, tenant_tecnico_token):
    status, _ = api_post(base_url, "/saas/equipment/00000000-0000-0000-0000-000000000000/action",
                         token=tenant_tecnico_token, json={"command": GRANTED_CMD, "value": True})
    assert status == 404


def test_equipment_action_send_to_inactive_equipment_rejected(base_url, org_admin_token,
                                                               tenant_tecnico_token, test_org,
                                                               test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, _ = api_put(base_url, f"/saas/equipment/{equipment_id}",
                        token=org_admin_token, json={"status": "inactive"})
    assert status == 200

    status, _ = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                         token=tenant_tecnico_token, json={"command": GRANTED_CMD, "value": True})
    assert status == 400


# ============================================
# Atualização dos grants
# ============================================

def test_equipment_update_can_revoke_command_grant(base_url, org_admin_token, tenant_tecnico_token,
                                                    test_org, test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, _ = api_put(base_url, f"/saas/equipment/{equipment_id}", token=org_admin_token, json={
        "commands": {GRANTED_CMD: {"name": "Relay 1", "resource": GRANTED_CMD,
                                   "dataType": "bool", "allowedRoles": ["gestor"]}},
    })
    assert status == 200

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}/action",
                           token=tenant_tecnico_token)
    assert status == 200
    assert body["data"]["commands"] == []

    status, _ = api_post(base_url, f"/saas/equipment/{equipment_id}/action",
                         token=tenant_tecnico_token, json={"command": GRANTED_CMD, "value": True})
    assert status == 403


# ============================================
# Modelo de equipamento
# ============================================

def test_equipment_model_with_commands_and_grants(base_url, run_id, org_admin_token,
                                                   tenant_tecnico_token, test_org,
                                                   equipment_model_factory):
    """O modelo declara os mesmos commands do equipamento — sem filtro aqui a role
    descobriria pelo modelo o que o equipamento esconde dela."""
    status, body = equipment_model_factory(
        org_admin_token,
        modelId=f"qa_{run_id}_model_cmds",
        name="QA Model Commands",
        equipmentType="sensor",
        organizationId=test_org["organizationId"],
        sensors=_sensors_payload(),
        commands=_commands_payload(),
    )
    assert status == 201, body
    model_id = body["data"]["modelId"]

    status, body = api_get(base_url, "/saas/catalog/equipment-model", token=org_admin_token,
                           params={"organizationId": test_org["organizationId"]})
    assert status == 200
    model = next(m for m in body["data"] if m["modelId"] == model_id)
    assert BLOCKED_CMD in model["commands"]
    assert RESTRICTED_SENSOR in model["sensors"]

    status, body = api_get(base_url, "/saas/catalog/equipment-model", token=tenant_tecnico_token)
    assert status == 200
    model = next(m for m in body["data"] if m["modelId"] == model_id)
    assert list(model["commands"]) == [GRANTED_CMD]
    assert RESTRICTED_SENSOR not in model["sensors"]


# ============================================
# Dashboard e relatório
# ============================================

def test_dashboard_applies_grants(base_url, org_admin_token, tenant_tecnico_token, test_org,
                                   test_tenant, equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = api_get(base_url, "/saas/dashboard", token=tenant_tecnico_token)
    assert status == 200
    equipment = next(e for e in body["data"]["equipment"] if e["equipmentId"] == equipment_id)

    sensor_variables = [s.get("variable") for s in equipment["sensors"]]
    assert OPEN_SENSOR in sensor_variables
    assert RESTRICTED_SENSOR not in sensor_variables
    cmd_variables = [c.get("variable") for c in equipment["commands"]]
    assert cmd_variables == [GRANTED_CMD]


def test_report_applies_view_grants(base_url, org_admin_token, tenant_tecnico_token, test_org,
                                     test_tenant, equipment_factory):
    """O grant é aplicado ANTES do filtro opcional 'variables'."""
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    now = datetime.now(timezone.utc)
    status, body = api_get(base_url, "/saas/report", token=tenant_tecnico_token, params={
        "equipmentId": equipment_id,
        "start": (now - timedelta(days=1)).isoformat(),
        "end": now.isoformat(),
        "variables": f"{OPEN_SENSOR},{RESTRICTED_SENSOR}",
    })
    assert status == 200, body
    equipment = next(e for e in body["data"]["equipment"] if e["equipmentId"] == equipment_id)
    variables = [s.get("variable") for s in equipment["sensors"]]
    assert OPEN_SENSOR in variables
    assert RESTRICTED_SENSOR not in variables
