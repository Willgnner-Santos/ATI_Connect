from datetime import datetime, timedelta, timezone

from helpers import api_get


def test_dashboard_get_requires_organization_id_for_global_role(base_url, superadmin_token):
    status, body = api_get(base_url, "/saas/dashboard", token=superadmin_token)
    assert status == 400


def test_dashboard_get_as_tenant_tecnico(base_url, tenant_tecnico_token, test_org, test_tenant):
    status, body = api_get(base_url, "/saas/dashboard", token=tenant_tecnico_token)
    assert status == 200
    for equipment in body["data"]["equipment"]:
        assert equipment.get("organizationId", test_org["organizationId"]) == test_org["organizationId"] \
            if "organizationId" in equipment else True


def test_dashboard_get_cross_org_forbidden(base_url, tenant_tecnico_token):
    status, body = api_get(base_url, "/saas/dashboard", token=tenant_tecnico_token,
                            params={"organizationId": "PLATFORM"})
    assert status == 403


def test_report_get_requires_tenant_id_for_global_role(base_url, superadmin_token, test_org):
    now = datetime.now(timezone.utc)
    status, body = api_get(base_url, "/saas/report", token=superadmin_token, params={
        "organizationId": test_org["organizationId"],
        "start": (now - timedelta(days=1)).isoformat(),
        "end": now.isoformat(),
    })
    assert status == 400


def test_report_get_success_as_tenant_tecnico(base_url, tenant_tecnico_token):
    now = datetime.now(timezone.utc)
    status, body = api_get(base_url, "/saas/report", token=tenant_tecnico_token, params={
        "start": (now - timedelta(days=1)).isoformat(),
        "end": now.isoformat(),
    })
    assert status == 200
    assert "equipment" in body["data"]
    assert "period" in body["data"]


def test_report_get_range_over_30_days_rejected(base_url, tenant_tecnico_token):
    now = datetime.now(timezone.utc)
    status, body = api_get(base_url, "/saas/report", token=tenant_tecnico_token, params={
        "start": (now - timedelta(days=40)).isoformat(),
        "end": now.isoformat(),
    })
    assert status == 400


def test_report_get_start_after_end_rejected(base_url, tenant_tecnico_token):
    now = datetime.now(timezone.utc)
    status, body = api_get(base_url, "/saas/report", token=tenant_tecnico_token, params={
        "start": now.isoformat(),
        "end": (now - timedelta(days=1)).isoformat(),
    })
    assert status == 400
