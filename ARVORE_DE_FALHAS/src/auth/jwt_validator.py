"""
Validação local de id_token do Cognito via JWKs públicos.

O id_token é um JWT RS256 assinado pelo Cognito. As chaves públicas são baixadas
uma vez do endpoint well-known e ficam em cache pelo lifetime do processo.
Nenhuma chamada à AWS é feita por requisição — sem dependência de IAM policy.

O frontend deve enviar o id_token (não o access_token) no header Authorization.
"""
from __future__ import annotations

import dataclasses
import json
import threading
import time
from typing import Any, Optional

import httpx
import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.algorithms import RSAAlgorithm
from loguru import logger

from src.config import settings
from src.scope import normalize_tenant_id

_bearer_scheme = HTTPBearer(auto_error=True)

# ── JWKs ──────────────────────────────────────────────────────────────────────

_JWKS_TTL: float = 3600.0       # segundos — refresh automático a cada 1 hora
_JWKS_LOCK = threading.Lock()
_JWKS_CACHE: tuple[dict, ...] | None = None
_JWKS_FETCHED_AT: float = 0.0


def _refresh_jwks() -> tuple[dict, ...]:
    """Baixa as chaves públicas do Cognito sem cache. Levanta HTTPException em caso de falha."""
    url = (
        f"https://cognito-idp.{settings.COGNITO_REGION}.amazonaws.com"
        f"/{settings.COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    )
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        keys = response.json()["keys"]
        return tuple(keys)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Falha ao atualizar chaves públicas do Cognito: {exc}",
        )


def _get_jwks(*, force_refresh: bool = False) -> tuple[dict, ...]:
    """Retorna as chaves do cache, refrescando se o TTL expirou ou se forçado.

    force_refresh=True — busca novas chaves mesmo dentro do TTL:
      - Falha de rede → levanta HTTPException (kid desconhecido e rede indisponível).
    force_refresh=False — atualização normal por TTL:
      - Falha de rede → preserva cache stale e loga warning (melhor que indisponibilidade).
      - Sem cache stale → levanta HTTPException.
    """
    global _JWKS_CACHE, _JWKS_FETCHED_AT

    with _JWKS_LOCK:
        now = time.monotonic()
        # 0.0 e sentinela de cache nunca carregado/forcado a expirar em testes.
        # Em hosts com uptime menor que o TTL, (monotonic() - 0) pode ser < TTL.
        expired = (
            _JWKS_CACHE is None
            or _JWKS_FETCHED_AT <= 0.0
            or (now - _JWKS_FETCHED_AT) >= _JWKS_TTL
        )

        if force_refresh or expired:
            try:
                _JWKS_CACHE = _refresh_jwks()
                _JWKS_FETCHED_AT = now
            except HTTPException as exc:
                if _JWKS_CACHE is not None and not force_refresh:
                    # TTL expirou mas rede falhou → cache stale é melhor que erro
                    logger.warning(
                        f"Falha ao refrescar JWKS (usando cache anterior): {exc.detail}"
                    )
                else:
                    raise

        return _JWKS_CACHE  # type: ignore[return-value]  # garantido não-None aqui


def _reset_jwks_cache() -> None:
    """Reseta o cache de JWKS. Para uso exclusivo em testes."""
    global _JWKS_CACHE, _JWKS_FETCHED_AT
    with _JWKS_LOCK:
        _JWKS_CACHE = None
        _JWKS_FETCHED_AT = 0.0


