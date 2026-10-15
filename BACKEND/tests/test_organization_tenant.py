from helpers import api_delete, api_get, api_post


def test_organization_get_list_as_superadmin(base_url, superadmin_token, test_org):
    status, body = api_get(base_url, "/saas/organization", token=superadmin_token)
    assert status == 200
    org_ids = [o["organizationId"] for o in body["data"]]
    assert test_org["organizationId"] in org_ids
    assert "PLATFORM" not in org_ids


def test_organization_get_by_id_as_superadmin(base_url, superadmin_token, test_org):
    status, body = api_get(base_url, "/saas/organization", token=superadmin_token,
                            params={"id": test_org["organizationId"]})
    assert status == 200
    assert body["data"]["organizationId"] == test_org["organizationId"]


def test_organization_get_other_org_forbidden_for_org_admin(base_url, org_admin_token):
    status, body = api_get(base_url, "/saas/organization", token=org_admin_token,
                            params={"id": "PLATFORM"})
    assert status == 403


def test_organization_new_duplicate_email(base_url, superadmin_token, test_org, run_id,
                                           organization_factory, dynamodb):
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-dup",
        email=f"qa-{run_id}-org@example.com",
        cnpj=f"{run_id[1:3]}.{run_id[0:3]}.{run_id[3:6]}/0003-00".upper(),
    )
    assert status == 201

    # organization/new não checa duplicidade de e-mail de organização; a
    # duplicidade real ocorre no e-mail do admin (Cognito UsernameExistsException).
    # Achado: a organização (+ roles semeadas) é gravada ANTES da criação do
    # admin, sem rollback — esta segunda chamada retorna 400 mas já deixou uma
    # organização órfã no DynamoDB. organization_factory só sabe limpar o que a
    # API confirma com 201, então localizamos e limpamos esse órfão manualmente.
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-dup-2",
        email=f"qa-{run_id}-org@example.com",
        cnpj=f"{run_id[2:4]}.{run_id[1:4]}.{run_id[4:7]}/0004-00".upper(),
        admin={"email": test_org["adminEmail"], "name": "dup"},
    )
    assert status == 400

    orphan_scan = dynamodb.Table("plataforma-ati-sandbox-organizations").scan(
        FilterExpression="#n = :n",
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={":n": f"qa-{run_id}-org-dup-2"},
    )
    for item in orphan_scan.get("Items", []):
        orphan_org_id = item["organizationId"]
        dynamodb.Table("plataforma-ati-sandbox-organizations").delete_item(
            Key={"pk": f"ORG#{orphan_org_id}", "sk": "METADATA"}
        )
        for role_id in ("admin", "manager", "viewer"):
            dynamodb.Table("plataforma-ati-sandbox-organization-roles").delete_item(
                Key={"pk": f"ORG#{orphan_org_id}#ROLES", "sk": f"ROLE#{role_id}"}
            )


def test_organization_new_with_cnpj(base_url, superadmin_token, run_id, organization_factory):
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-cnpj",
        email=f"qa-{run_id}-org-cnpj@example.com",
        cnpj="12.345.678/0001-99",
    )
    assert status == 201


def test_organization_new_cnpj_digits_only(base_url, superadmin_token, run_id, organization_factory):
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-cnpj-digits",
        email=f"qa-{run_id}-org-cnpj-digits@example.com",
        cnpj="12345678000100",
    )
    assert status == 201


def test_organization_new_cnpj_alphanumeric(base_url, superadmin_token, run_id, organization_factory):
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-cnpj-alpha",
        email=f"qa-{run_id}-org-cnpj-alpha@example.com",
        cnpj="AB.CDE.FGH/0001-12",
    )
    assert status == 201


def test_organization_new_cnpj_invalid_format_rejected(base_url, superadmin_token, run_id, organization_factory):
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-cnpj-bad",
        email=f"qa-{run_id}-org-cnpj-bad@example.com",
        cnpj="123-invalid-cnpj",
    )
    assert status == 400


