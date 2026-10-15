import pytest

from src.fault_tree import service


def _install_version_fakes(monkeypatch, state):
    def create_version(data):
        version = {"id": f"version-{len(state['versions']) + 1}", **data}
        state["versions"].append(version)
        return version

    monkeypatch.setattr(service.db, "create_fault_tree_version", create_version)
    monkeypatch.setattr(service.db, "link_version_document", lambda **kwargs: True)
    monkeypatch.setattr(
        service.db, "update_fault_tree_generation_status", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        service.db, "update_fault_tree_version_status", lambda *args, **kwargs: True
    )

    def activate(version_id, tree_id, org_id, items, supersedes_version_id=None):
        state["active_modes"] = items
        state["active_version_id"] = version_id
        return True

    monkeypatch.setattr(service.db, "activate_fault_tree_version", activate)
    monkeypatch.setattr(service.db, "get_active_fault_tree_version", lambda *args, **kwargs: None)


def test_equipment_without_model_is_created_generated_and_persisted(monkeypatch):
    state = {
        "tree": None,
        "binding": None,
        "versions": [],
        "active_modes": None,
        "active_version_id": None,
    }

    def bind_document(**kwargs):
        state["binding"] = kwargs
        return True

    def create_tree(data):
        state["tree"] = {**data, "generation_status": None, "failure_modes": None}
        return True

    monkeypatch.setattr(service.db, "bind_document", bind_document)
    monkeypatch.setattr(
        service.db,
        "get_fault_tree_by_equipment",
        lambda equipment_id, org_id, tenant_id=None: state["tree"],
    )
    monkeypatch.setattr(service.db, "create_fault_tree", create_tree)
    _install_version_fakes(monkeypatch, state)

    import src.fault_tree.generator as generator

    monkeypatch.setattr(
        generator, "generate_failure_modes", lambda **kwargs: [{"title": "Falha própria"}]
    )

    result = service.process_ingest_target(
        pipeline_result={"bronze_id": "bronze-1", "envelope_id": "envelope-1"},
        org_id="org-1",
        tenant_id="tenant-1",
        target_type="equipment",
        target_id="equipment-1",
        model_id=None,
        file_name="manual.pdf",
        generate_fault_tree=True,
    )

    assert state["tree"]["equipment_id"] == "equipment-1"
    assert state["tree"]["tenant_id"] == "tenant-1"
    assert state["tree"]["model_id"] is None
    assert state["binding"]["target_type"] == "equipment"
    assert state["binding"]["model_id"] is None
    assert state["versions"][0]["model_id"] is None
    assert state["active_modes"] == [{"title": "Falha própria"}]
    assert result["generation_id"] == "version-1"
    assert result["generation_status"] == "completed"


def test_replacement_failure_keeps_previous_active_modes(monkeypatch):
    previous = [{"title": "Versão herdada"}]
    state = {
        "tree": {
            "id": "tree-1",
            "org_id": "org-1",
            "tenant_id": "tenant-1",
            "model_id": "model-1",
            "equipment_id": "equipment-1",
            "failure_modes": previous,
        },
        "versions": [],
        "active_modes": previous,
        "active_version_id": "inherited-version",
    }
    _install_version_fakes(monkeypatch, state)

    import src.fault_tree.generator as generator

    def fail_generation(**kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(generator, "generate_failure_modes", fail_generation)
    candidate = service.create_generation_candidate(
        tree=state["tree"],
        document_id="bronze-2",
        envelope_id="envelope-2",
        file_name="novo.pdf",
        origin_type="equipment",
        origin_id="equipment-1",
    )

    with pytest.raises(RuntimeError, match="falha simulada"):
        service.run_generation_candidate(
            tree=state["tree"],
            version=candidate,
            document_id="bronze-2",
            file_name="novo.pdf",
            envelope_id="envelope-2",
        )

    assert state["active_modes"] == previous
    assert state["active_version_id"] == "inherited-version"


def test_equipment_ingest_requires_real_tenant(monkeypatch):
    monkeypatch.setattr(service.db, "bind_document", lambda **kwargs: True)

    with pytest.raises(ValueError, match="tenant_id"):
        service.process_ingest_target(
            pipeline_result={"bronze_id": "bronze-1"},
            org_id="org-1",
            tenant_id=None,
            target_type="equipment",
            target_id="equipment-1",
            model_id="model-1",
            file_name="manual.pdf",
            generate_fault_tree=False,
        )


def test_existing_equipment_cannot_change_model(monkeypatch):
    monkeypatch.setattr(
        service.db,
        "get_fault_tree_by_equipment",
        lambda *args, **kwargs: {"id": "tree-1", "model_id": "model-original"},
    )

    with pytest.raises(ValueError, match="model_id"):
        service.ensure_target_tree(
            org_id="org-1",
            tenant_id="tenant-1",
            target_type="equipment",
            target_id="equipment-1",
            model_id="model-diferente",
        )


def test_existing_equipment_accepts_omitted_model(monkeypatch):
    existing = {"id": "tree-1", "model_id": "model-original"}
    monkeypatch.setattr(
        service.db,
        "get_fault_tree_by_equipment",
        lambda *args, **kwargs: existing,
    )

    assert service.ensure_target_tree(
        org_id="org-1",
        tenant_id="tenant-1",
        target_type="equipment",
        target_id="equipment-1",
        model_id=None,
    ) is existing
