"""
Testes de regressão para R27 — MAX(seq)+1 fora da transação do INSERT.

Bug: _next_seq() abre sessão A, lê MAX(seq). append() abre sessão B, insere.
     Duas chamadas concorrentes leem o mesmo MAX e tentam inserir seq duplicado.

Correto: SELECT MAX + INSERT na mesma sessão; serialização via advisory lock.

B1 - _next_seq e o INSERT usam sessões distintas → seq pode ser duplicado.
     Correto: append executa SELECT e INSERT na mesma sessão.

B2 - Sem mecanismo de serialização → duas transações concorrentes geram o mesmo seq.
     Correto: SQL contém advisory lock ou equivalente.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, call, patch, PropertyMock


# ── B1: SELECT MAX e INSERT devem estar na mesma sessão ───────────────────────

def test_b1_append_uses_single_session_for_seq_and_insert():
    """
    Antes: _next_seq abre sessão própria; append abre outra para o INSERT.
    Após: append usa uma única sessão para ambas as operações.
    """
    from src.chat.store import ChatStore

    store = ChatStore()

    sessions_created = []

    class MockSession:
        def __init__(self):
            sessions_created.append(self)
            self._entered = False
            self.executed = []

        def __enter__(self):
            self._entered = True
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params=None):
            sql = str(query)
            self.executed.append(sql)
            result = MagicMock()
            result.scalar.return_value = 3  # MAX(seq) = 3
            return result

        def commit(self):
            pass

    with patch("src.chat.store.postgres_client") as mock_pg:
        mock_pg.get_session.side_effect = lambda: MockSession()
        store.append("equip-1", "org-1", "tenant-1", "user", "olá")

    # Deve ter criado apenas UMA sessão (não duas separadas)
    assert len(sessions_created) == 1, (
        f"append deve usar uma única sessão para SELECT e INSERT, "
        f"mas criou {len(sessions_created)} sessões"
    )


def test_b1_both_max_and_insert_executed_in_same_session():
    """SELECT MAX e INSERT devem aparecer na mesma sessão (mesma lista de executed)."""
    from src.chat.store import ChatStore

    store = ChatStore()
    executed_sqls = []

    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: s
    mock_session.__exit__ = MagicMock(return_value=False)

    seq_result = MagicMock()
    seq_result.scalar.return_value = 0

    def capture_execute(query, params=None):
        executed_sqls.append(str(query).upper())
        result = MagicMock()
        result.scalar.return_value = 0
        return result

    mock_session.execute.side_effect = capture_execute

    with patch("src.chat.store.postgres_client") as mock_pg:
        mock_pg.get_session.return_value = mock_session
        store.append("equip-1", "org-1", "tenant-1", "user", "olá")

    has_select = any("SELECT" in sql and "MAX" in sql for sql in executed_sqls)
    has_insert = any("INSERT" in sql for sql in executed_sqls)

    assert has_select, f"deve ter SELECT MAX. SQLs executados: {executed_sqls}"
    assert has_insert, f"deve ter INSERT. SQLs executados: {executed_sqls}"


# ── B2: deve haver serialização (advisory lock ou similar) ────────────────────

def test_b2_append_uses_advisory_lock_or_equivalent():
    """
    Antes: nenhuma serialização — duas transações concorrentes geram seq duplicado.
    Após: código usa pg_advisory_xact_lock ou mecanismo equivalente.
    """
    from src.chat.store import ChatStore

    source = inspect.getsource(ChatStore.append)
    has_advisory_lock = "advisory" in source.lower() or "pg_advisory" in source.lower()
    has_for_update = "FOR UPDATE" in source.upper()
    has_serialization = has_advisory_lock or has_for_update

    assert has_serialization, (
        "ChatStore.append deve usar pg_advisory_xact_lock ou FOR UPDATE "
        "para serializar geração de seq e evitar duplicatas concorrentes"
    )


def test_b2_next_seq_no_longer_separate():
    """_next_seq não deve mais existir como método público separado."""
    from src.chat.store import ChatStore

    # O método _next_seq era a raiz do bug: SESSION separada.
    # Após a correção, a lógica deve estar internalizada no append.
    source_append = inspect.getsource(ChatStore.append)
    assert "get_session" in source_append, (
        "append deve chamar get_session diretamente (sem delegar para _next_seq separado)"
    )