def test_organization_new_duplicate_cnpj_rejected(base_url, superadmin_token, run_id, organization_factory):
    cnpj = "AB.222.333/0001-44"
    status, _ = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-cnpj-dup-1",
        email=f"qa-{run_id}-org-cnpj-dup-1@example.com",
        cnpj=cnpj,
    )
    assert status == 201

    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-cnpj-dup-2",
        email=f"qa-{run_id}-org-cnpj-dup-2@example.com",
        cnpj=cnpj.lower(),
    )
    assert status == 400


def test_organization_new_without_cnpj_rejected(base_url, superadmin_token, run_id, organization_factory):
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-no-cnpj",
        email=f"qa-{run_id}-org-no-cnpj@example.com",
    )
    assert status == 400


def test_organization_new_duplicate_name_rejected(base_url, superadmin_token, run_id, organization_factory):
    name = f"qa-{run_id}-dup-name-org"
    status, _ = organization_factory(
        superadmin_token,
        name=name,
        email=f"qa-{run_id}-dup-name-org@example.com",
        cnpj=f"{run_id[3:5]}.{run_id[0:3]}.{run_id[5:8]}/0005-00".upper(),
    )
    assert status == 201

    status, body = organization_factory(
        superadmin_token,
        name=name,
        email=f"qa-{run_id}-dup-name-org-2@example.com",
        cnpj=f"{run_id[4:6]}.{run_id[1:4]}.{run_id[5:8]}/0006-00".upper(),
    )
    assert status == 400


def test_organization_delete_platform_forbidden(base_url, superadmin_token):
    status, body = api_delete(base_url, "/saas/organization/PLATFORM", token=superadmin_token)
    assert status == 403


def test_organization_reactivate_platform_forbidden(base_url, superadmin_token):
    status, body = api_post(base_url, "/saas/organization/PLATFORM/reactivate", token=superadmin_token)
    assert status == 403


def test_organization_reactivate_requires_deleted_status(base_url, superadmin_token, test_org):
    status, body = api_post(base_url, f"/saas/organization/{test_org['organizationId']}/reactivate",
                             token=superadmin_token)
    assert status == 400


def test_organization_delete_and_reactivate_cycle(base_url, superadmin_token, run_id, organization_factory):
    status, body = organization_factory(
        superadmin_token,
        name=f"qa-{run_id}-org-cycle",
        email=f"qa-{run_id}-org-cycle@example.com",
        cnpj=f"{run_id[5:7]}.{run_id[2:5]}.{run_id[0:3]}/0007-00".upper(),
    )
    assert status == 201
    org_id = body["data"]["organizationId"]

    status, _ = api_delete(base_url, f"/saas/organization/{org_id}", token=superadmin_token)
    assert status == 200

    status, body = api_post(base_url, f"/saas/organization/{org_id}/reactivate", token=superadmin_token)
    assert status == 200


def test_tenant_new_with_cnpj(base_url, org_admin_token, test_org, run_id, tenant_factory):
    status, body = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cnpj",
        cnpj="AB.CDE.FGH/0001-12",
    )
    assert status == 201


def test_tenant_new_with_cpf(base_url, org_admin_token, test_org, run_id, tenant_factory):
    status, body = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cpf",
        cpf="123.456.789-09",
    )
    assert status == 201


def test_tenant_new_cnpj_and_cpf_rejected(base_url, org_admin_token, test_org, run_id, tenant_factory):
    status, body = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-both",
        cnpj="12.345.678/0001-99",
        cpf="123.456.789-09",
    )
    assert status == 400


def test_tenant_new_invalid_cpf_format_rejected(base_url, org_admin_token, test_org, run_id, tenant_factory):
    status, body = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cpf-bad",
        cpf="123-invalid",
    )
    assert status == 400


