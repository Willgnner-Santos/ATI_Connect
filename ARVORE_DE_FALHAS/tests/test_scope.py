from src.auth.jwt_validator import _build_context
from src.scope import normalize_tenant_id, scope_type_for


def test_platform_is_organization_scope_for_superadmin():
    ctx = _build_context({
        "sub": "user-1",
        "custom:organizationId": "org-1",
        "custom:tenantId": "PLATFORM",
        "custom:organizationRole": "superadmin",
    })

    assert ctx.tenant_id is None
    assert scope_type_for(ctx.tenant_id) == "organization"


def test_real_tenant_is_preserved():
    assert normalize_tenant_id(" tenant-1 ") == "tenant-1"
    assert scope_type_for("tenant-1") == "tenant"
