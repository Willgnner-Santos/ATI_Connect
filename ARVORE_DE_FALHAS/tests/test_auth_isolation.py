"""
Testes de regressão para:
  R01 — O chat consulta modos de falha sem isolar o tenant
  R02 — DELETE de árvore não exige catalog:write no caminho JWT

Nenhum deste testes depende de serviços externos (DB, Bedrock, RabbitMQ).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.jwt_validator import TenantContext, get_current_tenant_optional


# ── helpers de fixture ────────────────────────────────────────────────────────


_VALID_UUID = "00000000-0000-0000-0000-000000000001"


def _make_tenant(org_id="org1", tenant_id="tenant1", role="user") -> TenantContext:
    return TenantContext(
        org_id=org_id,
        tenant_id=tenant_id,
        organization_role=role,
        tenant_role=None,
        sub="sub-test",
        raw_attrs={},
    )


@pytest.fixture()
def fault_tree_app():
    """App FastAPI mínimo com o router de fault-trees; usuário com role=user (sem catalog:write)."""
    from src.fault_tree.router import router as ft_router

    app = FastAPI()
    app.include_router(ft_router)
    app.dependency_overrides[get_current_tenant_optional] = lambda: _make_tenant()
    return app


@pytest.fixture()
def ft_client(fault_tree_app):
    return TestClient(fault_tree_app, raise_server_exceptions=False)


# ── R02: DELETE sem catalog:write deve retornar 403 ──────────────────────────


def test_delete_without_catalog_write_returns_403(ft_client):
    """R02 — usuário sem catalog:write recebe 403 ao tentar deletar árvore."""
    with patch("src.fault_tree.router.db") as mock_db:
        mock_db.get_equipment_fault_tree.return_value = {
            "id": "tree1",
            "org_id": "org1",
            "tenant_id": "tenant1",
        }
        mock_db.delete_fault_tree.return_value = True
        response = ft_client.delete(f"/fault-trees/equipment/{_VALID_UUID}")

    assert response.status_code == 403, (
        f"R02 não corrigido — esperado 403, obteve {response.status_code}. "
        "O endpoint DELETE deve exigir catalog:write no caminho JWT."
    )


def test_delete_without_catalog_write_does_not_call_delete_fn(ft_client):
    """R02 — delete_fault_tree não deve ser chamado quando o usuário não tem catalog:write."""
    with patch("src.fault_tree.router.db") as mock_db:
        mock_db.get_equipment_fault_tree.return_value = {
            "id": "tree1",
            "org_id": "org1",
            "tenant_id": "tenant1",
        }
        mock_db.delete_fault_tree.return_value = True
        ft_client.delete("/fault-trees/equipment/eq1")

    assert not mock_db.delete_fault_tree.called, (
        "R02 não corrigido — delete_fault_tree foi chamado sem que o usuário "
        "tivesse catalog:write."
    )


# ── R01: pipeline de chat deve passar tenant_id para consulta de fault tree ──


def test_chat_pipeline_passes_tenant_id_to_get_fault_tree():
    """R01 — get_equipment_fault_tree deve receber o tenant_id do usuário autenticado."""
    from src.chat.pipeline import run_chat_pipeline

    captured: list[dict] = []

    def fake_get_fault_tree(*, org_id, tenant_id, equipment_id):
        captured.append({
            "equipment_id": equipment_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
        })
        return None

    mock_chat_store = MagicMock()
    mock_chat_store.read_last.return_value = []
    mock_chat_store.count.return_value = 0
    mock_chat_store.append.return_value = None

    mock_summary_store = MagicMock()
    mock_summary_store.get.return_value = ("", 0)

    with (
        patch("src.chat.pipeline.postgres_client") as mock_db,
        patch("src.chat.pipeline.atualizar_resumo_se_necessario", return_value=""),
        patch("src.chat.pipeline.reescrever_pergunta_para_busca", return_value="modo de falha bomba"),
        patch("src.chat.pipeline._search_ingest_context", return_value={
            "context_text": "", "sources": [], "filenames": [],
        }),
        patch("src.chat.pipeline._chat_no_solution_identifier_answer", return_value=None),
        patch("src.chat.pipeline.build_failure_mode_chat_context") as mock_fmc,
        patch("src.chat.pipeline._chat_claude", return_value="resposta de teste"),
    ):
        mock_db.get_equipment_fault_tree.side_effect = fake_get_fault_tree
        mock_fmc.return_value = MagicMock(found=False, sources=[], text="")

        run_chat_pipeline(
            equipment_id="eq1",
            org_id="org1",
            tenant_id="tenant_A",
            message="qual o modo de falha?",
            chat_store=mock_chat_store,
            summary_store=mock_summary_store,
        )

    assert captured, "get_equipment_fault_tree não foi chamado durante o pipeline de chat."

    for call in captured:
        assert call["tenant_id"] == "tenant_A", (
            f"R01 não corrigido — get_equipment_fault_tree chamado sem tenant_id correto: "
            f"{call}. O pipeline deve passar tenant_id para isolar o tenant."
        )


def test_chat_pipeline_cross_tenant_data_does_not_leak():
    """R01 — dados do tenant_B não devem aparecer em resposta construída para tenant_A."""
    from src.chat.pipeline import run_chat_pipeline

    MARKER_B = "DADO_EXCLUSIVO_TENANT_B"

    def fake_get_fault_tree(*, org_id, tenant_id, equipment_id):
        if tenant_id == "tenant_B":
            return {"failure_modes": [{"name": MARKER_B, "description": MARKER_B}]}
        return None

    mock_chat_store = MagicMock()
    mock_chat_store.read_last.return_value = []
    mock_chat_store.count.return_value = 0
    mock_chat_store.append.return_value = None

    mock_summary_store = MagicMock()
    mock_summary_store.get.return_value = ("", 0)

    failure_mode_items_received: list = []

    def capture_fmc(failure_modes, query, max_items=8):
        failure_mode_items_received.extend(failure_modes)
        return MagicMock(found=bool(failure_modes), sources=[], text="")

    with (
        patch("src.chat.pipeline.postgres_client") as mock_db,
        patch("src.chat.pipeline.atualizar_resumo_se_necessario", return_value=""),
        patch("src.chat.pipeline.reescrever_pergunta_para_busca", return_value="modo de falha"),
        patch("src.chat.pipeline._search_ingest_context", return_value={
            "context_text": "", "sources": [], "filenames": [],
        }),
        patch("src.chat.pipeline._chat_no_solution_identifier_answer", return_value=None),
        patch("src.chat.pipeline.build_failure_mode_chat_context") as mock_fmc,
        patch("src.chat.pipeline._chat_claude", return_value="resposta"),
    ):
        mock_db.get_equipment_fault_tree.side_effect = fake_get_fault_tree
        mock_fmc.side_effect = capture_fmc

        run_chat_pipeline(
            equipment_id="eq_shared",
            org_id="org1",
            tenant_id="tenant_A",
            message="quais os modos de falha?",
            chat_store=mock_chat_store,
            summary_store=mock_summary_store,
        )

    leaked = any(MARKER_B in str(item) for item in failure_mode_items_received)
    assert not leaked, (
        f"R01 não corrigido — dados do tenant_B ('{MARKER_B}') vazaram para o contexto "
        "de chat do tenant_A. O pipeline deve passar tenant_id à consulta de fault tree."
    )


# ── R01 hardening: invariantes estruturais do método de storage ──────────────


def test_get_equipment_fault_tree_is_keyword_only():
    """R01 — get_equipment_fault_tree deve rejeitar chamada com args posicionais.

    Se o método aceitar posicional, um caller pode trocar org_id e tenant_id
    sem aviso em tempo de execução.
    """
    from src.storage.postgres_client import postgres_client as db_client

    with pytest.raises(TypeError):
        # Chamada posicional deve levantar TypeError antes de qualquer acesso ao DB
        db_client.get_equipment_fault_tree("org1", "tenant1", "eq1")


def test_get_fault_tree_by_equipment_raises_if_tenant_id_none():
    """R01 — get_fault_tree_by_equipment legado deve levantar ValueError se tenant_id=None.

    Elimina o fallback silencioso para consulta cross-tenant.
    """
    from src.storage.postgres_client import postgres_client as db_client

    with pytest.raises(ValueError, match="tenant_id"):
        db_client.get_fault_tree_by_equipment("eq1", "org1")  # tenant_id omitido

    with pytest.raises(ValueError, match="tenant_id"):
        db_client.get_fault_tree_by_equipment("eq1", "org1", tenant_id=None)

    with pytest.raises(ValueError, match="tenant_id"):
        db_client.get_fault_tree_by_equipment("eq1", "org1", tenant_id="")


# ── R01 hardening: DELETE via chave interna exige tenant_id explícito ────────


def test_delete_via_internal_key_without_tenant_id_returns_400(fault_tree_app):
    """R01 — DELETE via chave interna sem tenant_id deve retornar 400.

    A versão anterior de resolved_tenant = tenant.tenant_id if tenant else None
    resultava em None (consulta cross-tenant) neste caminho.
    """
    client = TestClient(fault_tree_app, raise_server_exceptions=False)
    with patch("src.fault_tree.router.settings") as mock_settings:
        mock_settings.INTERNAL_API_KEY = "test-internal-key"

        response = client.delete(
            f"/fault-trees/equipment/{_VALID_UUID}",
            params={"org_id": "org1"},  # tenant_id ausente
            headers={"X-Internal-Key": "test-internal-key"},
        )

    assert response.status_code == 400, (
        f"R01 — DELETE via chave interna sem tenant_id deveria retornar 400, "
        f"obteve {response.status_code}."
    )
    assert "tenant_id" in response.text.lower(), (
        "Mensagem de erro deve mencionar 'tenant_id'."
    )


def test_delete_via_internal_key_with_tenant_id_uses_it(fault_tree_app):
    """R01 — DELETE via chave interna com tenant_id deve usá-lo na query de busca."""
    client = TestClient(fault_tree_app, raise_server_exceptions=False)
    with (
        patch("src.fault_tree.router.settings") as mock_settings,
        patch("src.fault_tree.router.db") as mock_db,
    ):
        mock_settings.INTERNAL_API_KEY = "test-internal-key"
        mock_db.get_equipment_fault_tree.return_value = None  # 404

        response = client.delete(
            f"/fault-trees/equipment/{_VALID_UUID}",
            params={"org_id": "org1", "tenant_id": "tenant_X"},
            headers={"X-Internal-Key": "test-internal-key"},
        )

    # Independente do 404, o que importa é que a query usou tenant_id="tenant_X"
    mock_db.get_equipment_fault_tree.assert_called_once_with(
        org_id="org1",
        tenant_id="tenant_X",
        equipment_id=_VALID_UUID,
    )
