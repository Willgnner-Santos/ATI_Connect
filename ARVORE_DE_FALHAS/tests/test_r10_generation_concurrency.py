"""
Testes de regressão para R10 — Race condition na promoção de versão de árvore de falhas.

Cenário do bug: duas gerações simultâneas começam com o mesmo estado 'completed'.
A geração mais lenta termina por último e supersede o resultado correto da geração mais rápida.

Bugs provados (falham ANTES da correção, passam DEPOIS):

B1 - activate_fault_tree_version não aceita supersedes_version_id → sem CAS possível.
     Correto: aceita supersedes_version_id Optional[str].

B2 - activate_fault_tree_version não aborta quando a versão esperada já foi superseded.
     Correto: retorna False se rowcount da supersede for 0 (concorrente ganhou).

B3 - activate_fault_tree_version sem versão prévia (primeira geração) deve funcionar.
     Correto: supersedes_version_id=None → verifica que não existe active → promove.

B4 - get_active_fault_tree_version não existe → create_generation_candidate não consegue
     capturar o snapshot da versão ativa atual.
     Correto: método existe e retorna dict com 'id' ou None.

B5 - create_generation_candidate não captura supersedes_version_id.
     Correto: versão criada tem supersedes_version_id = id da versão ativa no momento.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, call, patch

import pytest


# ── B1: activate_fault_tree_version deve aceitar supersedes_version_id ────────

def test_b1_activate_accepts_supersedes_version_id():
    """
    Antes: assinatura é activate_fault_tree_version(version_id, tree_id, org_id, failure_modes).
    Após: aceita supersedes_version_id como parâmetro.
    """
    from src.storage.postgres_client import PostgresClient

    sig = inspect.signature(PostgresClient.activate_fault_tree_version)
    assert "supersedes_version_id" in sig.parameters, (
        "activate_fault_tree_version deve aceitar supersedes_version_id "
        "para implementar CAS e prevenir stale promotion"
    )


# ── B2: promoção com versão já superseded deve retornar False ─────────────────

def test_b2_activate_returns_false_when_expected_active_already_superseded():
    """
    Cenário: geração A e B partem do mesmo estado. B termina primeiro e promove.
    Quando A tenta promover, supersedes_version_id de A já está como 'superseded'
    (foi superseded por B). activate deve retornar False sem promover A.
    """
    from src.storage.postgres_client import PostgresClient

    pg = PostgresClient.__new__(PostgresClient)

    # Simula: supersede step retorna rowcount=0 (versão esperada já não está ativa)
    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: s
    mock_session.__exit__ = MagicMock(return_value=False)

    # supersede UPDATE retorna 0 (versão já foi superseded por B)
    mock_supersede_result = MagicMock()
    mock_supersede_result.rowcount = 0
    # candidate UPDATE e tree UPDATE — não devem ser chamados após falha do CAS
    mock_candidate_result = MagicMock()
    mock_candidate_result.rowcount = 1
    mock_tree_result = MagicMock()
    mock_tree_result.rowcount = 1

    mock_session.execute = MagicMock(side_effect=[
        mock_supersede_result,     # primeira chamada: supersede
        mock_candidate_result,     # segunda: candidata (não deve chegar aqui)
        mock_tree_result,          # terceira: fault_tree (não deve chegar aqui)
    ])

    with patch.object(pg, "get_session", return_value=mock_session):
        result = pg.activate_fault_tree_version(
            version_id="ver-A",
            tree_id="tree-1",
            org_id="org-1",
            failure_modes=[{"title": "modo A"}],
            supersedes_version_id="ver-prev",  # versão que esperávamos superseder
        )

    assert result is False, (
        "activate deve retornar False quando supersedes_version_id já não é 'active' "
        "(geração concorrente já ganhou)"
    )
    # O rollback deve ter sido chamado
    mock_session.rollback.assert_called_once()
    # O commit NÃO deve ter sido chamado
    mock_session.commit.assert_not_called()


def test_b2_activate_succeeds_when_expected_version_is_still_active():
    """
    Geração que termina primeiro deve conseguir promover normalmente.
    supersede rowcount=1 → candidata rowcount=1 → tree rowcount=1 → commit → True.
    """
    from src.storage.postgres_client import PostgresClient

    pg = PostgresClient.__new__(PostgresClient)

    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: s
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_supersede_result = MagicMock()
    mock_supersede_result.rowcount = 1  # versão esperada ainda está ativa → supersede OK
    mock_candidate_result = MagicMock()
    mock_candidate_result.rowcount = 1
    mock_tree_result = MagicMock()
    mock_tree_result.rowcount = 1

    mock_session.execute = MagicMock(side_effect=[
        mock_supersede_result,
        mock_candidate_result,
        mock_tree_result,
    ])

    with patch.object(pg, "get_session", return_value=mock_session):
        result = pg.activate_fault_tree_version(
            version_id="ver-B",
            tree_id="tree-1",
            org_id="org-1",
            failure_modes=[{"title": "modo B"}],
            supersedes_version_id="ver-prev",
        )

    assert result is True
    mock_session.commit.assert_called_once()
    mock_session.rollback.assert_not_called()


# ── B3: primeira geração (supersedes_version_id=None) deve funcionar ──────────

def test_b3_activate_without_prior_active_version_succeeds():
    """
    Primeira geração: supersedes_version_id=None, nenhuma versão ativa existe.
    Deve promover normalmente.
    """
    from src.storage.postgres_client import PostgresClient

    pg = PostgresClient.__new__(PostgresClient)

    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: s
    mock_session.__exit__ = MagicMock(return_value=False)

    # Sem versão ativa prévia (SELECT retorna None)
    mock_select_result = MagicMock()
    mock_select_result.fetchone.return_value = None
    mock_candidate_result = MagicMock()
    mock_candidate_result.rowcount = 1
    mock_tree_result = MagicMock()
    mock_tree_result.rowcount = 1

    mock_session.execute = MagicMock(side_effect=[
        mock_select_result,    # SELECT ativo existente → None
        mock_candidate_result,
        mock_tree_result,
    ])

    with patch.object(pg, "get_session", return_value=mock_session):
        result = pg.activate_fault_tree_version(
            version_id="ver-first",
            tree_id="tree-1",
            org_id="org-1",
            failure_modes=[{"title": "modo 1"}],
            supersedes_version_id=None,
        )

    assert result is True
    mock_session.commit.assert_called_once()


def test_b3_activate_without_prior_aborts_if_concurrent_already_promoted():
    """
    Primeira geração, mas outra geração concorrente já criou uma versão ativa.
    supersedes_version_id=None + versão ativa encontrada → retornar False.
    """
    from src.storage.postgres_client import PostgresClient

    pg = PostgresClient.__new__(PostgresClient)

    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: s
    mock_session.__exit__ = MagicMock(return_value=False)

    # Versão ativa já existe (outra geração ganhou)
    mock_select_result = MagicMock()
    mock_select_result.fetchone.return_value = MagicMock(id="ver-concurrent")

    mock_session.execute = MagicMock(return_value=mock_select_result)

    with patch.object(pg, "get_session", return_value=mock_session):
        result = pg.activate_fault_tree_version(
            version_id="ver-late",
            tree_id="tree-1",
            org_id="org-1",
            failure_modes=[{"title": "modo late"}],
            supersedes_version_id=None,
        )

    assert result is False
    mock_session.rollback.assert_called_once()


# ── B4: get_active_fault_tree_version deve existir ────────────────────────────

def test_b4_get_active_fault_tree_version_exists():
    """
    Antes: método não existe em PostgresClient.
    Após: existe e é usado por create_generation_candidate para capturar snapshot.
    """
    from src.storage.postgres_client import PostgresClient

    assert hasattr(PostgresClient, "get_active_fault_tree_version"), (
        "PostgresClient deve ter get_active_fault_tree_version(tree_id, org_id) "
        "para que create_generation_candidate possa capturar o snapshot da versão ativa"
    )


# ── B5: create_generation_candidate captura supersedes_version_id ─────────────

def test_b5_create_candidate_captures_supersedes_version_id():
    """
    Antes: create_generation_candidate não consulta a versão ativa atual.
    Após: consulta get_active_fault_tree_version e passa supersedes_version_id.
    """
    import src.fault_tree.service as svc

    source = inspect.getsource(svc.create_generation_candidate)
    assert "supersedes_version_id" in source or "get_active_fault_tree_version" in source, (
        "create_generation_candidate deve capturar a versão ativa atual (supersedes_version_id) "
        "para implementar CAS na promoção"
    )
