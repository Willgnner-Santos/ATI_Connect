from src.storage.postgres_client import postgres_client


def test_storage_columns_use_vendor_neutral_name():
    for table_name in (
        "bronze_documents",
        "silver_documents",
        "gold_analytics",
        "document_images",
    ):
        columns = postgres_client.metadata.tables[table_name].columns
        assert "object_path" in columns
        assert "minio_path" not in columns


def test_fault_tree_versioning_tables_are_declared():
    tables = postgres_client.metadata.tables
    assert "fault_tree_versions" in tables
    assert "fault_tree_version_documents" in tables

    version_columns = tables["fault_tree_versions"].columns
    for column in ("tree_id", "document_id", "status", "failure_modes", "activated_at"):
        assert column in version_columns


def test_equipment_fault_tree_model_is_optional_and_scope_is_unique():
    trees = postgres_client.metadata.tables["fault_trees"]
    versions = postgres_client.metadata.tables["fault_tree_versions"]

    assert trees.c.model_id.nullable is True
    assert versions.c.model_id.nullable is True
    assert "uq_fault_tree_model_scope" in {index.name for index in trees.indexes}
    assert "uq_fault_tree_equipment_scope" in {index.name for index in trees.indexes}
