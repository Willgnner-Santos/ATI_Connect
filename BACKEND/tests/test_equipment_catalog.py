from helpers import api_delete, api_get, api_post, api_put

# Provisionar equipamento é papel da organização: a role de tenant 'tecnico' tem
# equipment:read/equipment:update, mas não equipment:write/delete. Por isso os
# equipamentos de teste nascem com o token do admin da org e são lidos/atualizados
# com o token do técnico.


def test_sensor_type_get_returns_seeded_catalog(base_url, tenant_tecnico_token):
    status, body = api_get(base_url, "/saas/catalog/sensor-type", token=tenant_tecnico_token)
    assert status == 200
    type_ids = [s["typeId"] for s in body["data"]]
    assert "analog" in type_ids


def test_equipment_new_success(base_url, org_admin_token, test_org, test_tenant, equipment_factory):
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
    )
    assert status == 201
    assert body["data"]["equipmentId"]
    assert body["data"]["thingName"]
    assert body["data"]["iot"]["certificatePem"].startswith("-----BEGIN CERTIFICATE-----")
    assert body["data"]["iot"]["privateKey"].startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert body["data"]["iot"]["amazonRootCA1"].startswith("-----BEGIN CERTIFICATE-----")


def test_equipment_new_forbidden_for_tenant_role(base_url, tenant_tecnico_token, test_org, test_tenant,
                                                  equipment_factory):
    """O técnico opera o equipamento, não o provisiona: sem equipment:write o
    cadastro é negado mesmo dentro do próprio tenant."""
    status, body = equipment_factory(
        tenant_tecnico_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
    )
    assert status == 403


def test_equipment_new_unknown_sensor_type(base_url, org_admin_token, test_org, test_tenant, equipment_factory):
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        sensors={"temp1": {"sensorType": "does-not-exist-sensor-type"}},
    )
    assert status == 400


def test_equipment_new_cross_tenant_forbidden(base_url, org_admin_token, test_org, equipment_factory):
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId="some-other-tenant-not-mine",
    )
    assert status == 403


def test_equipment_get_list_and_view(base_url, org_admin_token, tenant_tecnico_token, test_org,
                                     test_tenant, equipment_factory):
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
    )
    assert status == 201
    equipment_id = body["data"]["equipmentId"]

    status, body = api_get(base_url, "/saas/equipment", token=tenant_tecnico_token)
    assert status == 200
    assert equipment_id in [e["equipmentId"] for e in body["data"]]

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}", token=tenant_tecnico_token)
    assert status == 200
    assert body["data"]["equipmentId"] == equipment_id


def test_equipment_view_returns_states_and_last_values(base_url, org_admin_token, tenant_tecnico_token,
                                                        test_org, test_tenant, equipment_factory):
    """O detalhe do equipamento monta o retorno campo a campo (sanitização): garante
    que states/local/lastSeen/lastValues não fiquem de fora como já aconteceu — eram
    documentados no schema Equipment mas nunca devolvidos."""
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        local="QA View Lab",
        sensors={"temp1": {"sensorType": "analog"}},
        states={"andar": {"dataType": "int", "name": "Andar Atual"}},
    )
    assert status == 201
    equipment_id = body["data"]["equipmentId"]

    status, body = api_get(base_url, f"/saas/equipment/{equipment_id}", token=tenant_tecnico_token)
    assert status == 200
    data = body["data"]
    assert data["local"] == "QA View Lab"
    assert data["sensors"]["temp1"]["sensorType"] == "analog"
    assert data["states"]["andar"]["dataType"] == "int"
    assert data["states"]["andar"]["variable"] == "andar"
    # Equipamento recém-criado nunca reportou telemetria: as chaves existem e vêm vazias.
    assert "lastSeen" in data and data["lastSeen"] is None
    assert data["lastValues"] == {}
    # iotMetadata (ARN/ID do certificado) continua fora da resposta.
    assert "iotMetadata" not in data


def test_equipment_update_success(base_url, org_admin_token, tenant_tecnico_token, test_org,
                                  test_tenant, equipment_factory):
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
    )
    assert status == 201
    equipment_id = body["data"]["equipmentId"]

    status, body = api_put(base_url, f"/saas/equipment/{equipment_id}", token=tenant_tecnico_token, json={
        "name": "QA Renamed Equipment",
    })
    assert status == 200


def test_equipment_update_empty_body_rejected(base_url, org_admin_token, tenant_tecnico_token,
                                               test_org, test_tenant, equipment_factory):
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
    )
    assert status == 201
    equipment_id = body["data"]["equipmentId"]

    status, body = api_put(base_url, f"/saas/equipment/{equipment_id}", token=tenant_tecnico_token, json={})
    assert status == 400


def test_equipment_new_duplicate_name_rejected(base_url, org_admin_token, test_org, test_tenant, run_id, equipment_factory):
    name = f"QA Dup Equipment {run_id}"
    status, _ = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        name=name,
    )
    assert status == 201

    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        name=name,
    )
    assert status == 400


def test_equipment_new_duplicate_name_case_insensitive(base_url, org_admin_token, test_org, test_tenant, run_id, equipment_factory):
    name = f"QA Case Equipment {run_id}"
    status, _ = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        name=name,
    )
    assert status == 201

    status, _ = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        name=name.upper(),
    )
    assert status == 400


def test_equipment_update_duplicate_name_rejected(base_url, org_admin_token, tenant_tecnico_token,
                                                   test_org, test_tenant, run_id, equipment_factory):
    name_a = f"QA EqA {run_id}"
    name_b = f"QA EqB {run_id}"

    status, _ = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        name=name_a,
    )
    assert status == 201

    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        name=name_b,
    )
    assert status == 201
    eq_b_id = body["data"]["equipmentId"]

    status, _ = api_put(base_url, f"/saas/equipment/{eq_b_id}", token=tenant_tecnico_token, json={
        "name": name_a,
    })
    assert status == 400


def test_equipment_update_same_name_allowed(base_url, org_admin_token, tenant_tecnico_token,
                                             test_org, test_tenant, run_id, equipment_factory):
    name = f"QA Same Name {run_id}"
    status, body = equipment_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        name=name,
    )
    assert status == 201
    equipment_id = body["data"]["equipmentId"]

    status, _ = api_put(base_url, f"/saas/equipment/{equipment_id}", token=tenant_tecnico_token, json={
        "name": name,
    })
    assert status == 200


def test_equipment_model_crud_cycle(base_url, org_admin_token, test_org, run_id, equipment_model_factory):
    model_id = f"qa_{run_id}_model"
    status, body = equipment_model_factory(
        org_admin_token,
        modelId=model_id,
        name=f"QA Model {run_id}",
        equipmentType="sensor",
        sensors={"temp1": {"sensorType": "analog"}},
        organizationId=test_org["organizationId"],
    )
    assert status == 201

    status, body = api_get(base_url, "/saas/catalog/equipment-model", token=org_admin_token,
                            params={"organizationId": test_org["organizationId"]})
    assert status == 200
    assert model_id in [m["modelId"] for m in body["data"]]

    status, body = api_put(base_url, f"/saas/catalog/equipment-model/{model_id}", token=org_admin_token, json={
        "organizationId": test_org["organizationId"],
        "name": f"QA Model {run_id} Renamed",
    })
    assert status == 200
