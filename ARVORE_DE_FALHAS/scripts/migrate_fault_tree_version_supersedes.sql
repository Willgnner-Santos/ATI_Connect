-- Migração: adiciona supersedes_version_id em fault_tree_versions (R10 — CAS anti-stale)
--
-- Propósito: ao criar um candidato de geração, registra qual versão estava 'active'
-- naquele momento. activate_fault_tree_version usa esse id para implementar
-- Compare-And-Swap: só supersede essa versão específica. Se ela já foi superseded
-- por uma geração concorrente mais rápida, rowcount=0 → rollback → stale descartado.
--
-- A tabela fault_tree_versions.id usa VARCHAR; manter o mesmo tipo na FK.
-- Rollback: DROP COLUMN supersedes_version_id (ver comentário abaixo)

BEGIN;

ALTER TABLE fault_tree_versions
    ADD COLUMN IF NOT EXISTS supersedes_version_id VARCHAR
        REFERENCES fault_tree_versions(id) ON DELETE SET NULL;

-- Índice para lookup rápido em activate_fault_tree_version
CREATE INDEX IF NOT EXISTS idx_ftv_supersedes
    ON fault_tree_versions(supersedes_version_id)
    WHERE supersedes_version_id IS NOT NULL;

-- Rollback:
-- ALTER TABLE fault_tree_versions DROP COLUMN IF EXISTS supersedes_version_id;
-- DROP INDEX IF EXISTS idx_ftv_supersedes;

COMMIT;
