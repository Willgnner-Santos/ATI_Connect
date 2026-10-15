from helpers import api_delete, api_get, api_post, api_put

# O executor do técnico virtual monta o payload MQTT a partir de
# action['variable'] / action['value'] / action['type'] — e desde a introdução do
# grant a variável precisa ser um comando cadastrado no equipamento e liberado para
# a role do chamador (authorizeScheduledActions), senão o agendamento seria um
# desvio do allowedRoles.
GRANTED_ACTION = "relay1"
BLOCKED_ACTION = "relay_admin"


def _equipment_commands():
    return {
        GRANTED_ACTION: {"name": "Relay 1", "resource": GRANTED_ACTION, "dataType": "bool",
                         "allowedRoles": ["tecnico"]},
        BLOCKED_ACTION: {"name": "Relay Admin", "resource": BLOCKED_ACTION, "dataType": "bool",
                         "allowedRoles": []},
    }


def _make_equipment(equipment_factory, admin_token, test_org, test_tenant):
    # Provisionar equipamento exige equipment:write, que a role de tenant não tem:
    # nasce com o token do admin da org e é automatizado com o token do técnico.
    status, body = equipment_factory(
        admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        commands=_equipment_commands(),
    )
    assert status == 201
    return body["data"]["equipmentId"]


def _vt_actions(variable=GRANTED_ACTION):
    return [{"variable": variable, "value": True, "type": "boolean"}]


def test_create_action_type_with_conditions(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                             equipment_factory, vt_action_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA Action Rule",
        type="action",
        equipmentId=equipment_id,
        conditions=[{"variable": "temperature", "operator": ">", "value": 80, "type": "number"}],
        actions=_vt_actions(),
    )
    assert status == 201
    assert body["data"]["type"] == "action"


def test_create_action_rejects_rce_style_payload(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                                   equipment_factory, vt_action_factory):
    """Regressão do achado C1 (RCE via eval()): variável/operador fora do
    allowlist devem ser rejeitados como entrada inválida (400), nunca
    executados como código."""
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA RCE Attempt",
        type="action",
        equipmentId=equipment_id,
        conditions=[{
            "variable": "__import__('os').system('echo pwned')",
            "operator": ">",
            "value": 1,
            "type": "number",
        }],
        actions=_vt_actions(),
    )
    assert status == 400


def test_create_action_with_blocked_command_forbidden(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                                        equipment_factory, vt_action_factory):
    """A automação não pode ser usada para burlar o executeRoles: agendar uma
    ação que a role não envia pelo endpoint síncrono também é 403."""
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA Blocked Command",
        type="action",
        equipmentId=equipment_id,
        conditions=[{"variable": "temperature", "operator": ">", "value": 80, "type": "number"}],
        actions=_vt_actions(BLOCKED_ACTION),
    )
    assert status == 403


def test_create_action_with_unknown_command_rejected(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                                       equipment_factory, vt_action_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA Unknown Command",
        type="action",
        equipmentId=equipment_id,
        conditions=[{"variable": "temperature", "operator": ">", "value": 80, "type": "number"}],
        actions=_vt_actions("nao_cadastrada"),
    )
    assert status == 404


def test_create_schedule_type(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                               equipment_factory, vt_action_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA Schedule",
        type="schedule",
        equipmentId=equipment_id,
        actions=_vt_actions(),
        schedule={"type": "interval", "interval": "daily", "time": "03:00"},
    )
    assert status == 201
    assert body["data"]["type"] == "schedule"


def test_create_schedule_with_blocked_command_forbidden(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                                          equipment_factory, vt_action_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)

    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA Schedule Blocked",
        type="schedule",
        equipmentId=equipment_id,
        actions=_vt_actions(BLOCKED_ACTION),
        schedule={"type": "interval", "interval": "daily", "time": "03:00"},
    )
    assert status == 403


def test_list_filters_by_tenant(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                 equipment_factory, vt_action_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)
    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA List Action",
        type="action",
        equipmentId=equipment_id,
        conditions=[{"variable": "temperature", "operator": ">", "value": 50, "type": "number"}],
        actions=_vt_actions(),
    )
    assert status == 201
    action_id = body["data"]["actionId"]

    status, body = api_get(base_url, "/saas/virtual-technician", token=tenant_tecnico_token)
    assert status == 200
    assert action_id in [a["actionId"] for a in body["data"]["actions"]]


