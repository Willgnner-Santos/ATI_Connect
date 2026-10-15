"""
Publisher assíncrono para RabbitMQ usando aio-pika.

Usa exchange topic (configurável, default: document-processing) e roteia as
mensagens pela `routing_key` do evento.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from loguru import logger

from src.config import settings


class RabbitMQPublisher:
    """Publisher reutilizável com conexão resiliente."""

    def __init__(self, amqp_url: Optional[str] = None):
        self.amqp_url = amqp_url or settings.AMQP_URL
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractRobustChannel] = None
        self._exchange: Optional[aio_pika.abc.AbstractRobustExchange] = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self):
        async with self._lock:
            if self._connection is None or self._connection.is_closed:
                if not self.amqp_url:
                    raise RuntimeError(
                        "AMQP_URL não configurado — publisher não pode conectar."
                    )
                logger.info(f"Conectando ao RabbitMQ: {self._redact_url()}")
                self._connection = await aio_pika.connect_robust(self.amqp_url)

            if self._channel is None or self._channel.is_closed:
                self._channel = await self._connection.channel(publisher_confirms=True)
                await self._channel.set_qos(prefetch_count=10)

                exchange_type = ExchangeType(settings.RABBITMQ_EXCHANGE_TYPE)
                self._exchange = await self._channel.declare_exchange(
                    settings.RABBITMQ_EXCHANGE,
                    type=exchange_type,
                    durable=True,
                )
                logger.info(
                    f"Exchange declarado: '{settings.RABBITMQ_EXCHANGE}' "
                    f"({settings.RABBITMQ_EXCHANGE_TYPE}, durable)"
                )

    def _redact_url(self) -> str:
        try:
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(self.amqp_url)
            if parsed.password:
                netloc = f"{parsed.username}:***@{parsed.hostname}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                return urlunparse(parsed._replace(netloc=netloc))
            return self.amqp_url
        except Exception:
            return "<amqp url>"

    async def publish(
        self,
        routing_key: str,
        payload: str,
        headers: Optional[dict] = None,
    ) -> None:
        await self._ensure_connected()
        assert self._exchange is not None

        message = Message(
            body=payload.encode("utf-8"),
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers=headers or {},
        )

        await self._exchange.publish(message, routing_key=routing_key)
        logger.debug(
            f"Publicado: routing_key={routing_key}, bytes={len(payload)}"
        )

    async def close(self):
        try:
            if self._channel and not self._channel.is_closed:
                await self._channel.close()
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
        except Exception as e:
            logger.warning(f"Erro ao fechar conexão RabbitMQ: {e}")
        finally:
            self._channel = None
            self._connection = None
            self._exchange = None
