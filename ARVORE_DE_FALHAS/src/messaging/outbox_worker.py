"""
Worker assíncrono que drena a tabela event_outbox publicando no RabbitMQ.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from src.config import settings
from .outbox import OutboxRepository, outbox_repository
from .rabbitmq_publisher import RabbitMQPublisher


class OutboxWorker:
    """Drena eventos pendentes da outbox para o RabbitMQ."""

    def __init__(
        self,
        publisher: Optional[RabbitMQPublisher] = None,
        repository: Optional[OutboxRepository] = None,
        poll_interval_seconds: Optional[int] = None,
    ):
        self.publisher = publisher or RabbitMQPublisher()
        self.repository = repository or outbox_repository
        self.poll_interval = poll_interval_seconds or settings.OUTBOX_POLL_INTERVAL_SECONDS
        self._stopped = asyncio.Event()

    async def run(self):
        logger.info(
            f"OutboxWorker iniciado (interval={self.poll_interval}s, "
            f"batch={settings.OUTBOX_BATCH_SIZE}, max_attempts={settings.OUTBOX_MAX_ATTEMPTS})"
        )
        try:
            while not self._stopped.is_set():
                try:
                    await self._drain_once()
                except Exception as e:
                    logger.error(f"Erro no ciclo da outbox: {e}")

                try:
                    await asyncio.wait_for(
                        self._stopped.wait(), timeout=self.poll_interval
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.publisher.close()
            logger.info("OutboxWorker parado")

    async def _drain_once(self):
        records = self.repository.fetch_pending()
        if not records:
            return

        logger.info(f"Publicando {len(records)} evento(s) da outbox")

        for record in records:
            try:
                await self.publisher.publish(
                    routing_key=record.routing_key,
                    payload=record.payload,
                    headers={
                        "event_type": record.event_type,
                        "org_id": record.org_id,
                        "outbox_id": record.id,
                        **({"tenant_id": record.tenant_id} if record.tenant_id else {}),
                    },
                )
                self.repository.mark_published(record.id)
                logger.info(
                    f"Evento publicado: {record.id} "
                    f"(type={record.event_type}, org={record.org_id}, tenant={record.tenant_id})"
                )
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"Falha ao publicar {record.id} (attempts={record.attempts + 1}): {error_msg}"
                )
                self.repository.mark_failed_attempt(record.id, error_msg)

    def stop(self):
        self._stopped.set()
