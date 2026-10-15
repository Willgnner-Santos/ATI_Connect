import sys
import importlib.util
from pathlib import Path

import pytest


LAYER_PATH = Path(__file__).parents[1] / "01_layers" / "plataforma_ati" / "python"
sys.path.insert(0, str(LAYER_PATH))

import plataforma_ati as ati


def load_handler(relative_path, module_name):
    path = Path(__file__).parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tenant_auth(role="tecnico"):
    return {"role": role, "tenantRole": role, "tenantId": "tenant-1"}


def test_execute_roles_has_precedence_over_legacy_allowed_roles():
    equipment = {
        "commands": {
            "relay": {
                "variable": "relay",
                "dataType": "bool",
                "allowedRoles": ["tecnico"],
                "executeRoles": ["gestor"],
            }
        }
    }
    with pytest.raises(Exception, match="403::"):
        ati.authorizeCommandExecution(equipment, "relay", tenant_auth("tecnico"))


def test_legacy_role_aliases_remain_compatible():
    command = {"variable": "relay", "dataType": "bool", "allowedRoles": ["technician"]}
    equipment = {"commands": {"relay": command}}
    assert ati.authorizeCommandExecution(equipment, "relay", tenant_auth("tecnico")) is command


def test_missing_tenant_role_fails_closed_for_schema_and_values():
    auth = {"role": "tecnico", "tenantId": "tenant-1"}
    sensors = {"secret": {"viewRoles": ["tecnico"]}}
    assert ati.filterVariablesForRole(sensors, auth) == {}
    assert ati.filterValuesForRole({"secret": 42}, sensors, {}, auth) == {}


def test_last_values_uses_same_tenant_role_as_schema_filter():
    auth = {"role": "tecnico", "tenantRole": "user", "tenantId": "tenant-1"}
    sensors = {"secret": {"viewRoles": ["tecnico"]}}
    assert ati.filterVariablesForRole(sensors, auth) == {}
    assert ati.filterValuesForRole({"secret": 42}, sensors, {}, auth) == {}


def test_int_rejects_fraction_and_enforces_constraints():
    command = {"variable": "level", "dataType": "int", "min": 1, "max": 10, "allowedValues": [1, 5, 10]}
    with pytest.raises(Exception, match="inteiro"):
        ati.coerceCommandValue(command, 1.9)
    with pytest.raises(Exception, match="menor ou igual"):
        ati.coerceCommandValue(command, 99)
    with pytest.raises(Exception, match="valores permitidos"):
        ati.coerceCommandValue(command, 2)
    assert ati.coerceCommandValue(command, 5) == 5


def test_bool_enforces_allowed_values():
    command = {"variable": "relay", "dataType": "bool", "allowedValues": [True]}
    with pytest.raises(Exception, match="valores permitidos"):
        ati.coerceCommandValue(command, False)
    assert ati.coerceCommandValue(command, True) is True


def test_authorization_falls_back_to_legacy_role_id_in_same_tenant(monkeypatch):
    calls = []

    def fake_resolve(_table, _org, tenant, role, _region):
        calls.append((tenant, role))
        if role == "technician":
            return {"success": True, "data": {"permissions": ["equipment:read"]}}
        return {"success": True, "data": {}}

    monkeypatch.setattr(ati, "resolveSaaSRoleDefinition", fake_resolve)
    auth = ati.authorizeSaaS(
        {
            "organizationId": "org-1",
            "tenantId": "tenant-1",
            "organizationRole": "technician",
            "sub": "user-1",
        },
        "roles-table",
        "equipment",
        "read",
    )

    assert calls == [("tenant-1", "tecnico"), ("tenant-1", "technician")]
    assert auth["role"] == "tecnico"


def test_virtual_technician_sql_serializes_string_literals_safely():
    handler = load_handler(
        "02_lambdas/saas/virtual-technician/create/handler.py",
        "virtual_technician_create_handler",
    )
    query = handler.build_iot_rule_query(
        "tenant/device/telemetry",
        [{"variable": "state", "operator": "=", "type": "string", "value": "x' OR true"}],
        "tenant-1",
        "action-1",
        "equipment-1",
    )
    assert 'state = "x\' OR true"' in query
    assert "state = 'x' OR true'" not in query


def test_virtual_technician_payload_uses_current_command_schema():
    handler = load_handler(
        "02_lambdas/saas/virtual-technician/executor/handler.py",
        "virtual_technician_executor_handler",
    )
    payload = handler.build_mqtt_payload(
        {"variable": "level", "value": 5.0, "type": "string"},
        "equipment-1",
        {"variable": "level", "dataType": "int", "min": 1, "max": 5},
        "execution-1",
    )
    assert payload["valor"] == 5
    assert payload["commandExecutionId"] == "execution-1"


@pytest.mark.parametrize("field_type", ["int", "number"])
def test_generic_numeric_validation_rejects_boolean(field_type):
    result = ati.validateInput(
        [{"field": "value", "type": field_type, "mandatory": True}],
        {"value": True},
    )
    assert result["success"] is False


def test_dynamodb_pagination_preserves_existing_list_contract():
    class FakeTable:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [{"id": 1}], "LastEvaluatedKey": {"pk": "next"}}
            return {"Items": [{"id": 2}]}

    table = FakeTable()
    assert ati.getAllDynamoItems(table, KeyConditionExpression="ignored") == [{"id": 1}, {"id": 2}]
    assert table.calls[1]["ExclusiveStartKey"] == {"pk": "next"}


def test_internal_error_details_are_not_returned_to_client():
    response = ati.lambdaReturn({"statusCode": 500, "message": "Erro interno: secret"})
    assert "secret" not in response["body"]
