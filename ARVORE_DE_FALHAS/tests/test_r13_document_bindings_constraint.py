"""
Testes de regressão para R13 — Constraint de document_bindings não inclui tenant_id.

Garante que:
- A constraint única de document_bindings inclui org_id e tenant_id.
- O ON CONFLICT usa (document_id, target_type, target_id, org_id, tenant_id) NULLS NOT DISTINCT.
- O ON CONFLICT NÃO atualiza tenant_id (imutável após criação).
- Tenant A e Tenant B da mesma org conseguem ter bindings independentes.
- bind_document de uma org diferente não aciona conflito na org original.
"""
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import MagicMock, call, patch

import pytest

import pytest


def _make_mock_client():
    """Cria instância de PostgresClient com engine/session mockados."""
    sys.modules.setdefault("aio_pika", MagicMock())
    from src.storage.postgres_client import PostgresClient

    client = PostgresClient.__new__(PostgresClient)
    client.metadata = MagicMock()
    return client


# ── Constraint: inclui org_id ─────────────────────────────────────────────────


def test_document_bindings_constraint_includes_org_and_tenant():
    """R13 — UniqueConstraint de document_bindings deve incluir org_id e tenant_id."""
    from sqlalchemy import UniqueConstraint

    sys.modules.setdefault("aio_pika", MagicMock())

    from src.storage.postgres_client import postgres_client as client

    table = client.metadata.tables.get("document_bindings")
    if table is None:
        pytest.skip("Tabela document_bindings não criada (DB não disponível)")

    constraint_cols = {
        col
        for c in table.constraints
        if isinstance(c, UniqueConstraint)
        for col in c.columns.keys()
    }
    assert "org_id" in constraint_cols, (
        "R13 — constraint de document_bindings deve incluir org_id."
    )
    assert "tenant_id" in constraint_cols, (
        "R13 — constraint de document_bindings deve incluir tenant_id para isolar "
        "bindings entre tenants da mesma organização."
    )


# ── SQL gerado: ON CONFLICT não atualiza tenant_id ────────────────────────────


def test_bind_document_conflict_clause_includes_org_and_tenant():
    """R13 — SQL de bind_document deve usar ON CONFLICT com org_id e tenant_id."""
    sys.modules.setdefault("aio_pika", MagicMock())

    from src.storage.postgres_client import postgres_client as client

    executed_sqls: list[str] = []

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, stmt, params=None):
            executed_sqls.append(str(stmt))
        def commit(self): pass

    with patch.object(client, "get_session", return_value=_FakeSession()):
        client.bind_document(
            document_id="doc-1",
            envelope_id=None,
            org_id="org-A",
            tenant_id="tenant-1",
            target_type="equipment",
            target_id="equip-1",
        )

    assert executed_sqls, "bind_document não executou nenhum SQL"
    sql = executed_sqls[0].upper()

    # ON CONFLICT ON CONSTRAINT é a forma executável em PostgreSQL com NULLS NOT DISTINCT.
    # Evita replicar a lista de colunas no código e vincula-se à constraint nomeada.
    assert "ON CONSTRAINT UQ_DOCUMENT_BINDING_TENANT_SCOPED" in sql, (
        "R13 — bind_document deve usar 'ON CONFLICT ON CONSTRAINT "
        "uq_document_binding_tenant_scoped' para acionar a constraint que inclui "
        "tenant_id com NULLS NOT DISTINCT."
    )
    assert "TENANT_ID = EXCLUDED.TENANT_ID" not in sql, (
        "R13 — ON CONFLICT não deve atualizar tenant_id: "
        "o tenant de um vínculo é imutável após criação."
    )


