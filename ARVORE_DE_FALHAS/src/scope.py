"""Regras canônicas de escopo organizacional e de tenant."""
from __future__ import annotations

from typing import Optional


ORG_SCOPE_TENANT_SENTINELS = frozenset({"", "none", "platform"})


def normalize_tenant_id(value: object) -> Optional[str]:
    """Converte sentinelas do provedor de identidade em ausência de tenant."""
    tenant_id = str(value or "").strip()
    if tenant_id.lower() in ORG_SCOPE_TENANT_SENTINELS:
        return None
    return tenant_id


def scope_type_for(tenant_id: Optional[str]) -> str:
    return "tenant" if normalize_tenant_id(tenant_id) else "organization"


def scope_storage_segment(tenant_id: Optional[str]) -> str:
    return normalize_tenant_id(tenant_id) or "_organization"
