"""
Testes de regressão para R03/R14/R19 — RAG sem escopo de equipamento.

Bug: IngestAdapter(org_id, tenant_id) sem context_ids → busca todos os
documentos do tenant, incluindo documentos de outros equipamentos.

Correto:
  1. Pipeline resolve os bindings do equipamento e passa document_ids como
     context_ids para _search_ingest_context.
  2. Se nenhum binding existe, RAG context é vazio (sem pesquisa).
  3. Se context_ids for fornecido pelo caller, bindings NÃO são consultados.
  4. _search_ingest_context com context_ids=[] retorna vazio imediatamente.

B1 — RAG usa document_ids dos bindings quando context_ids não fornecido.
B2 — context_ids fornecido pelo caller → bindings não são consultados.
B3 — Sem bindings → RAG context vazio, sem busca.
B4 — _search_ingest_context com lista vazia → retorna vazio sem chamar adapter.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch


# ── B4: _search_ingest_context com lista vazia → vazio imediato ───────────────

def test_r03_b4_empty_context_ids_returns_empty_without_search():
    """
    Se context_ids=[] (nenhum binding), _search_ingest_context não deve chamar
    o adapter (evitando busca não-escoped em todos os documentos do tenant).
    """
    from src.chat.pipeline import _search_ingest_context

    mock_adapter = MagicMock()
    result = _search_ingest_context(mock_adapter, query="falha bomba", context_ids=[])

    mock_adapter.search_text.assert_not_called()
    assert result == {"context_text": "", "sources": [], "filenames": []}, (
        "context_ids=[] deve retornar vazio sem chamar o adapter"
    )


def test_r03_b4_none_context_ids_calls_search():
    """
    context_ids=None (sem restrição) → adapter.search_text deve ser chamado.
    Esse é o comportamento legado; agora o pipeline o previne passando context_ids
    resolvidos, mas a função em si não bloqueia None.
    """
    from src.chat.pipeline import _search_ingest_context

    mock_adapter = MagicMock()
    mock_adapter.search_text.return_value = {"citations": []}
    _search_ingest_context(mock_adapter, query="falha bomba", context_ids=None)

    mock_adapter.search_text.assert_called_once()


# ── B1: pipeline resolve bindings quando context_ids não fornecido ────────────

def _make_mock_store(count=0):
    mock_store = MagicMock()
    mock_store.read_last.return_value = []
    mock_store.count.return_value = count
    return mock_store


def _make_mock_summ():
    mock_summ = MagicMock()
    mock_summ.get.return_value = ("", 0)
    return mock_summ


def test_r03_b1_pipeline_resolves_bindings_for_context_ids():
    """
    Quando context_ids não é fornecido ao pipeline, ele deve consultar
    list_document_bindings e usar os document_ids como context_ids para o RAG.
    """
    import src.chat.pipeline as pipeline

    binding_doc_id = "bronze-doc-abc"
    fake_binding = {"document_id": binding_doc_id, "document_status": "ingested"}

    captured = {}

    def fake_search_ingest(adapter, *, query, context_ids, doc_id_to_filename=None, limit=8):
        captured["context_ids"] = context_ids
        return {"context_text": "", "sources": [], "filenames": []}

    mock_store = _make_mock_store()
    mock_summ = _make_mock_summ()

    with patch.object(pipeline.postgres_client, "list_document_bindings",
                      return_value=[fake_binding]) as mock_bindings, \
         patch.object(pipeline, "_search_ingest_context", side_effect=fake_search_ingest), \
         patch.object(pipeline, "IngestAdapter"), \
         patch.object(pipeline, "build_failure_mode_chat_context",
                      return_value=MagicMock(found=False, text="", sources=[])), \
         patch.object(pipeline.postgres_client, "get_envelope_by_bronze", return_value=None), \
         patch.object(pipeline, "_chat_claude", return_value="resposta ok"), \
         patch.object(pipeline.postgres_client, "get_equipment_fault_tree",
                      return_value=None):

        try:
            pipeline.run_chat_pipeline(
                message="como funciona a bomba?",
                equipment_id="equip-1",
                org_id="org-1",
                tenant_id="tenant-1",
                context_ids=None,  # não fornecido
                chat_store=mock_store,
                summary_store=mock_summ,
            )
        except Exception:
            pass  # não interessa o resultado, apenas o context_ids capturado

    mock_bindings.assert_called_once_with(
        org_id="org-1",
        tenant_id="tenant-1",
        target_type="equipment",
        target_id="equip-1",
    )
    assert captured.get("context_ids") == [binding_doc_id], (
        f"context_ids deve ser ['{binding_doc_id}'], recebeu: {captured.get('context_ids')}"
    )


def test_r03_b2_caller_provided_context_ids_are_limited_to_equipment_bindings():
    """
    Se context_ids for fornecido pelo caller, apenas ids vinculados ao
    equipamento podem chegar ao RAG.
    """
    import src.chat.pipeline as pipeline

    captured = {}

    def fake_search_ingest(adapter, *, query, context_ids, doc_id_to_filename=None, limit=8):
        captured["context_ids"] = context_ids
        return {"context_text": "", "sources": [], "filenames": []}

    mock_store = _make_mock_store()
    mock_summ = _make_mock_summ()

    with patch.object(pipeline.postgres_client, "list_document_bindings",
                      return_value=[{"document_id": "caller-provided-id"}]) as mock_bindings, \
         patch.object(pipeline, "_search_ingest_context", side_effect=fake_search_ingest), \
         patch.object(pipeline, "IngestAdapter"), \
         patch.object(pipeline, "build_failure_mode_chat_context",
                      return_value=MagicMock(found=False, text="", sources=[])), \
         patch.object(pipeline.postgres_client, "get_envelope_by_bronze", return_value=None), \
         patch.object(pipeline, "_chat_claude", return_value="resposta ok"), \
         patch.object(pipeline.postgres_client, "get_equipment_fault_tree",
                      return_value=None):

        try:
            pipeline.run_chat_pipeline(
                message="como funciona a bomba?",
                equipment_id="equip-1",
                org_id="org-1",
                tenant_id="tenant-1",
                context_ids=["caller-provided-id", "unbound-document-id"],
                chat_store=mock_store,
                summary_store=mock_summ,
            )
        except Exception:
            pass

    mock_bindings.assert_called_once()
    assert captured.get("context_ids") == ["caller-provided-id"]


def test_r03_b3_no_bindings_returns_empty_rag():
    """
    Sem bindings, context_ids=[] → _search_ingest_context retorna vazio.
    O pipeline não deve buscar em todos os documentos do tenant.
    """
    import src.chat.pipeline as pipeline

    captured = {}

    def fake_search_ingest(adapter, *, query, context_ids, doc_id_to_filename=None, limit=8):
        captured["context_ids"] = context_ids
        return {"context_text": "", "sources": [], "filenames": []}

    mock_store = _make_mock_store()
    mock_summ = _make_mock_summ()

    with patch.object(pipeline.postgres_client, "list_document_bindings",
                      return_value=[]), \
         patch.object(pipeline, "_search_ingest_context", side_effect=fake_search_ingest), \
         patch.object(pipeline, "IngestAdapter"), \
         patch.object(pipeline, "build_failure_mode_chat_context",
                      return_value=MagicMock(found=False, text="", sources=[])), \
         patch.object(pipeline.postgres_client, "get_envelope_by_bronze", return_value=None), \
         patch.object(pipeline, "_chat_claude", return_value="resposta ok"), \
         patch.object(pipeline.postgres_client, "get_equipment_fault_tree",
                      return_value=None):

        try:
            pipeline.run_chat_pipeline(
                message="como funciona a bomba?",
                equipment_id="equip-1",
                org_id="org-1",
                tenant_id="tenant-1",
                context_ids=None,
                chat_store=mock_store,
                summary_store=mock_summ,
            )
        except Exception:
            pass

    assert captured.get("context_ids") == [], (
        "Sem bindings, context_ids deve ser [] para evitar busca não-escoped. "
        f"Recebeu: {captured.get('context_ids')}"
    )
