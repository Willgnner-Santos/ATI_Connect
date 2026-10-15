"""
ChatStore e SummaryStore persistidos no PostgreSQL (substitui MongoDB do LTS).
Chave: equipment_id + org_id + tenant_id.
"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger
from sqlalchemy import text

from src.storage.postgres_client import postgres_client


class ChatStore:
    def _key(self, equipment_id: str, org_id: str, tenant_id: str) -> dict:
        return {"equipment_id": str(equipment_id), "org_id": str(org_id), "tenant_id": str(tenant_id)}

    def append(self, equipment_id: str, org_id: str, tenant_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            return
        content = (content or "").strip()
        if not content:
            return
        try:
            with postgres_client.get_session() as session:
                # pg_advisory_xact_lock serializa geração de seq para a mesma conversation.
                # Libera automaticamente ao fim da transação (xact_lock).
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"{equipment_id}:{org_id}:{tenant_id}"},
                )
                result = session.execute(
                    text("SELECT COALESCE(MAX(seq), 0) FROM chat_history "
                         "WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id},
                ).scalar()
                seq = int(result or 0) + 1
                session.execute(
                    text("INSERT INTO chat_history (equipment_id, org_id, tenant_id, seq, role, content, ts) "
                         "VALUES (:eid, :org_id, :tid, :seq, :role, :content, :ts)"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id,
                     "seq": seq, "role": role, "content": content, "ts": int(time.time())},
                )
                session.commit()
        except Exception as e:
            logger.error(f"ChatStore.append error: {e}")

    def read_last(self, equipment_id: str, org_id: str, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            with postgres_client.get_session() as session:
                rows = session.execute(
                    text("SELECT role, content FROM chat_history "
                         "WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid "
                         "ORDER BY seq DESC LIMIT :lim"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id, "lim": max(1, limit)},
                ).fetchall()
            rows.reverse()
            return [{"role": r.role, "content": r.content} for r in rows if r.content]
        except Exception as e:
            logger.error(f"ChatStore.read_last error: {e}")
            return []

    def read_all(self, equipment_id: str, org_id: str, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        try:
            with postgres_client.get_session() as session:
                rows = session.execute(
                    text("SELECT role, content, ts FROM chat_history "
                         "WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid "
                         "ORDER BY seq ASC LIMIT :lim"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id, "lim": limit},
                ).fetchall()
            return [{"role": r.role, "content": r.content, "ts": r.ts} for r in rows if r.content]
        except Exception as e:
            logger.error(f"ChatStore.read_all error: {e}")
            return []

    def count(self, equipment_id: str, org_id: str, tenant_id: str) -> int:
        try:
            with postgres_client.get_session() as session:
                return int(session.execute(
                    text("SELECT COUNT(*) FROM chat_history "
                         "WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id},
                ).scalar() or 0)
        except Exception as e:
            logger.error(f"ChatStore.count error: {e}")
            return 0

    def read_range(self, equipment_id: str, org_id: str, tenant_id: str, start: int, end: int) -> list[dict[str, Any]]:
        limit = max(0, end - start)
        if limit <= 0:
            return []
        try:
            with postgres_client.get_session() as session:
                rows = session.execute(
                    text("SELECT role, content FROM chat_history "
                         "WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid "
                         "ORDER BY seq ASC LIMIT :lim OFFSET :off"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id, "lim": limit, "off": start},
                ).fetchall()
            return [{"role": r.role, "content": r.content} for r in rows if r.content]
        except Exception as e:
            logger.error(f"ChatStore.read_range error: {e}")
            return []

    def delete(self, equipment_id: str, org_id: str, tenant_id: str) -> None:
        try:
            with postgres_client.get_session() as session:
                session.execute(
                    text("DELETE FROM chat_history WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id},
                )
                session.commit()
        except Exception as e:
            logger.error(f"ChatStore.delete error: {e}")


class SummaryStore:
    def get(self, equipment_id: str, org_id: str, tenant_id: str) -> tuple[str, int]:
        try:
            with postgres_client.get_session() as session:
                row = session.execute(
                    text("SELECT summary, summarized_upto FROM chat_summaries "
                         "WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id},
                ).fetchone()
            if row:
                return (str(row.summary or "").strip(), int(row.summarized_upto or 0))
            return ("", 0)
        except Exception as e:
            logger.error(f"SummaryStore.get error: {e}")
            return ("", 0)

    def set(self, equipment_id: str, org_id: str, tenant_id: str, summary: str, summarized_upto: int) -> None:
        try:
            with postgres_client.get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO chat_summaries (equipment_id, org_id, tenant_id, summary, summarized_upto, updated_at)
                        VALUES (:eid, :org_id, :tid, :summary, :upto, NOW())
                        ON CONFLICT (org_id, equipment_id, tenant_id)
                        DO UPDATE SET summary = EXCLUDED.summary, summarized_upto = EXCLUDED.summarized_upto, updated_at = NOW()
                    """),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id,
                     "summary": (summary or "").strip(), "upto": int(summarized_upto)},
                )
                session.commit()
        except Exception as e:
            logger.error(f"SummaryStore.set error: {e}")

    def delete(self, equipment_id: str, org_id: str, tenant_id: str) -> None:
        try:
            with postgres_client.get_session() as session:
                session.execute(
                    text("DELETE FROM chat_summaries WHERE equipment_id = :eid AND org_id = :org_id AND tenant_id = :tid"),
                    {"eid": equipment_id, "org_id": org_id, "tid": tenant_id},
                )
                session.commit()
        except Exception as e:
            logger.error(f"SummaryStore.delete error: {e}")


chat_store = ChatStore()
summary_store = SummaryStore()
