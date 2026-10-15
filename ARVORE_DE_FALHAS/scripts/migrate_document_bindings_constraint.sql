-- R13: Corrigir constraint de document_bindings para incluir tenant_id.
--
-- Problema original: a constraint (document_id, target_type, target_id) sem
-- escopo de org/tenant permitia colisão cross-org e cross-tenant.
-- A correção da fase anterior adicionou org_id mas OMITIU tenant_id, o que
-- ainda permite que Tenant A e Tenant B da mesma org colidam pelo mesmo binding.
--
-- Esta migration:
--   1. Diagnostica duplicatas no escopo correto (org_id + tenant_id).
--   2. Salva registro dos conflitos antes de qualquer deleção.
--   3. Remove apenas duplicatas verdadeiras (mesmo org + tenant + doc + target).
--   4. Troca a constraint antiga pela nova (com tenant_id, NULLS NOT DISTINCT).
--   5. Valida que não restam violações.
--
-- Pré-requisito: PostgreSQL >= 15 (suporte a UNIQUE NULLS NOT DISTINCT).
-- Rollback: ver seção "-- ROLLBACK" ao final.

BEGIN;

-- ── Passo 1: Diagnóstico ──────────────────────────────────────────────────────
-- Conta duplicatas no escopo da nova constraint.
-- Antes de executar a migration, rode este SELECT para inspecionar o impacto.
-- Uma linha por grupo com count > 1 indica linhas que serão removidas.

/*  DIAGNÓSTICO (execute manualmente antes do BEGIN):

    SELECT document_id, target_type, target_id, org_id, tenant_id,
           COUNT(*) AS total,
           MIN(created_at) AS oldest,
           MAX(created_at) AS newest
    FROM document_bindings
    GROUP BY document_id, target_type, target_id, org_id, tenant_id
    HAVING COUNT(*) > 1;

*/

-- ── Passo 2: Backup dos registros afetados ────────────────────────────────────
-- Cria tabela de backup com as linhas que serão removidas.
-- Permite auditoria e eventual restauração manual.

CREATE TABLE IF NOT EXISTS document_bindings_migration_backup AS
SELECT db.*
FROM document_bindings db
WHERE db.id NOT IN (
    SELECT DISTINCT ON (document_id, target_type, target_id, org_id, tenant_id)
        id
    FROM document_bindings
    ORDER BY document_id, target_type, target_id, org_id, tenant_id, created_at DESC
);

-- ── Passo 3: Remover duplicatas no escopo correto (org_id + tenant_id) ────────
-- Mantém somente o registro mais recente por
-- (document_id, target_type, target_id, org_id, tenant_id).
-- Bindings de tenants diferentes dentro da mesma org NÃO são removidos.

DELETE FROM document_bindings
WHERE id NOT IN (
    SELECT DISTINCT ON (document_id, target_type, target_id, org_id, tenant_id)
        id
    FROM document_bindings
    ORDER BY document_id, target_type, target_id, org_id, tenant_id, created_at DESC
);

-- ── Passo 4: Remover constraints anteriores ───────────────────────────────────

ALTER TABLE document_bindings
    DROP CONSTRAINT IF EXISTS uq_document_binding_target;

ALTER TABLE document_bindings
    DROP CONSTRAINT IF EXISTS uq_document_binding_org_target;

-- ── Passo 5: Criar nova constraint (inclui tenant_id, NULLS NOT DISTINCT) ─────
-- NULLS NOT DISTINCT: dois bindings com tenant_id = NULL (modelo global) e
-- mesmo (document_id, target_type, target_id, org_id) colidem corretamente.
-- Bindings de tenants distintos na mesma org não colidem.

ALTER TABLE document_bindings
    ADD CONSTRAINT uq_document_binding_tenant_scoped
    UNIQUE NULLS NOT DISTINCT (document_id, target_type, target_id, org_id, tenant_id);

-- ── Passo 6: Validação pós-migration ─────────────────────────────────────────
-- Deve retornar 0 linhas. Se retornar alguma linha, ROLLBACK imediatamente.

DO $$
DECLARE
    violation_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO violation_count
    FROM (
        SELECT document_id, target_type, target_id, org_id, tenant_id, COUNT(*)
        FROM document_bindings
        GROUP BY document_id, target_type, target_id, org_id, tenant_id
        HAVING COUNT(*) > 1
    ) violations;

    IF violation_count > 0 THEN
        RAISE EXCEPTION
            'R13 migration: % grupos de duplicatas ainda presentes após deduplicação. ROLLBACK.',
            violation_count;
    END IF;
END;
$$;

COMMIT;

-- ── ROLLBACK ──────────────────────────────────────────────────────────────────
-- Para reverter esta migration (execute em transação separada):
--
-- BEGIN;
-- ALTER TABLE document_bindings
--     DROP CONSTRAINT IF EXISTS uq_document_binding_tenant_scoped;
-- ALTER TABLE document_bindings
--     ADD CONSTRAINT uq_document_binding_org_target
--     UNIQUE (document_id, target_type, target_id, org_id);
-- -- Restaurar bindings removidos (se necessário):
-- INSERT INTO document_bindings
-- SELECT * FROM document_bindings_migration_backup
-- ON CONFLICT DO NOTHING;
-- COMMIT;
--
-- DROP TABLE IF EXISTS document_bindings_migration_backup;  -- após confirmação
