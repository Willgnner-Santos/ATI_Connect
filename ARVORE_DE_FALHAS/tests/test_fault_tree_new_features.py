"""Testes para funcionalidades do segundo ciclo de correções:
- resolve_generation_documents
- 422 DOCUMENT_SELECTION_REQUIRED quando há múltiplos manuais
- DocumentBindingResponse.from_binding_row
- _map_generation_error
- storage_scope org-scoped
"""
import pytest
from fastapi import BackgroundTasks, HTTPException

from src.auth.jwt_validator import TenantContext
from src.fault_tree import router
from src.fault_tree import service
from src.fault_tree.schemas import DocumentBindingResponse, GenerateFaultTreeRequest
from src.fault_tree.service import GenerationDocument, resolve_generation_documents


# ── Fixtures helpers ──────────────────────────────────────────────────────────

def _binding(
    doc_id="bronze-1",
    envelope_id="env-1",
    filename="manual.pdf",
    status="processed",
    doc_tenant="tenant-1",
    relationship="source",
):
    return {
        "id": "binding-1",
        "document_id": doc_id,
        "envelope_id": envelope_id,
        "org_id": "org-1",
        "tenant_id": "tenant-1",
        "target_type": "equipment",
        "target_id": "00000000-0000-0000-0000-000000000001",
        "model_id": None,
        "file_name": filename,
        "document_status": status,
        "document_tenant_id": doc_tenant,
        "relationship": relationship,
        "object_path": None,
        "upload_date": None,
        "created_at": None,
    }


def _tenant():
    return TenantContext(
        org_id="org-1",
        tenant_id="tenant-1",
        organization_role="superadmin",
        tenant_role=None,
        sub="user-1",
        raw_attrs={},
    )


# ── resolve_generation_documents ─────────────────────────────────────────────

def test_resolve_returns_generation_document(monkeypatch):
    monkeypatch.setattr(service.db, "list_document_bindings", lambda **kw: [_binding()])
    docs = resolve_generation_documents("org-1", "tenant-1", "equipment", "00000000-0000-0000-0000-000000000001")
    assert len(docs) == 1
    d = docs[0]
    assert d.document_id == "bronze-1"
    assert d.envelope_id == "env-1"
    assert d.filename == "manual.pdf"
    assert d.scope_type == "tenant"
    assert d.storage_tenant_id == "tenant-1"


def test_resolve_org_scoped_doc(monkeypatch):
    """Documento com doc_tenant=None → scope_type='organization'."""
    monkeypatch.setattr(
        service.db, "list_document_bindings",
        lambda **kw: [_binding(doc_tenant=None)],
    )
    docs = resolve_generation_documents("org-1", "tenant-1", "equipment", "00000000-0000-0000-0000-000000000001")
    assert len(docs) == 1
    assert docs[0].scope_type == "organization"
    assert docs[0].storage_tenant_id is None


def test_resolve_skips_failed_docs(monkeypatch):
    monkeypatch.setattr(
        service.db, "list_document_bindings",
        lambda **kw: [_binding(status="failed")],
    )
    docs = resolve_generation_documents("org-1", "tenant-1", "equipment", "00000000-0000-0000-0000-000000000001")
    assert docs == []


def test_resolve_filters_by_document_id(monkeypatch):
    monkeypatch.setattr(
        service.db, "list_document_bindings",
        lambda **kw: [_binding(doc_id="bronze-1"), _binding(doc_id="bronze-2")],
    )
    docs = resolve_generation_documents(
        "org-1", "tenant-1", "equipment", "00000000-0000-0000-0000-000000000001", document_id="bronze-2"
    )
    assert len(docs) == 1
    assert docs[0].document_id == "bronze-2"


def test_resolve_falls_back_to_envelope_when_missing(monkeypatch):
    b = _binding(envelope_id=None, filename="")
    monkeypatch.setattr(service.db, "list_document_bindings", lambda **kw: [b])
    monkeypatch.setattr(
        service.db, "get_envelope_by_bronze_any_tenant",
        lambda bronze_id, org_id: {"id": "env-resolved", "file_name": "resolved.pdf"},
    )
    docs = resolve_generation_documents("org-1", "tenant-1", "equipment", "00000000-0000-0000-0000-000000000001")
    assert docs[0].envelope_id == "env-resolved"
    assert docs[0].filename == "resolved.pdf"


