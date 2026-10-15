from fastapi import BackgroundTasks

from src.auth.jwt_validator import TenantContext
from src.fault_tree import router
from src.fault_tree.schemas import GenerateFaultTreeRequest
from src.fault_tree.service import GenerationDocument


def test_first_equipment_generation_accepts_missing_model(monkeypatch):
    equipment_id = "bca0d293-7e25-4489-832d-e4cf0ba8b42c"
    tree = {
        "id": "tree-1",
        "org_id": "org-1",
        "tenant_id": "tenant-1",
        "equipment_id": equipment_id,
        "model_id": None,
    }
    captured = {}

    # Novo contrato: get_equipment_fault_tree retorna None (árvore não existe ainda)
    monkeypatch.setattr(router.db, "get_equipment_fault_tree", lambda **kwargs: None)

    # list_document_bindings é chamado pelo resolve_generation_documents
    monkeypatch.setattr(
        router.db,
        "list_document_bindings",
        lambda **kwargs: [{
            "id": "binding-1",
            "document_id": "bronze-1",
            "envelope_id": "envelope-1",
            "org_id": "org-1",
            "tenant_id": "tenant-1",
            "target_type": "equipment",
            "target_id": equipment_id,
            "model_id": None,
            "file_name": "manual.pdf",
            "document_status": "processed",
            "document_tenant_id": "tenant-1",
            "relationship": "source",
            "object_path": None,
            "upload_date": None,
            "created_at": None,
        }],
    )

    def ensure_target_tree(**kwargs):
        captured["ensure"] = kwargs
        return tree

    def start_generation(**kwargs):
        captured["start"] = kwargs
        return {"generation_id": "version-1"}

    monkeypatch.setattr(router.fault_tree_service, "ensure_target_tree", ensure_target_tree)
    monkeypatch.setattr(router, "_start_generation", start_generation)
    tenant = TenantContext(
        org_id="org-1",
        tenant_id="tenant-1",
        organization_role="superadmin",
        tenant_role=None,
        sub="user-1",
        raw_attrs={},
    )

    result = router.generate_equipment_fault_tree(
        equipment_id=equipment_id,
        body=GenerateFaultTreeRequest(doc_id="bronze-1", filename="manual.pdf"),
        background_tasks=BackgroundTasks(),
        tenant=tenant,
    )

    assert result == {"generation_id": "version-1"}
    assert captured["ensure"]["model_id"] is None
    assert captured["start"]["tree"]["model_id"] is None