def test_tenant_new_duplicate_cnpj_rejected(base_url, org_admin_token, test_org, run_id, tenant_factory):
    cnpj = "99.888.777/0001-55"
    status, _ = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cnpj-dup-1",
        cnpj=cnpj,
    )
    assert status == 201

    status, _ = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cnpj-dup-2",
        cnpj=cnpj,
    )
    assert status == 400


def test_tenant_new_duplicate_cpf_rejected(base_url, org_admin_token, test_org, run_id, tenant_factory):
    cpf = "987.654.321-00"
    status, _ = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cpf-dup-1",
        cpf=cpf,
    )
    assert status == 201

    status, _ = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cpf-dup-2",
        cpf=cpf,
    )
    assert status == 400


def test_tenant_new_without_document_rejected(base_url, org_admin_token, test_org, run_id, tenant_factory):
    status, body = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-no-doc",
    )
    assert status == 400


def test_tenant_new_duplicate_name_rejected(base_url, org_admin_token, test_org, run_id, tenant_factory):
    name = f"qa-{run_id}-dup-name-tenant"
    status, _ = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=name,
        cnpj=f"{run_id[0:2]}.{run_id[2:5]}.{run_id[5:8]}/0008-00".upper(),
    )
    assert status == 201

    status, body = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=name,
        cpf=f"{run_id[:3]}.{run_id[1:4]}.{run_id[2:5]}-00",
    )
    assert status == 400


def test_tenant_get_scoped_to_own_org(base_url, org_admin_token, test_org, test_tenant):
    status, body = api_get(base_url, "/saas/tenant", token=org_admin_token,
                            params={"organizationId": test_org["organizationId"]})
    assert status == 200
    tenant_ids = [t["tenantId"] for t in body["data"]]
    assert test_tenant["tenantId"] in tenant_ids


def test_tenant_get_cross_org_forbidden(base_url, org_admin_token, test_org):
    # admin da org tem tenant:* — o 403 aqui é escopo (organização alheia), não
    # falta de permissão, que é justamente o que este teste precisa provar.
    status, body = api_get(base_url, "/saas/tenant", token=org_admin_token,
                            params={"organizationId": "PLATFORM"})
    assert status == 403


def test_tenant_new_requires_organization_id(base_url, superadmin_token, run_id):
    status, body = api_post(base_url, "/saas/tenant", token=superadmin_token, json={
        "name": f"qa-{run_id}-orphan-tenant",
    })
    assert status == 400


def test_tenant_delete_and_reactivate_cycle(base_url, org_admin_token, test_org, run_id, tenant_factory):
    status, body = tenant_factory(
        org_admin_token,
        organizationId=test_org["organizationId"],
        name=f"qa-{run_id}-tenant-cycle",
        cnpj=f"{run_id[1:3]}.{run_id[3:6]}.{run_id[5:8]}/0009-00".upper(),
    )
    assert status == 201
    tenant_id = body["data"]["tenantId"]

    status, _ = api_delete(base_url, f"/saas/tenant/{tenant_id}", token=org_admin_token)
    assert status == 200

    status, body = api_post(base_url, f"/saas/tenant/{tenant_id}/reactivate", token=org_admin_token)
    assert status == 200


def test_tenant_reactivate_requires_deleted_status(base_url, org_admin_token, test_tenant):
    status, body = api_post(base_url, f"/saas/tenant/{test_tenant['tenantId']}/reactivate",
                             token=org_admin_token)
    assert status == 400


def test_tenant_reactivate_forbidden_for_tenant_scoped_user(base_url, tenant_tecnico_token, test_tenant):
    # o técnico do tenant tem tenantId fixo (não é admin da org) — reactivate exige
    # ausência de tenant fixo (ver M2/reactivate na revisão de handlers).
    status, body = api_post(base_url, f"/saas/tenant/{test_tenant['tenantId']}/reactivate",
                             token=tenant_tecnico_token)
    assert status == 403