# ── 422 para múltiplos documentos sem document_id ────────────────────────────

def test_generate_multiple_docs_without_selection_returns_422(monkeypatch):
    """Quando há 2 documentos e nenhum document_id é fornecido, deve retornar 422."""
    tree = {
        "id": "tree-1", "org_id": "org-1", "tenant_id": "tenant-1",
        "model_id": None, "equipment_id": "00000000-0000-0000-0000-000000000001",
        "generation_status": None,
    }

    monkeypatch.setattr(router.db, "get_equipment_fault_tree", lambda **kw: tree)
    monkeypatch.setattr(
        router.db, "list_document_bindings",
        lambda **kw: [
            _binding(doc_id="bronze-1"),
            {**_binding(doc_id="bronze-2"), "id": "binding-2"},
        ],
    )
    monkeypatch.setattr(router.fault_tree_service, "ensure_target_tree", lambda **kw: tree)

    with pytest.raises(HTTPException) as exc_info:
        router.generate_equipment_fault_tree(
            equipment_id="00000000-0000-0000-0000-000000000001",
            body=GenerateFaultTreeRequest(),
            background_tasks=BackgroundTasks(),
            tenant=_tenant(),
        )

    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["error_code"] == "DOCUMENT_SELECTION_REQUIRED"
    assert len(detail["available_documents"]) == 2


def test_generate_single_doc_without_document_id_proceeds(monkeypatch):
    """Com apenas 1 documento, gera sem precisar de document_id."""
    tree = {
        "id": "tree-1", "org_id": "org-1", "tenant_id": "tenant-1",
        "model_id": None, "equipment_id": "00000000-0000-0000-0000-000000000001",
        "generation_status": None,
    }
    monkeypatch.setattr(router.db, "get_equipment_fault_tree", lambda **kw: tree)
    monkeypatch.setattr(
        router.db, "list_document_bindings",
        lambda **kw: [_binding()],
    )
    monkeypatch.setattr(router.fault_tree_service, "ensure_target_tree", lambda **kw: tree)
    captured = {}

    def start_generation(**kwargs):
        captured["called"] = True
        return {"generation_id": "v-1"}

    monkeypatch.setattr(router, "_start_generation", start_generation)

    result = router.generate_equipment_fault_tree(
        equipment_id="00000000-0000-0000-0000-000000000001",
        body=GenerateFaultTreeRequest(),
        background_tasks=BackgroundTasks(),
        tenant=_tenant(),
    )
    assert result == {"generation_id": "v-1"}
    assert captured.get("called")


def test_generate_with_document_id_bypasses_selection_check(monkeypatch):
    """document_id explícito não exige seleção mesmo com múltiplos bindings."""
    tree = {
        "id": "tree-1", "org_id": "org-1", "tenant_id": "tenant-1",
        "model_id": None, "equipment_id": "00000000-0000-0000-0000-000000000001",
        "generation_status": None,
    }
    monkeypatch.setattr(router.db, "get_equipment_fault_tree", lambda **kw: tree)
    monkeypatch.setattr(
        router.db, "list_document_bindings",
        lambda **kw: [_binding(doc_id="bronze-1"), {**_binding(doc_id="bronze-2"), "id": "b2"}],
    )
    monkeypatch.setattr(router.fault_tree_service, "ensure_target_tree", lambda **kw: tree)
    captured = {}

    def start_generation(**kwargs):
        captured["called"] = True
        return {"generation_id": "v-1"}

    monkeypatch.setattr(router, "_start_generation", start_generation)

    result = router.generate_equipment_fault_tree(
        equipment_id="00000000-0000-0000-0000-000000000001",
        body=GenerateFaultTreeRequest(document_id="bronze-1"),
        background_tasks=BackgroundTasks(),
        tenant=_tenant(),
    )
    assert result == {"generation_id": "v-1"}


# ── DocumentBindingResponse.from_binding_row ─────────────────────────────────

def test_document_binding_response_from_row_tenant_scope():
    row = _binding()
    resp = DocumentBindingResponse.from_binding_row(row)
    assert resp.document_id == "bronze-1"
    assert resp.filename == "manual.pdf"
    assert resp.scope == "tenant"
    assert resp.relationship == "source"


def test_document_binding_response_from_row_org_scope():
    row = _binding(doc_tenant=None)
    resp = DocumentBindingResponse.from_binding_row(row)
    assert resp.scope == "organization"


