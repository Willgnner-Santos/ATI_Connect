BEGIN;

-- Documentos de modelo pertencem à organização. PLATFORM era apenas uma
-- sentinela do provedor de identidade e nunca um tenant real.
ALTER TABLE bronze_documents ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE silver_documents ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE gold_analytics ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE envelope_documents ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE document_images ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE image_links ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE image_cluster_runs ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE image_clusters ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE processing_logs ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE event_outbox ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE ingest_jobs ALTER COLUMN tenant_id DROP NOT NULL;

UPDATE bronze_documents SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE silver_documents SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE gold_analytics SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE envelope_documents SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE document_images SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE image_links SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE image_cluster_runs SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE image_clusters SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE processing_logs SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE event_outbox SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';
UPDATE ingest_jobs SET tenant_id = NULL WHERE UPPER(tenant_id) = 'PLATFORM';

CREATE UNIQUE INDEX IF NOT EXISTS uq_bronze_scope_hash
ON bronze_documents (org_id, COALESCE(tenant_id, ''), document_hash);

COMMIT;
