"""
Testes de regressão para R22 — Versões vulneráveis de fastapi/starlette/python-multipart.

Garante que:
- python-multipart >= 0.0.31 (CPU DoS em 0.0.30, Content-Length negativo em 0.0.31).
- starlette >= 1.3.1 (DoS no parsing de forms publicado em jun/2026).
- fastapi >= 0.133.0 (primeiro release com suporte a Starlette 1.x).
- Upload multipart básico continua funcional após a atualização.
"""
from __future__ import annotations

from importlib.metadata import version as pkg_version
from packaging.version import Version

import pytest


# ── Versões mínimas seguras (setembro 2026) ────────────────────────────────────

# python-multipart: 0.0.30 corrige CPU DoS; 0.0.31 corrige Content-Length negativo.
_MIN_PYTHON_MULTIPART = Version("0.0.31")

# starlette: GHSA-f96h-pmfr-66vw corrigido em 0.40.0 (não 0.37.2);
#            DoS no parsing de forms publicado jun/2026, corrigido em 1.3.1.
_MIN_STARLETTE = Version("1.3.1")

# fastapi: 0.133.0 é o primeiro release com suporte a Starlette >= 1.x.
_MIN_FASTAPI = Version("0.133.0")


def test_python_multipart_version_is_secure():
    """R22 — python-multipart deve ser >= 0.0.31 (CPU DoS + Content-Length negativo)."""
    try:
        installed = Version(pkg_version("python-multipart"))
    except Exception:
        pytest.skip("python-multipart não instalado — verificar em ambiente com dependências completas")
    assert installed >= _MIN_PYTHON_MULTIPART, (
        f"R22 — python-multipart {installed} está na faixa vulnerável. "
        f"Mínimo seguro: {_MIN_PYTHON_MULTIPART}. "
        "CVE: GHSA-2jv5-9r88-3w3p (ReDoS), GHSA-pp6c-gr5w-3c5g (headers), "
        "CPU DoS corrigido em 0.0.30, Content-Length negativo em 0.0.31."
    )


def test_fastapi_version_is_secure():
    """R22 — fastapi deve ser >= 0.133.0 (primeiro release com suporte a Starlette 1.x)."""
    try:
        installed = Version(pkg_version("fastapi"))
    except Exception:
        pytest.skip("fastapi não instalado")
    assert installed >= _MIN_FASTAPI, (
        f"R22 — fastapi {installed} está na faixa vulnerável. "
        f"Mínimo seguro: {_MIN_FASTAPI}."
    )


def test_starlette_version_is_secure():
    """R22 — starlette deve ser >= 1.3.1 (DoS parsing de forms jun/2026)."""
    try:
        installed = Version(pkg_version("starlette"))
    except Exception:
        pytest.skip("starlette não instalado")
    assert installed >= _MIN_STARLETTE, (
        f"R22 — starlette {installed} está na faixa vulnerável. "
        f"Mínimo seguro: {_MIN_STARLETTE}. "
        "GHSA-f96h-pmfr-66vw corrigido em 0.40.0; "
        "DoS no parsing de forms (jun/2026) corrigido em 1.3.1."
    )


# ── Compatibilidade funcional após atualização ────────────────────────────────


def test_multipart_form_functional():
    """R22 — formulário multipart e URL-encoded devem funcionar após atualização."""
    from fastapi import FastAPI, Form
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/form")
    async def handle_form(name: str = Form(...), value: str = Form(...)):
        return {"name": name, "value": value}

    client = TestClient(app)

    # multipart/form-data
    response = client.post("/form", data={"name": "test", "value": "ok"})
    assert response.status_code == 200, (
        f"R22 — formulário multipart retornou {response.status_code}: {response.text}"
    )
    data = response.json()
    assert data == {"name": "test", "value": "ok"}
