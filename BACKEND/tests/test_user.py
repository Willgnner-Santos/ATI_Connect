from helpers import api_delete, api_get, api_post, api_put


def test_user_new_success(base_url, org_admin_token, test_org, test_tenant, run_id, user_factory):
    status, body = user_factory(
        org_admin_token,
        name="QA New User",
        email=f"qa-{run_id}-newuser@example.com",
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        role="user",
        password="NewUserPass!1",
    )
    assert status == 201
    assert body["data"]["organizationId"] == test_org["organizationId"]


def test_user_new_duplicate_email(base_url, org_admin_token, test_org, test_tenant, run_id, user_factory):
    email = f"qa-{run_id}-dupuser@example.com"
    status, body = user_factory(
        org_admin_token, name="QA Dup", email=email,
        organizationId=test_org["organizationId"], tenantId=test_tenant["tenantId"],
        role="user", password="DupUserPass!1",
    )
    assert status == 201

    status, body = user_factory(
        org_admin_token, name="QA Dup 2", email=email,
        organizationId=test_org["organizationId"], tenantId=test_tenant["tenantId"],
        role="user", password="DupUserPass!1",
    )
    assert status == 400


def test_user_new_nonexistent_role(base_url, org_admin_token, test_org, test_tenant, run_id, user_factory):
    status, body = user_factory(
        org_admin_token, name="QA Bad Role", email=f"qa-{run_id}-badrole@example.com",
        organizationId=test_org["organizationId"], tenantId=test_tenant["tenantId"],
        role="does-not-exist", password="BadRolePass!1",
    )
    assert status == 400


def test_user_get_filtered_by_tenant(base_url, org_admin_token, test_tenant, tenant_user):
    status, body = api_get(base_url, "/saas/user", token=org_admin_token,
                            params={"tenantId": test_tenant["tenantId"]})
    assert status == 200
    user_ids = [u["userId"] for u in body["data"]]
    assert tenant_user["userId"] in user_ids


def test_user_get_other_org_tenant_returns_nothing(base_url, org_admin_token):
    """Admin da org pode filtrar por tenant, mas o resultado é peneirado pela própria
    organização — tenant de outra org não vaza usuário nenhum."""
    status, body = api_get(base_url, "/saas/user", token=org_admin_token,
                            params={"tenantId": "some-other-tenant-id"})
    assert status == 200
    assert body["data"] == []


def test_user_get_forbidden_for_tenant_role(base_url, tenant_tecnico_token, test_tenant):
    """Nenhuma das roles de tenant ('tecnico'/'user') tem user:read: administrar
    usuários é papel da organização."""
    status, body = api_get(base_url, "/saas/user", token=tenant_tecnico_token,
                            params={"tenantId": test_tenant["tenantId"]})
    assert status == 403


def test_user_update_self_name_success(base_url, test_tenant):
    status, login_body = api_post(base_url, "/saas/auth/login", json={
        "email": test_tenant["tecnicoEmail"],
        "password": test_tenant["tecnicoPassword"],
    })
    assert status == 200
    token = login_body["data"]["authorization"]["access_token"]
    tecnico_id = test_tenant["tecnicoUserId"]

    status, body = api_put(base_url, f"/saas/user/{tecnico_id}", token=token, json={
        "name": "QA Tecnico Renamed",
    })
    assert status == 200


def test_user_update_self_role_forbidden(base_url, test_tenant):
    status, login_body = api_post(base_url, "/saas/auth/login", json={
        "email": test_tenant["tecnicoEmail"],
        "password": test_tenant["tecnicoPassword"],
    })
    assert status == 200
    token = login_body["data"]["authorization"]["access_token"]
    tecnico_id = test_tenant["tecnicoUserId"]

    status, body = api_put(base_url, f"/saas/user/{tecnico_id}", token=token, json={
        "role": "admin",
    })
    assert status == 403


def test_user_delete_and_reactivate_cycle(base_url, org_admin_token, test_org, test_tenant, run_id, user_factory):
    status, body = user_factory(
        org_admin_token, name="QA Cycle User", email=f"qa-{run_id}-cycleuser@example.com",
        organizationId=test_org["organizationId"], tenantId=test_tenant["tenantId"],
        role="user", password="CycleUserPass!1",
    )
    assert status == 201
    user_id = body["data"]["userId"]

    status, _ = api_delete(base_url, f"/saas/user/{user_id}", token=org_admin_token)
    assert status == 200

    status, _ = api_post(base_url, f"/saas/user/{user_id}/reactivate", token=org_admin_token)
    assert status == 200