def test_view_cross_tenant_forbidden(base_url, org_admin_token, tenant_tecnico_token, superadmin_token, test_org, test_tenant,
                                      equipment_factory, vt_action_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)
    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA View Ownership",
        type="action",
        equipmentId=equipment_id,
        conditions=[{"variable": "temperature", "operator": ">", "value": 50, "type": "number"}],
        actions=_vt_actions(),
    )
    assert status == 201
    action_id = body["data"]["actionId"]

    # tenantId real (a busca no DynamoDB usa pk=TENANT#{tenantId}#..., então um
    # tenantId inexistente já daria 404 antes de chegar à checagem de
    # ownership) combinado com um organizationId diferente do dono real —
    # passa por resolveSaaSTargetScope (global role só exige presença e
    # organizationId != PLATFORM), encontra o item pelo tenantId certo, e só
    # então falha na checagem de ownership dentro do handler (403).
    status, body = api_get(base_url, f"/saas/virtual-technician/{action_id}", token=superadmin_token,
                            params={"organizationId": "some-other-org-not-the-owner",
                                    "tenantId": test_tenant["tenantId"]})
    assert status == 403


def test_update_status_only_does_not_error(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                            equipment_factory, vt_action_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)
    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA Update Status",
        type="action",
        equipmentId=equipment_id,
        conditions=[{"variable": "temperature", "operator": ">", "value": 50, "type": "number"}],
        actions=_vt_actions(),
    )
    assert status == 201
    action_id = body["data"]["actionId"]

    status, body = api_put(base_url, f"/saas/virtual-technician/{action_id}", token=tenant_tecnico_token, json={
        "status": "inactive",
    })
    assert status == 200
    assert body["data"]["status"] == "inactive"


def test_update_to_blocked_command_forbidden(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                               equipment_factory, vt_action_factory):
    """O update valida o grant ANTES de mexer na IoT Rule: a regra original tem de
    continuar de pé depois do 403."""
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)
    status, body = vt_action_factory(
        tenant_tecnico_token,
        name="QA Update Grant",
        type="action",
        equipmentId=equipment_id,
        conditions=[{"variable": "temperature", "operator": ">", "value": 50, "type": "number"}],
        actions=_vt_actions(),
    )
    assert status == 201
    action_id = body["data"]["actionId"]
    rule_name = body["data"]["ruleName"]

    status, body = api_put(base_url, f"/saas/virtual-technician/{action_id}", token=tenant_tecnico_token, json={
        "conditions": [{"variable": "temperature", "operator": ">", "value": 60, "type": "number"}],
        "actions": _vt_actions(BLOCKED_ACTION),
    })
    assert status == 403

    status, body = api_get(base_url, f"/saas/virtual-technician/{action_id}", token=tenant_tecnico_token)
    assert status == 200
    assert body["data"]["ruleName"] == rule_name
    assert [a["variable"] for a in body["data"]["actions"]] == [GRANTED_ACTION]


def test_delete_removes_action(base_url, org_admin_token, tenant_tecnico_token, test_org, test_tenant,
                                equipment_factory):
    equipment_id = _make_equipment(equipment_factory, org_admin_token, test_org, test_tenant)
    status, body = api_post(base_url, "/saas/virtual-technician", token=tenant_tecnico_token, json={
        "name": "QA Delete Me",
        "type": "action",
        "equipmentId": equipment_id,
        "conditions": [{"variable": "temperature", "operator": ">", "value": 50, "type": "number"}],
        "actions": _vt_actions(),
    })
    assert status == 201
    action_id = body["data"]["actionId"]

    status, body = api_delete(base_url, f"/saas/virtual-technician/{action_id}", token=tenant_tecnico_token)
    assert status == 200

    status, body = api_get(base_url, f"/saas/virtual-technician/{action_id}", token=tenant_tecnico_token)
    assert status == 404
