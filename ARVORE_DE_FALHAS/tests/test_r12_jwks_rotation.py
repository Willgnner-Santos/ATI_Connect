"""
Testes de regressão para R12 — Cache de JWKS não acompanha rotação de chaves.

Garante que:
- kid desconhecido dispara um refresh do cache (exatamente um).
- kid conhecido usa cache sem requisição extra.
- Falhas de rede são tratadas explicitamente — não silenciadas.
- Cache stale é preservado em falhas de rede durante TTL normal.
- Primeiro carregamento com falha de rede levanta 503.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_cache():
    """Reseta o cache de JWKS antes e depois de cada teste."""
    from src.auth.jwt_validator import _reset_jwks_cache
    _reset_jwks_cache()
    yield
    _reset_jwks_cache()


def _fake_jwks_response(kids: list[str]) -> MagicMock:
    """Resposta httpx mockada com as chaves informadas."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "keys": [{"kid": kid, "kty": "RSA", "n": "abc", "e": "AQAB"} for kid in kids]
    }
    return mock_resp


def _fake_token(kid: str) -> str:
    """Cria um token JWT com header válido apenas (payload/sig falsos — não para validação)."""
    def _b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{_b64({'alg': 'RS256', 'kid': kid})}.{_b64({'sub': 'test'})}.fakesig"


# ── Cache básico ──────────────────────────────────────────────────────────────


class TestJwksCacheBasic:

    def test_initial_load_fetches_from_cognito(self):
        """R12 — Primeira chamada a _get_jwks deve buscar as chaves via HTTP."""
        from src.auth.jwt_validator import _get_jwks

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["kid1", "kid2"])
            keys = _get_jwks()

        assert len(keys) == 2
        mock_get.assert_called_once()

    def test_second_call_within_ttl_uses_cache(self):
        """R12 — Segunda chamada dentro do TTL não deve fazer requisição HTTP."""
        from src.auth.jwt_validator import _get_jwks

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["kid1"])
            _get_jwks()   # carrega
            _get_jwks()   # deve usar cache

        assert mock_get.call_count == 1, (
            f"R12 — segunda chamada não deve buscar JWKS novamente: "
            f"httpx.get foi chamado {mock_get.call_count} vezes."
        )

    def test_ttl_expiry_triggers_refresh(self):
        """R12 — Após TTL expirar, próxima chamada deve refrescar as chaves."""
        from src.auth import jwt_validator

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["kid1"])
            jwt_validator._get_jwks()

            # Simular TTL expirado
            jwt_validator._JWKS_FETCHED_AT = 0.0
            jwt_validator._get_jwks()

        assert mock_get.call_count == 2, (
            f"R12 — TTL expirado deve disparar refresh: "
            f"httpx.get chamado {mock_get.call_count} vezes (esperado 2)."
        )

    def test_force_refresh_bypasses_ttl(self):
        """R12 — force_refresh=True busca novas chaves independente do TTL."""
        from src.auth.jwt_validator import _get_jwks

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["kid1"])
            _get_jwks()                    # carrega (TTL ativo)
            _get_jwks(force_refresh=True)  # deve buscar mesmo assim

        assert mock_get.call_count == 2


# ── Falhas de rede ────────────────────────────────────────────────────────────


class TestJwksNetworkFailures:

    def test_first_load_failure_raises_503(self):
        """R12 — Falha de rede no primeiro carregamento levanta 503."""
        from src.auth.jwt_validator import _get_jwks

        with patch("src.auth.jwt_validator.httpx.get", side_effect=Exception("timeout")):
            with pytest.raises(HTTPException) as exc_info:
                _get_jwks()

        assert exc_info.value.status_code == 503

    def test_ttl_expiry_failure_preserves_stale_cache(self):
        """R12 — Falha de rede após TTL expirar preserva cache stale (não levanta exceção)."""
        from src.auth import jwt_validator

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["kid1"])
            initial_keys = jwt_validator._get_jwks()

            jwt_validator._JWKS_FETCHED_AT = 0.0  # expirar TTL
            mock_get.side_effect = Exception("network down")

            stale_keys = jwt_validator._get_jwks()  # não deve levantar exceção

        assert stale_keys == initial_keys, (
            "R12 — cache stale deve ser retornado quando rede falha após TTL expirar."
        )

    def test_force_refresh_failure_raises_explicitly(self):
        """R12 — Falha de rede em force_refresh deve levantar exceção (não silenciar)."""
        from src.auth import jwt_validator

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["kid1"])
            jwt_validator._get_jwks()  # carrega cache

            mock_get.side_effect = Exception("network down")
            with pytest.raises(HTTPException):
                jwt_validator._get_jwks(force_refresh=True)


# ── kid desconhecido — rotação de chaves ─────────────────────────────────────


class TestUnknownKidRefresh:

    def test_unknown_kid_triggers_exactly_one_refresh(self):
        """R12 — kid não encontrado no cache dispara exatamente um refresh."""
        from src.auth.jwt_validator import _get_public_key

        token = _fake_token("new-kid")

        with (
            patch("src.auth.jwt_validator.httpx.get") as mock_get,
            patch("src.auth.jwt_validator.RSAAlgorithm.from_jwk", return_value=MagicMock()),
        ):
            # Primeira resposta: sem o novo kid. Segunda (refresh): com ele.
            mock_get.side_effect = [
                _fake_jwks_response(["old-kid"]),
                _fake_jwks_response(["old-kid", "new-kid"]),
            ]
            _get_public_key(token)

        assert mock_get.call_count == 2, (
            f"R12 — kid desconhecido deve disparar exatamente 1 refresh: "
            f"httpx.get chamado {mock_get.call_count} vezes."
        )

    def test_known_kid_does_not_trigger_refresh(self):
        """R12 — kid já presente no cache não deve disparar refresh."""
        from src.auth.jwt_validator import _get_public_key

        token = _fake_token("kid1")

        with (
            patch("src.auth.jwt_validator.httpx.get") as mock_get,
            patch("src.auth.jwt_validator.RSAAlgorithm.from_jwk", return_value=MagicMock()),
        ):
            mock_get.return_value = _fake_jwks_response(["kid1", "kid2"])
            _get_public_key(token)

        assert mock_get.call_count == 1, (
            "R12 — kid conhecido não deve disparar refresh adicional."
        )

    def test_unknown_kid_missing_after_refresh_returns_401(self):
        """R12 — kid ausente mesmo após refresh deve retornar 401."""
        from src.auth.jwt_validator import _get_public_key

        token = _fake_token("ghost-kid")

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["other-kid"])

            with pytest.raises(HTTPException) as exc_info:
                _get_public_key(token)

        assert exc_info.value.status_code == 401

    def test_unknown_kid_with_network_failure_returns_401(self):
        """R12 — kid desconhecido + falha de rede no refresh retorna 401 (não 503)."""
        from src.auth import jwt_validator

        token = _fake_token("rotated-kid")

        with patch("src.auth.jwt_validator.httpx.get") as mock_get:
            mock_get.return_value = _fake_jwks_response(["old-kid"])
            jwt_validator._get_jwks()  # carrega cache

            mock_get.side_effect = Exception("timeout")

            with pytest.raises(HTTPException) as exc_info:
                jwt_validator._get_public_key(token)

        assert exc_info.value.status_code == 401, (
            f"R12 — kid desconhecido + falha de refresh deve retornar 401, "
            f"obteve {exc_info.value.status_code}."
        )