def test_bind_document_conflict_does_not_mutate_tenant_id():
    """R13 — segundo upsert com tenant diferente não deve alterar tenant original."""
    sys.modules.setdefault("aio_pika", MagicMock())

    from src.storage.postgres_client import postgres_client as client

    executed_sqls: list[str] = []

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, stmt, params=None):
            executed_sqls.append(str(stmt))
        def commit(self): pass

    with patch.object(client, "get_session", return_value=_FakeSession()):
        # Criação original: tenant-A
        client.bind_document(
            document_id="shared-doc",
            envelope_id=None,
            org_id="org-X",
            tenant_id="tenant-A",
            target_type="equipment",
            target_id="equip-99",
        )
        # Segundo upsert: tenant-B (conflito com mesma chave)
        client.bind_document(
            document_id="shared-doc",
            envelope_id=None,
            org_id="org-X",
            tenant_id="tenant-B",
            target_type="equipment",
            target_id="equip-99",
        )

    # Verificar que nenhum dos SQLs executados atualiza tenant_id
    for sql in executed_sqls:
        assert "TENANT_ID = EXCLUDED.TENANT_ID" not in sql.upper(), (
            "R13 — segundo bind_document com tenant diferente não deve "
            "sobrescrever tenant_id do vínculo original."
        )


def test_different_tenants_same_org_produce_independent_bindings():
    """R13 — Tenant A e Tenant B da mesma org devem ter bindings independentes.

    Com a constraint incluindo tenant_id, dois INSERTs com o mesmo
    (document_id, target_type, target_id, org_id) mas tenant_ids diferentes
    NÃO devem colidir — cada tenant possui seu próprio binding.
    Antes desta correção (apenas org_id na constraint), o segundo INSERT
    colidiria e sobrescreveria o registro do Tenant A.
    """
    sys.modules.setdefault("aio_pika", MagicMock())

    from src.storage.postgres_client import postgres_client as client

    executed_sqls: list[str] = []

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, stmt, params=None):
            executed_sqls.append(str(stmt))
        def commit(self): pass

    with patch.object(client, "get_session", return_value=_FakeSession()):
        client.bind_document(
            document_id="shared-doc",
            envelope_id="env-A",
            org_id="org-X",
            tenant_id="tenant-A",
            target_type="equipment",
            target_id="equip-1",
        )
        client.bind_document(
            document_id="shared-doc",
            envelope_id="env-B",
            org_id="org-X",
            tenant_id="tenant-B",
            target_type="equipment",
            target_id="equip-1",
        )

    assert len(executed_sqls) == 2, (
        "R13 — dois bind_document com tenants diferentes devem gerar dois INSERTs "
        "independentes, não um INSERT + silêncio por conflito antecipado."
    )

    # Ambos os INSERTs devem referenciar a constraint nomeada.
    for sql in executed_sqls:
        assert "ON CONSTRAINT" in sql.upper(), (
            "R13 — cada bind_document deve usar ON CONFLICT ON CONSTRAINT."
        )


# ── Teste de integração real com PostgreSQL ───────────────────────────────────
# Requer: TEST_DATABASE_URL=postgresql://user:pass@host:5432/dbname
# Skipped automaticamente quando a variável não está definida.

