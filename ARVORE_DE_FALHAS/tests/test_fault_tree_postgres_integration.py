"""Integração opt-in do versionamento contra PostgreSQL real."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from src.storage.postgres_client import postgres_client as db


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="Defina RUN_POSTGRES_INTEGRATION=1 em um PostgreSQL descartável.",
)


def test_inherited_tree_replacement_is_atomic_and_documents_survive_delete():
    suffix = uuid.uuid4().hex
    org_id = f"org-{suffix}"
    tenant_id = f"tenant-{suffix}"
    model_id = f"model-{suffix}"
    equipment_id = str(uuid.uuid4())
    sibling_equipment_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    model_tree_id = str(uuid.uuid4())
    equipment_tree_id = str(uuid.uuid4())
    sibling_tree_id = str(uuid.uuid4())

    db.create_tables()
    try:
        assert db.insert_bronze_document({
            "id": document_id,
            "org_id": org_id,
            "tenant_id": None,
            "file_name": "modelo.pdf",
            "file_type": "application/pdf",
            "document_hash": suffix,
            "object_path": f"documents/{document_id}.pdf",
            "metadata": "{}",
        })
        assert db.bind_document(
            document_id=document_id,
            envelope_id=None,
            org_id=org_id,
            tenant_id=None,
            target_type="model",
            target_id=model_id,
            model_id=model_id,
        )
        assert db.create_fault_tree({
            "id": model_tree_id,
            "org_id": org_id,
            "model_id": model_id,
            "tree_data": {"titulo": "Template"},
            "failure_modes": [{"title": "Herdada"}],
        })
        assert db.create_fault_tree({
            "id": sibling_tree_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "model_id": model_id,
            "equipment_id": sibling_equipment_id,
            "tree_data": {"titulo": "Equipamento irmão"},
            "failure_modes": [{"title": "Herdada"}],
        })
        assert db.create_fault_tree({
            "id": equipment_tree_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "model_id": model_id,
            "equipment_id": equipment_id,
            "tree_data": {"titulo": "Equipamento"},
            "failure_modes": [{"title": "Herdada"}],
        })
        copied = db.copy_model_document_bindings(
            model_id=model_id,
            equipment_id=equipment_id,
            org_id=org_id,
            tenant_id=tenant_id,
        )
        assert len(copied) == 1
        documents = db.list_document_bindings(
            org_id=org_id,
            tenant_id=tenant_id,
            target_type="equipment",
            target_id=equipment_id,
        )
        assert documents[0]["relationship"] == "inherited"

        inherited = db.create_fault_tree_version({
            "tree_id": equipment_tree_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "model_id": model_id,
            "equipment_id": equipment_id,
            "status": "active",
            "failure_modes": [{"title": "Herdada"}],
        })
        candidate = db.create_fault_tree_version({
            "tree_id": equipment_tree_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "model_id": model_id,
            "equipment_id": equipment_id,
            "document_id": document_id,
            "status": "running",
        })
        assert inherited and candidate
        assert db.activate_fault_tree_version(
            candidate["id"],
            equipment_tree_id,
            org_id,
            [{"title": "Substituída"}],
        )

        versions = db.list_fault_tree_versions(equipment_tree_id, org_id)
        statuses = {version["id"]: version["status"] for version in versions}
        assert statuses[inherited["id"]] == "superseded"
        assert statuses[candidate["id"]] == "active"
        current = db.get_fault_tree_by_equipment(equipment_id, org_id, tenant_id)
        assert current["failure_modes"] == [{"title": "Substituída"}]
        assert db.get_fault_tree_by_model(model_id, org_id)["failure_modes"] == [
            {"title": "Herdada"}
        ]
        assert db.get_fault_tree_by_equipment(
            sibling_equipment_id, org_id, tenant_id
        )["failure_modes"] == [{"title": "Herdada"}]

        assert db.delete_fault_tree(equipment_tree_id, org_id)
        assert db.list_fault_tree_versions(equipment_tree_id, org_id) == []
        assert len(db.list_document_bindings(
            org_id=org_id,
            tenant_id=tenant_id,
            target_type="equipment",
            target_id=equipment_id,
        )) == 1
    finally:
        with db.get_session() as session:
            session.execute(text("DELETE FROM fault_tree_version_documents WHERE origin_id IN (:model_id, :equipment_id)"), {"model_id": model_id, "equipment_id": equipment_id})
            session.execute(text("DELETE FROM fault_tree_versions WHERE org_id = :org_id"), {"org_id": org_id})
            session.execute(text("DELETE FROM fault_trees WHERE org_id = :org_id"), {"org_id": org_id})
            session.execute(text("DELETE FROM document_bindings WHERE org_id = :org_id"), {"org_id": org_id})
            session.execute(text("DELETE FROM bronze_documents WHERE org_id = :org_id"), {"org_id": org_id})
            session.commit()


def test_equipment_without_model_persists_versions_and_is_unique_per_tenant():
    suffix = uuid.uuid4().hex
    org_id = f"org-model-less-{suffix}"
    tenant_id = f"tenant-a-{suffix}"
    other_tenant_id = f"tenant-b-{suffix}"
    equipment_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    tree_id = str(uuid.uuid4())
    other_tree_id = str(uuid.uuid4())

    db.create_tables()
    try:
        assert db.insert_bronze_document({
            "id": document_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "file_name": "equipamento-sem-modelo.pdf",
            "file_type": "application/pdf",
            "document_hash": suffix,
            "object_path": f"documents/{document_id}.pdf",
            "metadata": "{}",
        })
        assert db.bind_document(
            document_id=document_id,
            envelope_id=None,
            org_id=org_id,
            tenant_id=tenant_id,
            target_type="equipment",
            target_id=equipment_id,
            model_id=None,
        )
        assert db.create_fault_tree({
            "id": tree_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "equipment_id": equipment_id,
            "model_id": None,
            "tree_data": {"titulo": "Equipamento sem modelo"},
        })
        version = db.create_fault_tree_version({
            "tree_id": tree_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "equipment_id": equipment_id,
            "document_id": document_id,
            "status": "running",
        })
        assert version is not None
        assert version["model_id"] is None
        assert db.activate_fault_tree_version(
            version["id"], tree_id, org_id, [{"title": "Falha própria"}]
        )

        current = db.get_fault_tree_by_equipment(equipment_id, org_id, tenant_id)
        assert current["model_id"] is None
        assert current["failure_modes"] == [{"title": "Falha própria"}]

        assert not db.create_fault_tree({
            "id": str(uuid.uuid4()),
            "org_id": org_id,
            "tenant_id": tenant_id,
            "equipment_id": equipment_id,
            "model_id": None,
            "tree_data": {"titulo": "Duplicada"},
        })
        assert db.create_fault_tree({
            "id": other_tree_id,
            "org_id": org_id,
            "tenant_id": other_tenant_id,
            "equipment_id": equipment_id,
            "model_id": None,
            "tree_data": {"titulo": "Mesmo UUID em outro tenant"},
        })

        assert db.delete_fault_tree(tree_id, org_id)
        assert db.get_fault_tree_by_equipment(equipment_id, org_id, tenant_id) is None
        assert len(db.list_document_bindings(
            org_id=org_id,
            tenant_id=tenant_id,
            target_type="equipment",
            target_id=equipment_id,
        )) == 1
    finally:
        with db.get_session() as session:
            session.execute(
                text("DELETE FROM fault_tree_version_documents WHERE origin_id = :equipment_id"),
                {"equipment_id": equipment_id},
            )
            session.execute(text("DELETE FROM fault_tree_versions WHERE org_id = :org_id"), {"org_id": org_id})
            session.execute(text("DELETE FROM fault_trees WHERE org_id = :org_id"), {"org_id": org_id})
            session.execute(text("DELETE FROM document_bindings WHERE org_id = :org_id"), {"org_id": org_id})
            session.execute(text("DELETE FROM bronze_documents WHERE org_id = :org_id"), {"org_id": org_id})
            session.commit()