def test_document_binding_response_status_mapping():
    for status_val in ("ingested", "processed", "completed"):
        row = _binding(status=status_val)
        resp = DocumentBindingResponse.from_binding_row(row)
        assert resp.status == "ready"

    row = _binding(status="failed")
    resp = DocumentBindingResponse.from_binding_row(row)
    assert resp.status == "failed"


# ── _map_generation_error ─────────────────────────────────────────────────────

def test_map_bedrock_error():
    exc = RuntimeError("bedrock throttling limit exceeded")
    code, msg = router._map_generation_error(exc)
    assert code == "GENERATION_SERVICE_UNAVAILABLE"
    assert len(msg) > 0


def test_map_qdrant_error():
    exc = ConnectionError("qdrant connection refused")
    code, _ = router._map_generation_error(exc)
    assert code == "RETRIEVAL_ERROR"


def test_map_unknown_error_fallback():
    exc = RuntimeError("something completely unknown happened")
    code, _ = router._map_generation_error(exc)
    assert code == "GENERATION_FAILED"


# ── storage_scope wiring in service ──────────────────────────────────────────

def test_run_generation_uses_org_scope_for_org_docs(monkeypatch):
    """Documentos com scope_type='organization' devem usar tenant=None no Qdrant."""
    tree = {
        "id": "tree-1", "org_id": "org-1", "tenant_id": "tenant-1",
        "model_id": "model-1", "equipment_id": "00000000-0000-0000-0000-000000000001",
    }
    version = {"id": "version-1"}
    captured = {}

    import src.fault_tree.generator as generator

    def fake_generate(**kwargs):
        captured["storage_tenant_id"] = kwargs.get("storage_tenant_id")
        return [{"title": "Falha org"}]

    monkeypatch.setattr(generator, "generate_failure_modes", fake_generate)
    monkeypatch.setattr(service.db, "update_fault_tree_version_status", lambda *a, **kw: True)
    monkeypatch.setattr(service.db, "update_fault_tree_generation_status", lambda *a, **kw: True)
    monkeypatch.setattr(service.db, "activate_fault_tree_version", lambda *a, **kw: True)
    monkeypatch.setattr(service.db, "update_fault_tree_version_progress", lambda **kw: None)

    service.run_generation_candidate(
        tree=tree,
        version=version,
        document_id="bronze-org",
        file_name="org-manual.pdf",
        envelope_id="env-org",
        storage_tenant_id=None,
        storage_scope="organization",
    )

    from src.fault_tree.generator import _STORAGE_TENANT_UNSET
    # org-scope deve passar None explícito — não o sentinel
    assert captured["storage_tenant_id"] is None
    assert captured["storage_tenant_id"] is not _STORAGE_TENANT_UNSET


def test_run_generation_default_scope_uses_sentinel(monkeypatch):
    """Sem escopo explícito, generator deve receber sentinel para usar equipment tenant."""
    tree = {
        "id": "tree-1", "org_id": "org-1", "tenant_id": "tenant-A",
        "model_id": None, "equipment_id": "00000000-0000-0000-0000-000000000001",
    }
    version = {"id": "version-1"}
    captured = {}

    import src.fault_tree.generator as generator
    from src.fault_tree.generator import _STORAGE_TENANT_UNSET

    def fake_generate(**kwargs):
        captured["storage_tenant_id"] = kwargs.get("storage_tenant_id")
        return [{"title": "Falha tenant"}]

    monkeypatch.setattr(generator, "generate_failure_modes", fake_generate)
    monkeypatch.setattr(service.db, "update_fault_tree_version_status", lambda *a, **kw: True)
    monkeypatch.setattr(service.db, "update_fault_tree_generation_status", lambda *a, **kw: True)
    monkeypatch.setattr(service.db, "activate_fault_tree_version", lambda *a, **kw: True)
    monkeypatch.setattr(service.db, "update_fault_tree_version_progress", lambda **kw: None)

    service.run_generation_candidate(
        tree=tree,
        version=version,
        document_id="bronze-1",
        file_name="manual.pdf",
        envelope_id="env-1",
        # storage_tenant_id not provided → default None → should use sentinel
        storage_scope="tenant",
    )

    assert captured["storage_tenant_id"] is _STORAGE_TENANT_UNSET