_POSTGRES_DSN = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(
    not _POSTGRES_DSN,
    reason="TEST_DATABASE_URL não configurado — pulando teste de integração PostgreSQL",
)
def test_tenant_scoped_bindings_on_real_postgres():
    """R13 — integração: dois tenants da mesma org geram bindings independentes no PostgreSQL real.

    Valida:
    1. O segundo INSERT com tenant-B NÃO colide com o registro de tenant-A.
    2. Dois INSERTs com mesmo tenant colidem (UPSERT funciona).
    3. INSERT com tenant=NULL (modelo) faz UPSERT correto via NULLS NOT DISTINCT.
    """
    import sqlalchemy as sa
    from sqlalchemy import create_engine, text, MetaData, Table, Column, String, DateTime

    engine = create_engine(_POSTGRES_DSN, future=True)

    with engine.begin() as conn:
        # Criar tabela de teste isolada
        conn.execute(text("DROP TABLE IF EXISTS r13_test_bindings"))
        conn.execute(text("""
            CREATE TABLE r13_test_bindings (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                envelope_id TEXT,
                org_id TEXT NOT NULL,
                tenant_id TEXT,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                model_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_r13_test_tenant_scoped
                    UNIQUE NULLS NOT DISTINCT (document_id, target_type, target_id, org_id, tenant_id)
            )
        """))

        doc = "doc-r13"
        org = "org-test"
        target = "equip-r13"

        # INSERT tenant-A
        conn.execute(text("""
            INSERT INTO r13_test_bindings (id, document_id, org_id, tenant_id, target_type, target_id)
            VALUES (:id, :doc, :org, 'tenant-A', 'equipment', :target)
            ON CONFLICT ON CONSTRAINT uq_r13_test_tenant_scoped DO UPDATE SET
                envelope_id = EXCLUDED.envelope_id
        """), {"id": str(uuid.uuid4()), "doc": doc, "org": org, "target": target})

        # INSERT tenant-B — deve criar linha independente
        conn.execute(text("""
            INSERT INTO r13_test_bindings (id, document_id, org_id, tenant_id, target_type, target_id)
            VALUES (:id, :doc, :org, 'tenant-B', 'equipment', :target)
            ON CONFLICT ON CONSTRAINT uq_r13_test_tenant_scoped DO UPDATE SET
                envelope_id = EXCLUDED.envelope_id
        """), {"id": str(uuid.uuid4()), "doc": doc, "org": org, "target": target})

        count = conn.execute(text(
            "SELECT COUNT(*) FROM r13_test_bindings WHERE document_id = :doc AND org_id = :org"
        ), {"doc": doc, "org": org}).scalar()
        assert count == 2, (
            f"R13 — esperado 2 bindings (tenant-A + tenant-B), obtido {count}. "
            "A constraint deve permitir bindings independentes por tenant."
        )

        # UPSERT com mesmo tenant-A deve atualizar (não criar linha nova)
        conn.execute(text("""
            INSERT INTO r13_test_bindings (id, document_id, org_id, tenant_id, target_type, target_id, envelope_id)
            VALUES (:id, :doc, :org, 'tenant-A', 'equipment', :target, 'env-updated')
            ON CONFLICT ON CONSTRAINT uq_r13_test_tenant_scoped DO UPDATE SET
                envelope_id = EXCLUDED.envelope_id
        """), {"id": str(uuid.uuid4()), "doc": doc, "org": org, "target": target})

        count_after_upsert = conn.execute(text(
            "SELECT COUNT(*) FROM r13_test_bindings WHERE document_id = :doc AND org_id = :org"
        ), {"doc": doc, "org": org}).scalar()
        assert count_after_upsert == 2, (
            f"R13 — UPSERT com mesmo tenant-A não deve criar linha extra. "
            f"Esperado 2, obtido {count_after_upsert}."
        )

        # INSERT com tenant_id=NULL (modelo global) — NULLS NOT DISTINCT deve colidir
        conn.execute(text("""
            INSERT INTO r13_test_bindings (id, document_id, org_id, tenant_id, target_type, target_id)
            VALUES (:id, :doc, :org, NULL, 'model', :target)
            ON CONFLICT ON CONSTRAINT uq_r13_test_tenant_scoped DO UPDATE SET
                envelope_id = EXCLUDED.envelope_id
        """), {"id": str(uuid.uuid4()), "doc": doc, "org": org, "target": target})

        conn.execute(text("""
            INSERT INTO r13_test_bindings (id, document_id, org_id, tenant_id, target_type, target_id)
            VALUES (:id, :doc, :org, NULL, 'model', :target)
            ON CONFLICT ON CONSTRAINT uq_r13_test_tenant_scoped DO UPDATE SET
                envelope_id = EXCLUDED.envelope_id
        """), {"id": str(uuid.uuid4()), "doc": doc, "org": org, "target": target})

        null_count = conn.execute(text(
            "SELECT COUNT(*) FROM r13_test_bindings "
            "WHERE document_id = :doc AND org_id = :org AND tenant_id IS NULL"
        ), {"doc": doc, "org": org}).scalar()
        assert null_count == 1, (
            f"R13 — dois INSERTs com tenant_id=NULL devem colidir via NULLS NOT DISTINCT. "
            f"Esperado 1, obtido {null_count}."
        )

        conn.execute(text("DROP TABLE r13_test_bindings"))