def _get_public_key(token: str):
    """Resolve a chave pública pelo kid, com refresh automático em kid desconhecido."""
    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.DecodeError as exc:
        raise HTTPException(status_code=401, detail=f"Token malformado: {exc}")

    kid = header.get("kid")

    # Tentativa 1: cache atual
    for key_data in _get_jwks():
        if key_data.get("kid") == kid:
            return RSAAlgorithm.from_jwk(json.dumps(key_data))

    # kid não encontrado — pode ser nova chave pós-rotação; tenta refresh uma vez
    try:
        refreshed_keys = _get_jwks(force_refresh=True)
    except HTTPException:
        raise HTTPException(
            status_code=401,
            detail="Chave de assinatura não encontrada e atualização de JWKS falhou.",
        )

    for key_data in refreshed_keys:
        if key_data.get("kid") == kid:
            return RSAAlgorithm.from_jwk(json.dumps(key_data))

    raise HTTPException(status_code=401, detail="Chave de assinatura não encontrada.")


# ── RBAC ──────────────────────────────────────────────────────────────────────

GLOBAL_ORGANIZATION_ROLES = frozenset({"superadmin", "collaborator"})

# Roles padrão de tenant — idêntico ao TENANT_DEFAULT_ROLES do backend principal.
TENANT_DEFAULT_ROLES: dict[str, dict] = {
    "gestor": {
        "roleName": "Gestor",
        "permissions": [
            "user:read",
            "user:write",
            "equipment:read",
            "dashboard:read",
            "report:read",
            "catalog:read",
        ],
    },
    "tecnico": {
        "roleName": "Técnico",
        "permissions": [
            "equipment:read",
            "equipment:update",
            "action:read",
            "action:execute",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:read",
        ],
    },
    "user": {
        "roleName": "Usuário",
        "permissions": [
            "equipment:read",
            "action:read",
            "dashboard:read",
            "report:read",
            "catalog:read",
            "virtual-technician:read",
        ],
    },
}


def _check_permission(role_data: dict, module: str, action: str) -> bool:
    permissions = role_data.get("permissions")
    if not permissions:
        return False
    if isinstance(permissions, list):
        if "*:*" in permissions:
            return True
        if f"{module}:*" in permissions:
            return True
        if f"{module}:{action}" in permissions:
            return True
        return False
    if isinstance(permissions, dict):
        return bool(permissions.get(module, {}).get(action, False))
    return False


def _has_tenant_scope(tenant_id: Optional[str]) -> bool:
    return normalize_tenant_id(tenant_id) is not None


def _is_global_role(role: str) -> bool:
    return role in GLOBAL_ORGANIZATION_ROLES


def is_global_role(role: str) -> bool:
    """Interface pública para decisões de escopo nos handlers."""
    return _is_global_role(role)


def _resolve_role_data(
    organization_id: str, tenant_id: Optional[str], role_name: str
) -> dict:
    """Resolve a definição de role: tenant roles em memória, org roles no DynamoDB."""
    if _has_tenant_scope(tenant_id) and not _is_global_role(role_name):
        role_data = TENANT_DEFAULT_ROLES.get(role_name)
        if not role_data:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Role '{role_name}' não reconhecida. "
                    f"Roles de tenant válidas: {', '.join(TENANT_DEFAULT_ROLES)}"
                ),
            )
        return role_data

    # Roles de organização / globais → DynamoDB
    table_name = settings.DYNAMODB_TABLE_ROLES
    if not table_name:
        return {"permissions": ["*:*"]}

    try:
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name=settings.DYNAMODB_REGION)
        table = dynamodb.Table(table_name)
        response = table.get_item(
            Key={
                "pk": f"ORG#{organization_id}#ROLES",
                "sk": f"ROLE#{role_name}",
            }
        )
        item = response.get("Item")
        if not item:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role_name}' não encontrada para a organização '{organization_id}'.",
            )
        return item
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao verificar permissões: {exc}")


# ── TenantContext ─────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class TenantContext:
    org_id: str
    tenant_id: Optional[str]
    organization_role: str
    tenant_role: Optional[str]
    sub: str
    raw_attrs: dict[str, Any]


# ── Validação do id_token ─────────────────────────────────────────────────────


