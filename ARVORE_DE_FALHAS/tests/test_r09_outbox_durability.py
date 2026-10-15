"""
Testes de regressão para R09 — Outbox sem claim/lease e não transacional.

Bugs provados (falham ANTES da correção, passam DEPOIS):

B1 - fetch_pending não usa FOR UPDATE SKIP LOCKED → dois workers publicam o mesmo evento.
     Correto: SQL contém FOR UPDATE SKIP LOCKED.

B2 - save_event não aceita sessão externa → impossível tornar outbox + negócio atômicos.
     Correto: save_event_with_session(event, session) usa a sessão fornecida, sem commit próprio.

B3 - save_event_with_session não deve chamar session.commit().
     Correto: quem fornece a sessão controla o commit.

B4 - _emit_processed_event no orchestrator usa save_event (transação própria) mesmo com
     save_event_with_session disponível.
     Correto: orchestrator usa save_event_with_session com sessão controlada.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, call, patch

import pytest


# ── B1: fetch_pending deve usar FOR UPDATE SKIP LOCKED ────────────────────────

def test_b1_fetch_pending_sql_contains_skip_locked():
    """
    Antes: SELECT ... sem FOR UPDATE SKIP LOCKED.
    Após: SQL contém a cláusula de claim atômico.
    """
    from src.messaging.outbox import OutboxRepository

    source = inspect.getsource(OutboxRepository.fetch_pending)
    assert "FOR UPDATE SKIP LOCKED" in source, (
        "fetch_pending deve usar FOR UPDATE SKIP LOCKED para prevenir "
        "que dois workers consumam o mesmo evento"
    )


def test_b1_skip_locked_positioned_after_limit():
    """FOR UPDATE SKIP LOCKED deve vir depois de LIMIT (sintaxe PostgreSQL correta)."""
    from src.messaging.outbox import OutboxRepository

    source = inspect.getsource(OutboxRepository.fetch_pending)
    limit_pos = source.find("LIMIT")
    skip_pos = source.find("FOR UPDATE SKIP LOCKED")
    assert limit_pos != -1, "LIMIT deve estar presente"
    assert skip_pos != -1, "FOR UPDATE SKIP LOCKED deve estar presente"
    assert skip_pos > limit_pos, (
        "FOR UPDATE SKIP LOCKED deve vir após LIMIT na query PostgreSQL"
    )


# ── B2: save_event_with_session deve existir e usar sessão fornecida ──────────

def test_b2_save_event_with_session_method_exists():
    """
    Antes: OutboxRepository não possui save_event_with_session.
    Após: método existe e aceita (event, session).
    """
    from src.messaging.outbox import OutboxRepository

    assert hasattr(OutboxRepository, "save_event_with_session"), (
        "OutboxRepository deve ter save_event_with_session(event, session)"
    )


def test_b2_save_event_with_session_uses_provided_session():
    """
    save_event_with_session deve executar o INSERT na sessão fornecida,
    não abrir uma sessão nova do postgres_client.
    """
    from src.messaging.outbox import OutboxRepository
    from src.messaging.event_schemas import DocumentProcessedEvent

    repo = OutboxRepository()
    mock_session = MagicMock()

    event = DocumentProcessedEvent(
        event_id="evt-test",
        org_id="org-1",
        tenant_id="ten-1",
        bronze_id="br-1",
        file_name="doc.pdf",
        file_type="pdf",
    )

    with patch("src.messaging.outbox.postgres_client") as mock_pg:
        record_id = repo.save_event_with_session(event, mock_session)

    # Deve ter chamado session.execute (INSERT), não postgres_client.get_session
    assert mock_session.execute.called, "deve chamar session.execute com o INSERT"
    mock_pg.get_session.assert_not_called(), "não deve abrir nova sessão do postgres_client"
    assert isinstance(record_id, str) and len(record_id) > 0, "deve retornar o record_id"


# ── B3: save_event_with_session não deve commitar ────────────────────────────

def test_b3_save_event_with_session_does_not_commit():
    """
    Antes: não existe — N/A.
    Após: save_event_with_session NÃO chama session.commit(); o commit é responsabilidade do caller.
    """
    from src.messaging.outbox import OutboxRepository
    from src.messaging.event_schemas import DocumentProcessedEvent

    repo = OutboxRepository()
    mock_session = MagicMock()

    event = DocumentProcessedEvent(
        event_id="evt-test-2",
        org_id="org-1",
        tenant_id="ten-1",
        bronze_id="br-2",
        file_name="doc2.pdf",
        file_type="pdf",
    )

    with patch("src.messaging.outbox.postgres_client"):
        repo.save_event_with_session(event, mock_session)

    mock_session.commit.assert_not_called(), (
        "save_event_with_session não deve chamar commit — "
        "o caller controla a transação"
    )


# ── B4: orchestrator deve usar save_event_with_session ───────────────────────

def test_b4_orchestrator_emit_uses_session_aware_method():
    """
    Antes: _emit_processed_event chama outbox_repository.save_event (transação própria).
    Após: chama save_event_with_session com sessão controlada pelo orchestrator.
    """
    import src.orchestrator as orch_module

    source = inspect.getsource(orch_module.IngestOrchestrator._emit_processed_event)
    assert "save_event_with_session" in source, (
        "_emit_processed_event deve usar save_event_with_session "
        "para que a inserção na outbox participe da mesma sessão"
    )


def test_b4_orchestrator_emit_failed_uses_session_aware_method():
    """_emit_failed_event também deve usar save_event_with_session."""
    import src.orchestrator as orch_module

    source = inspect.getsource(orch_module.IngestOrchestrator._emit_failed_event)
    assert "save_event_with_session" in source, (
        "_emit_failed_event deve usar save_event_with_session"
    )
