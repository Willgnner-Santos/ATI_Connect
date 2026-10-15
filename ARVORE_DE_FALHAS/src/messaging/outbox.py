"""
Repositório da Outbox de eventos (Outbox Pattern).

Fluxo:
    1. O orchestrator salva o evento em `event_outbox` (status=pending).
    2. Um worker (outbox_worker.py) faz polling periódico, pega lotes de
       eventos pendentes, publica no RabbitMQ e marca published.
    3. Se falhar, o worker incrementa `attempts`, agenda `next_attempt_at`
       com backoff exponencial e loga o erro. Eventos não são perdidos.

Status possíveis:
    - pending     — aguardando publicação (ou retry agendado)
    - published   — publicado com sucesso
    - failed      — excedeu OUTBOX_MAX_ATTEMPTS, requer investigação manual
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from loguru import logger
from sqlalchemy import text

from src.config import settings
from src.storage.postgres_client import postgres_client
from .event_schemas import BaseEvent


@dataclass
class OutboxRecord:
    """Representação em memória de um registro da event_outbox."""

    id: str
    org_id: str
    tenant_id: Optional[str]
    event_type: str
    routing_key: str
    payload: str
    status: str
    attempts: int
    last_error: Optional[str]
    created_at: datetime
    published_at: Optional[datetime]
    next_attempt_at: Optional[datetime]


def _backoff_seconds(attempts: int) -> int:
    """Backoff exponencial capado em 5 minutos."""
    return min(5 * (2 ** max(0, attempts - 1)), 300)


class OutboxRepository:
    """Acesso à tabela event_outbox."""

    _INSERT_SQL = """
        INSERT INTO event_outbox
        (id, org_id, tenant_id, event_type, routing_key, payload,
         status, attempts, created_at)
        VALUES (:id, :org_id, :tenant_id, :event_type, :routing_key,
                :payload, 'pending', 0, :created_at)
    """

    def _event_params(self, record_id: str, event: BaseEvent) -> dict:
        return {
            "id": record_id,
            "org_id": event.org_id,
            "tenant_id": event.tenant_id,
            "event_type": event.event_type.value,
            "routing_key": event.routing_key(),
            "payload": event.to_json(),
            "created_at": datetime.now(timezone.utc),
        }

    def save_event_with_session(self, event: BaseEvent, session) -> str:
        """Insere evento na outbox usando a sessão fornecida, sem commit.

        Usar quando a inserção na outbox precisa ser atômica com uma escrita
        de domínio que já detém a mesma sessão. O caller é responsável pelo commit.
        """
        record_id = str(uuid.uuid4())
        session.execute(text(self._INSERT_SQL), self._event_params(record_id, event))
        logger.info(
            f"Evento preparado na outbox (sem commit): {record_id} "
            f"(type={event.event_type.value}, org={event.org_id}, tenant={event.tenant_id})"
        )
        return record_id

    def save_event(self, event: BaseEvent) -> Optional[str]:
        record_id = str(uuid.uuid4())
        try:
            with postgres_client.get_session() as session:
                session.execute(text(self._INSERT_SQL), self._event_params(record_id, event))
                session.commit()
                logger.info(
                    f"Evento salvo na outbox: {record_id} "
                    f"(type={event.event_type.value}, org={event.org_id}, tenant={event.tenant_id})"
                )
                return record_id
        except Exception as e:
            logger.error(f"Erro ao salvar evento na outbox: {e}")
            return None

    def fetch_pending(self, batch_size: Optional[int] = None) -> List[OutboxRecord]:
        limit = batch_size or settings.OUTBOX_BATCH_SIZE
        try:
            with postgres_client.get_session() as session:
                rows = session.execute(
                    text("""
                        SELECT id, org_id, tenant_id, event_type, routing_key, payload,
                               status, attempts, last_error, created_at,
                               published_at, next_attempt_at
                        FROM event_outbox
                        WHERE status = 'pending'
                          AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
                        ORDER BY created_at ASC
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED
                    """),
                    {"now": datetime.now(timezone.utc), "limit": limit},
                ).fetchall()

                return [
                    OutboxRecord(
                        id=r.id,
                        org_id=r.org_id,
                        tenant_id=r.tenant_id,
                        event_type=r.event_type,
                        routing_key=r.routing_key,
                        payload=r.payload,
                        status=r.status,
                        attempts=r.attempts,
                        last_error=r.last_error,
                        created_at=r.created_at,
                        published_at=r.published_at,
                        next_attempt_at=r.next_attempt_at,
                    )
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Erro ao buscar eventos pendentes: {e}")
            return []

    def mark_published(self, record_id: str) -> bool:
        try:
            with postgres_client.get_session() as session:
                session.execute(
                    text("""
                        UPDATE event_outbox
                        SET status = 'published',
                            published_at = :now,
                            last_error = NULL
                        WHERE id = :id
                    """),
                    {"now": datetime.now(timezone.utc), "id": record_id},
                )
                session.commit()
                return True
        except Exception as e:
            logger.error(f"Erro ao marcar evento como publicado ({record_id}): {e}")
            return False

    def mark_failed_attempt(self, record_id: str, error_message: str) -> bool:
        try:
            with postgres_client.get_session() as session:
                row = session.execute(
                    text("SELECT attempts FROM event_outbox WHERE id = :id"),
                    {"id": record_id},
                ).fetchone()
                if row is None:
                    logger.warning(f"Outbox record não encontrado: {record_id}")
                    return False

                new_attempts = row.attempts + 1
                max_attempts = settings.OUTBOX_MAX_ATTEMPTS

                if new_attempts >= max_attempts:
                    new_status = "failed"
                    next_attempt = None
                    logger.error(
                        f"Evento {record_id} atingiu {max_attempts} tentativas — "
                        f"marcado como failed. Último erro: {error_message}"
                    )
                else:
                    new_status = "pending"
                    next_attempt = datetime.now(timezone.utc) + timedelta(
                        seconds=_backoff_seconds(new_attempts)
                    )

                session.execute(
                    text("""
                        UPDATE event_outbox
                        SET attempts = :attempts,
                            last_error = :last_error,
                            status = :status,
                            next_attempt_at = :next_attempt
                        WHERE id = :id
                    """),
                    {
                        "id": record_id,
                        "attempts": new_attempts,
                        "last_error": error_message[:5000],
                        "status": new_status,
                        "next_attempt": next_attempt,
                    },
                )
                session.commit()
                return True
        except Exception as e:
            logger.error(f"Erro ao registrar falha do evento {record_id}: {e}")
            return False

    def count_by_status(self, org_id: Optional[str] = None, tenant_id: Optional[str] = None) -> dict:
        try:
            with postgres_client.get_session() as session:
                if tenant_id:
                    rows = session.execute(
                        text(
                            "SELECT status, COUNT(*) as qty FROM event_outbox "
                            "WHERE tenant_id = :tenant_id GROUP BY status"
                        ),
                        {"tenant_id": tenant_id},
                    ).fetchall()
                else:
                    rows = session.execute(
                        text(
                            "SELECT status, COUNT(*) as qty FROM event_outbox "
                            "GROUP BY status"
                        )
                    ).fetchall()
                return {r.status: r.qty for r in rows}
        except Exception as e:
            logger.error(f"Erro ao contar outbox: {e}")
            return {}


outbox_repository = OutboxRepository()
