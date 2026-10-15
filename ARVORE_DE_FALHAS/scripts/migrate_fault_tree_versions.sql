BEGIN;

CREATE TABLE IF NOT EXISTS fault_tree_versions (
    id VARCHAR PRIMARY KEY,
    tree_id VARCHAR NOT NULL,
    org_id VARCHAR NOT NULL,
    tenant_id VARCHAR NULL,
    model_id VARCHAR NOT NULL,
    equipment_id VARCHAR NULL,
    document_id VARCHAR NULL,
    envelope_id VARCHAR NULL,
    file_name VARCHAR NULL,
    status VARCHAR NOT NULL DEFAULT 'queued',
    failure_modes TEXT NULL,
    error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS ix_fault_tree_versions_tree_created
    ON fault_tree_versions (tree_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_fault_tree_versions_stale
    ON fault_tree_versions (status, updated_at);

CREATE TABLE IF NOT EXISTS fault_tree_version_documents (
    id VARCHAR PRIMARY KEY,
    version_id VARCHAR NOT NULL,
    document_id VARCHAR NOT NULL,
    envelope_id VARCHAR NULL,
    relationship VARCHAR NOT NULL DEFAULT 'source',
    origin_type VARCHAR NOT NULL,
    origin_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fault_tree_version_document
        UNIQUE (version_id, document_id, relationship)
);

CREATE INDEX IF NOT EXISTS ix_fault_tree_version_documents_version
    ON fault_tree_version_documents (version_id);

DO $$
BEGIN
    IF to_regclass('public.ingest_jobs') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS ix_ingest_jobs_stale
            ON ingest_jobs (status, updated_at);
    END IF;
END
$$;

COMMIT;
