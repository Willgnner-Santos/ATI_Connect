BEGIN;

ALTER TABLE fault_trees
    ALTER COLUMN model_id DROP NOT NULL;

ALTER TABLE fault_tree_versions
    ALTER COLUMN model_id DROP NOT NULL;

ALTER TABLE fault_trees
    DROP CONSTRAINT IF EXISTS uq_fault_tree_org_model_equipment;

CREATE UNIQUE INDEX IF NOT EXISTS uq_fault_tree_model_scope
    ON fault_trees (org_id, model_id)
    WHERE equipment_id IS NULL AND model_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_fault_tree_equipment_scope
    ON fault_trees (org_id, tenant_id, equipment_id)
    WHERE equipment_id IS NOT NULL;

COMMIT;