def _validate_id_token(token: str) -> dict[str, Any]:
    """Valida assinatura e claims do id_token localmente via JWKs do Cognito."""
    public_key = _get_public_key(token)

    decode_options: dict[str, Any] = {}
    decode_kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],
        "issuer": (
            f"https://cognito-idp.{settings.COGNITO_REGION}.amazonaws.com"
            f"/{settings.COGNITO_USER_POOL_ID}"
        ),
        "options": {"verify_aud": True},
        "audience": settings.COGNITO_CLIENT_ID,
        "leeway": 30,  # tolera até 30s de clock skew entre Cognito e servidor
    }

    try:
        claims = pyjwt.decode(token, public_key, **decode_kwargs)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado.")
    except pyjwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="Token de emissor inválido.")
    except pyjwt.InvalidAudienceError:
        raise HTTPException(status_code=401, detail="Token com audience inválido.")
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Token inválido: {exc}")

    if claims.get("token_use") != "id":
        raise HTTPException(
            status_code=401,
            detail="Token inválido: envie o id_token, não o access_token.",
        )

    return claims


def _build_context(claims: dict[str, Any]) -> TenantContext:
    # Remove prefixo 'custom:' dos claims — mesmo padrão do validateTokenSaaS
    attrs = {k.replace("custom:", ""): v for k, v in claims.items()}

    org_id = str(attrs.get("organizationId") or "").strip()
    organization_role = str(attrs.get("organizationRole") or "").strip()
    raw_tenant_id = str(attrs.get("tenantId") or "").strip()
    tenant_id = normalize_tenant_id(raw_tenant_id)
    sub = str(attrs.get("sub") or "").strip()

    if not org_id:
        raise HTTPException(status_code=401, detail="Usuário não associado a uma organização.")
    if tenant_id is None and not _is_global_role(organization_role):
        raise HTTPException(status_code=401, detail="Usuário não associado a um tenant.")
    if not organization_role:
        raise HTTPException(status_code=401, detail="Role da organização não informada no token.")
    if not sub:
        raise HTTPException(status_code=401, detail="Identificação do usuário não encontrada.")

    return TenantContext(
        org_id=org_id,
        tenant_id=tenant_id,
        organization_role=organization_role,
        tenant_role=attrs.get("tenantRole"),
        sub=sub,
        raw_attrs=attrs,
    )


# ── FastAPI dependencies ──────────────────────────────────────────────────────


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> TenantContext:
    claims = _validate_id_token(credentials.credentials)
    return _build_context(claims)


_bearer_scheme_optional = HTTPBearer(auto_error=False)


async def get_current_tenant_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme_optional),
) -> Optional[TenantContext]:
    if credentials is None:
        return None
    try:
        claims = _validate_id_token(credentials.credentials)
        return _build_context(claims)
    except HTTPException:
        return None


def require_permission(module: str, action: str):
    """Dependency factory — verifica permissão module:action no contexto do tenant.

    Superadmin e collaborator podem sobrescrever o escopo via query params:
      ?org_id=<organizationId>&tenant_id=<tenantId>
    Usuários comuns são bloqueados ao próprio tenant — query params ignorados.
    """

    async def dependency(
        request: Request,
        ctx: TenantContext = Depends(get_current_tenant),
    ) -> TenantContext:
        role_data = _resolve_role_data(ctx.org_id, ctx.tenant_id, ctx.organization_role)
        if not _check_permission(role_data, module, action):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Acesso negado: permissão '{module}:{action}' não disponível "
                    f"para a role '{ctx.organization_role}'."
                ),
            )

        if _is_global_role(ctx.organization_role):
            override_org = request.query_params.get("org_id")
            has_tenant_override = "tenant_id" in request.query_params
            override_tenant = normalize_tenant_id(
                request.query_params.get("tenant_id")
            )
            if override_org or has_tenant_override:
                ctx = dataclasses.replace(
                    ctx,
                    org_id=override_org or ctx.org_id,
                    tenant_id=override_tenant if has_tenant_override else ctx.tenant_id,
                )

        return ctx

    return dependency
