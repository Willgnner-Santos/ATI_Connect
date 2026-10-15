"""
Testes de regressão para R04 — Resultado vazio de geração pode ser promovido.

Garante que:
- run_generation_candidate NÃO chama activate_fault_tree_version quando items=[].
- O status da versão é marcado como "empty" (não "failed") quando itens estão vazios.
- O status da árvore é marcado como "empty" correspondentemente.
- run_generation_candidate com itens não-vazios continua chamando activate (sem regressão).
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


def _make_tree(tree_id: str = "tree-1", org_id: str = "org-1") -> dict:
    return {
        "id": tree_id,
        "org_id": org_id,
        "tenant_id": "tenant-1",
        "failure_modes": [{"title": "Modo existente"}],
    }


def _make_version(version_id: str = "ver-1", org_id: str = "org-1") -> dict:
    return {"id": version_id, "org_id": org_id}


def _run(tree, version, items, raises_activate=False):
    """Executa run_generation_candidate com generate_failure_modes mockado."""
    from src.fault_tree import service

    mock_db = MagicMock()
    mock_db.update_fault_tree_version_status.return_value = True
    mock_db.update_fault_tree_generation_status.return_value = True
    if raises_activate:
        mock_db.activate_fault_tree_version.return_value = False
    else:
        mock_db.activate_fault_tree_version.return_value = True

    with (
        patch.object(service, "db", mock_db),
        patch(
            "src.fault_tree.generator.generate_failure_modes", return_value=items
        ),
    ):
        try:
            result = service.run_generation_candidate(
                tree=tree,
                version=version,
                document_id="doc-1",
                file_name="manual.pdf",
                envelope_id=None,
            )
        except RuntimeError as exc:
            return mock_db, None, str(exc)
        return mock_db, result, None


# ── Resultado vazio: não promover ─────────────────────────────────────────────


def test_empty_result_does_not_call_activate():
    """R04 — resultado vazio (items=[]) não deve chamar activate_fault_tree_version."""
    mock_db, result, error = _run(_make_tree(), _make_version(), items=[])

    assert result is None, "run_generation_candidate deveria ter levantado RuntimeError"
    assert error is not None
    mock_db.activate_fault_tree_version.assert_not_called(), (
        "R04 — activate_fault_tree_version não deve ser chamado com resultado vazio."
    )


def test_empty_result_sets_version_status_to_empty():
    """R04 — resultado vazio deve marcar a versão como 'empty', não 'failed'."""
    mock_db, _, _ = _run(_make_tree(), _make_version(), items=[])

    calls = [str(c) for c in mock_db.update_fault_tree_version_status.call_args_list]
    assert any("empty" in c for c in calls), (
        f"R04 — status da versão deve ser 'empty' quando resultado é vazio. "
        f"Chamadas: {calls}"
    )


def test_empty_result_sets_generation_status_to_empty():
    """R04 — resultado vazio deve marcar generation_status da árvore como 'empty'."""
    mock_db, _, _ = _run(_make_tree(), _make_version(), items=[])

    calls = [str(c) for c in mock_db.update_fault_tree_generation_status.call_args_list]
    assert any("empty" in c for c in calls), (
        f"R04 — generation_status deve ser 'empty' quando resultado é vazio. "
        f"Chamadas: {calls}"
    )


def test_empty_result_raises_runtime_error():
    """R04 — resultado vazio deve levantar RuntimeError."""
    _, result, error = _run(_make_tree(), _make_version(), items=[])

    assert error is not None, "R04 — RuntimeError esperado para resultado vazio"
    assert result is None


def test_empty_result_final_version_status_is_empty_not_failed():
    """R04 — o estado FINAL da versão deve ser 'empty', não 'failed'."""
    mock_db, _, _ = _run(_make_tree(), _make_version(), items=[])

    version_calls = mock_db.update_fault_tree_version_status.call_args_list
    last_version_call = str(version_calls[-1])
    assert "empty" in last_version_call, (
        f"R04 — último update_fault_tree_version_status deve ser 'empty'. "
        f"Obtido: {last_version_call}"
    )
    assert "failed" not in last_version_call, (
        f"R04 — EmptyGenerationError não deve cair no except genérico e sobrescrever "
        f"'empty' por 'failed'. Obtido: {last_version_call}"
    )


def test_empty_result_final_generation_status_is_empty_not_failed():
    """R04 — o estado FINAL da geração deve ser 'empty', não 'failed'."""
    mock_db, _, _ = _run(_make_tree(), _make_version(), items=[])

    gen_calls = mock_db.update_fault_tree_generation_status.call_args_list
    last_gen_call = str(gen_calls[-1])
    assert "empty" in last_gen_call, (
        f"R04 — último update_fault_tree_generation_status deve ser 'empty'. "
        f"Obtido: {last_gen_call}"
    )
    assert "failed" not in last_gen_call, (
        f"R04 — EmptyGenerationError não deve cair no except genérico e sobrescrever "
        f"'empty' por 'failed'. Obtido: {last_gen_call}"
    )


# ── Resultado não-vazio: continua funcionando ─────────────────────────────────


def test_non_empty_result_calls_activate():
    """R04 — resultado não-vazio deve continuar chamando activate_fault_tree_version."""
    items = [{"title": "Rolamento", "description": "Falha de rolamento"}]
    mock_db, result, error = _run(_make_tree(), _make_version(), items=items)

    assert result == items, "R04 — resultado não-vazio deve ser retornado"
    assert error is None
    mock_db.activate_fault_tree_version.assert_called_once(), (
        "R04 — activate_fault_tree_version deve ser chamado com resultado não-vazio."
    )


def test_non_empty_result_activate_receives_items():
    """R04 — activate_fault_tree_version deve receber os itens gerados."""
    items = [{"title": "Cavitação", "description": "Perda de pressão"}]
    mock_db, _, _ = _run(_make_tree(), _make_version(), items=items)

    activate_call = mock_db.activate_fault_tree_version.call_args
    assert activate_call is not None
    passed_items = activate_call.args[3] if len(activate_call.args) >= 4 else activate_call.kwargs.get("failure_modes")
    assert passed_items == items, (
        f"R04 — activate_fault_tree_version deve receber os itens gerados: {items}. "
        f"Recebeu: {passed_items}"
    )
