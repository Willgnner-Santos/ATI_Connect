"""
Testes de regressão para bugs no generator.

R16 — reclassify_existing_failure_modes_to_explicit_taxonomy levantava NameError
       imediatamente porque progress_callback não era parâmetro.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_reclassify_does_not_raise_name_error_without_callback():
    """R16 — chamada sem callback não pode levantar NameError."""
    from src.fault_tree.generator import reclassify_existing_failure_modes_to_explicit_taxonomy

    ingest_client = MagicMock()
    ingest_client.search_text.return_value = []
    ingest_client.get_chunks_by_document.return_value = []

    try:
        reclassify_existing_failure_modes_to_explicit_taxonomy(
            None,
            ingest_client=ingest_client,
            asset_id="asset1",
            doc_id="doc1",
            filename="manual.pdf",
            items=[],
        )
    except NameError as exc:
        pytest.fail(f"R16 não corrigido — NameError: {exc}")
    except Exception:
        # Outras exceções (ex.: ValueError por items vazio) são aceitáveis;
        # o importante é que NameError não ocorra antes da lógica da função.
        pass


def test_reclassify_does_not_raise_name_error_with_callback():
    """R16 — chamada com callback não pode levantar NameError."""
    from src.fault_tree.generator import reclassify_existing_failure_modes_to_explicit_taxonomy

    ingest_client = MagicMock()
    ingest_client.search_text.return_value = []
    ingest_client.get_chunks_by_document.return_value = []

    calls: list[dict] = []

    try:
        reclassify_existing_failure_modes_to_explicit_taxonomy(
            None,
            ingest_client=ingest_client,
            asset_id="asset1",
            doc_id="doc1",
            filename="manual.pdf",
            items=[],
            progress_callback=calls.append,
        )
    except NameError as exc:
        pytest.fail(f"R16 não corrigido — NameError: {exc}")
    except Exception:
        pass

    # O callback deve ter sido chamado pelo menos uma vez (emit do inicio do progresso).
    assert len(calls) >= 1, (
        "R16 — progress_callback deveria ser chamado durante a execução da função."
    )
