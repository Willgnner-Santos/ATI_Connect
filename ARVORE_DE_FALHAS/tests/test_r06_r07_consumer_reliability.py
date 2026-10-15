"""
Testes de regressão para R06/R07 — Consumer ACKa falhas e pode morrer silenciosamente.

Bugs provados (falham ANTES da correção, passam DEPOIS):

B1 - JSON não-dict (ex: []) levanta TypeError não capturado → consumer encerra.
     Correto: mensagem vai para DLQ, consumer continua.

B2 - Falha transitória (_materialize_file timeout) → ACK silencioso.
     Correto: retry com backoff; após MAX_RETRIES → DLQ.

B3 - Falha permanente (SSRF) → ACK silencioso.
     Correto: DLQ imediato, sem retry.

B4 - Pipeline retorna status='failed' → ACK silencioso.
     Correto: retry; após MAX_RETRIES → DLQ.

B5 - Mensagem já na MAX retry → DLQ, não mais retry.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mockar aio_pika antes de qualquer import do módulo
if "aio_pika" not in sys.modules:
    _aio_mock = MagicMock()
    # Message e DeliveryMode precisam de valores reais para a implementação
    _aio_mock.Message = MagicMock(side_effect=lambda body, headers=None, delivery_mode=None: _FakeAmqpMessage(body, headers))
    _aio_mock.DeliveryMode.PERSISTENT = 2
    sys.modules["aio_pika"] = _aio_mock


class _FakeAmqpMessage:
    """Representa uma mensagem aio_pika criada para publicação (DLQ / requeue)."""
    def __init__(self, body: bytes, headers=None, delivery_mode=None):
        self.body = body
        self.headers = headers or {}
        self.delivery_mode = delivery_mode


@dataclass
class _MockMessage:
    """Representa uma IncomingMessage do RabbitMQ."""
    _body: bytes
    headers: dict = field(default_factory=dict)
    acked: bool = field(default=False, init=False)
    nacked: bool = field(default=False, init=False)

    @property
    def body(self) -> bytes:
        return self._body

    async def ack(self):
        self.acked = True

    async def nack(self, requeue=True):
        self.nacked = True


@dataclass
class _MockExchange:
    published: list = field(default_factory=list)

    async def publish(self, message, routing_key):
        self.published.append((message, routing_key))


@dataclass
class _MockChannel:
    _exchange: _MockExchange = field(default_factory=_MockExchange)
    _closed: bool = False

    @property
    def default_exchange(self):
        return self._exchange

    @property
    def is_closed(self):
        return self._closed

    async def close(self):
        self._closed = True

    async def declare_queue(self, name, durable=True):
        return MagicMock()

    async def set_qos(self, prefetch_count):
        pass


def _make_consumer(pipeline_handler=None):
    from src.messaging.request_consumer import RequestConsumer
    handler = pipeline_handler or (lambda *a, **kw: {"status": "success"})
    consumer = RequestConsumer(
        pipeline_handler=handler,
        amqp_url="amqp://unused",
        queue_name="test.queue",
    )
    consumer._channel = _MockChannel()
    return consumer


def _msg(body, headers=None):
    if isinstance(body, str):
        body = body.encode()
    return _MockMessage(_body=body, headers=headers or {})


def _valid_body(**overrides) -> str:
    base = {
        "event_id": "evt-1",
        "event_type": "ingest.requested",
        "org_id": "org-1",
        "tenant_id": "ten-1",
        "file_name": "doc.pdf",
        "content_base64": "SGVsbG8=",  # b"Hello"
    }
    base.update(overrides)
    return json.dumps(base)


def _no_wait(coro_or_aw, timeout=None):
    """Substitui asyncio.wait_for para não aguardar nos testes."""
    if hasattr(coro_or_aw, "close"):
        coro_or_aw.close()
    raise asyncio.TimeoutError


# ── B1: JSON não-dict não deve encerrar o consumer ────────────────────────────

def test_b1_json_array_body_goes_to_dlq_not_crash():
    """
    Antes da correção: IngestRequestedEvent(**[]) levanta TypeError não capturado,
    propagando para run() e encerrando o consumer.
    Após: mensagem é enviada para DLQ e consumer continua.
    """
    consumer = _make_consumer()

    async def run():
        message = _msg("[]")
        with patch("asyncio.wait_for", side_effect=_no_wait):
            await consumer._handle_message(message)
        return message

    message = asyncio.run(run())

    assert message.acked, "mensagem deve ser ACKada após envio para DLQ"
    published = consumer._channel._exchange.published
    assert len(published) == 1, "deve publicar uma mensagem na DLQ"
    _, routing_key = published[0]
    assert routing_key == "test.queue.dlq"


def test_b1_json_object_body_is_valid():
    """JSON objeto válido não deve ir para DLQ."""
    consumer = _make_consumer()

    async def run():
        message = _msg(_valid_body())
        with patch("asyncio.wait_for", side_effect=_no_wait):
            with patch.object(consumer, "_materialize_file", new_callable=AsyncMock, return_value="/tmp/f.pdf"):
                await consumer._handle_message(message)
        return message

    message = asyncio.run(run())
    assert message.acked
    assert consumer._channel._exchange.published == []


# ── B2: Falha transitória → retry, não ACK silencioso ─────────────────────────

def test_b2_transient_materialize_error_retries_not_acks():
    """
    Antes: timeout em download → ACK silencioso.
    Após: mensagem é reenfileirada com x-retry-count=1.
    """
    from src.messaging.request_consumer import _TransientError

    consumer = _make_consumer()

    async def run():
        message = _msg(_valid_body(content_base64=None, source_url="https://example.com/doc.pdf"))
        with patch("src.messaging.request_consumer.aio_pika.Message", side_effect=_FakeAmqpMessage):
            with patch.object(consumer, "_materialize_file", new_callable=AsyncMock,
                              side_effect=_TransientError("timeout")):
                with patch("asyncio.wait_for", side_effect=_no_wait):
                    await consumer._handle_message(message)
        return message

    message = asyncio.run(run())

    assert message.acked, "ACK deve ocorrer após requeue"
    published = consumer._channel._exchange.published
    assert len(published) == 1, "deve publicar requeue na fila original"
    amqp_msg, routing_key = published[0]
    assert routing_key == "test.queue"
    assert amqp_msg.headers.get("x-retry-count") == 1


def test_b2_transient_error_not_acked_silently_without_requeue():
    """Falha transitória NÃO deve apenas ACKar sem reenfileirar."""
    from src.messaging.request_consumer import _TransientError

    consumer = _make_consumer()

    async def run():
        message = _msg(_valid_body(content_base64=None, source_url="https://example.com/doc.pdf"))
        with patch.object(consumer, "_materialize_file", new_callable=AsyncMock,
                          side_effect=_TransientError("timeout")):
            with patch("asyncio.wait_for", side_effect=_no_wait):
                await consumer._handle_message(message)
        return message

    message = asyncio.run(run())
    published_routing_keys = [rk for _, rk in consumer._channel._exchange.published]
    # Não deve ir direto para DLQ na primeira falha
    assert "test.queue.dlq" not in published_routing_keys


# ── B3: Falha permanente → DLQ imediato, sem retry ────────────────────────────

def test_b3_permanent_error_goes_to_dlq_immediately():
    """
    Antes: SSRF block → ACK silencioso.
    Após: DLQ imediato, sem tentar novamente.
    """
    from src.messaging.request_consumer import _PermanentError

    consumer = _make_consumer()

    async def run():
        message = _msg(_valid_body(content_base64=None, source_url="http://192.168.1.1/evil.pdf"))
        with patch.object(consumer, "_materialize_file", new_callable=AsyncMock,
                          side_effect=_PermanentError("SSRF bloqueado")):
            with patch("asyncio.wait_for", side_effect=_no_wait):
                await consumer._handle_message(message)
        return message

    message = asyncio.run(run())

    assert message.acked
    published = consumer._channel._exchange.published
    assert len(published) == 1
    _, routing_key = published[0]
    assert routing_key == "test.queue.dlq", "erro permanente deve ir para DLQ, não retry"


def test_b3_permanent_error_does_not_retry():
    """Erro permanente não deve gerar mensagem na fila original (sem retry)."""
    from src.messaging.request_consumer import _PermanentError

    consumer = _make_consumer()

    async def run():
        message = _msg(_valid_body())
        with patch.object(consumer, "_materialize_file", new_callable=AsyncMock,
                          side_effect=_PermanentError("arquivo proibido")):
            with patch("asyncio.wait_for", side_effect=_no_wait):
                await consumer._handle_message(message)
        return message

    asyncio.run(run())
    published_routing_keys = [rk for _, rk in consumer._channel._exchange.published]
    assert "test.queue" not in published_routing_keys


# ── B4: Pipeline failed → retry, não ACK ─────────────────────────────────────

def test_b4_pipeline_failed_status_retries():
    """
    Antes: pipeline retorna {'status': 'failed'} → ACK silencioso.
    Após: reenfileira com retry.
    """
    consumer = _make_consumer(pipeline_handler=lambda *a, **kw: {"status": "failed"})

    async def run():
        message = _msg(_valid_body())
        with patch.object(consumer, "_materialize_file", new_callable=AsyncMock, return_value="/tmp/f.pdf"):
            with patch("asyncio.wait_for", side_effect=_no_wait):
                await consumer._handle_message(message)
        return message

    message = asyncio.run(run())

    assert message.acked
    published_routing_keys = [rk for _, rk in consumer._channel._exchange.published]
    assert "test.queue" in published_routing_keys, "status=failed deve gerar retry"


def test_b4_pipeline_success_acks_without_requeue():
    """Pipeline com status=success não deve gerar retry nem DLQ."""
    consumer = _make_consumer(pipeline_handler=lambda *a, **kw: {"status": "success"})

    async def run():
        message = _msg(_valid_body())
        with patch.object(consumer, "_materialize_file", new_callable=AsyncMock, return_value="/tmp/f.pdf"):
            with patch("asyncio.wait_for", side_effect=_no_wait):
                await consumer._handle_message(message)
        return message

    message = asyncio.run(run())
    assert message.acked
    assert consumer._channel._exchange.published == []


# ── B5: MAX_RETRIES atingido → DLQ ────────────────────────────────────────────

def test_b5_max_retries_exceeded_goes_to_dlq():
    """
    Mensagem com x-retry-count == MAX_RETRIES deve ir para DLQ (não retry infinito).
    """
    from src.messaging.request_consumer import _MAX_RETRIES, _TransientError

    consumer = _make_consumer()

    async def run():
        message = _msg(_valid_body(), headers={"x-retry-count": _MAX_RETRIES})
        with patch.object(consumer, "_materialize_file", new_callable=AsyncMock,
                          side_effect=_TransientError("still timing out")):
            with patch("asyncio.wait_for", side_effect=_no_wait):
                await consumer._handle_message(message)
        return message

    message = asyncio.run(run())

    assert message.acked
    published = consumer._channel._exchange.published
    assert len(published) == 1
    _, routing_key = published[0]
    assert routing_key == "test.queue.dlq"


def test_b5_retry_count_increments_correctly():
    """x-retry-count deve ser incrementado a cada requeue."""
    from src.messaging.request_consumer import _TransientError

    consumer = _make_consumer()

    async def run():
        message = _msg(_valid_body(), headers={"x-retry-count": 1})
        with patch("src.messaging.request_consumer.aio_pika.Message", side_effect=_FakeAmqpMessage):
            with patch.object(consumer, "_materialize_file", new_callable=AsyncMock,
                              side_effect=_TransientError("ainda falha")):
                with patch("asyncio.wait_for", side_effect=_no_wait):
                    await consumer._handle_message(message)

    asyncio.run(run())
    published = consumer._channel._exchange.published
    assert len(published) == 1
    amqp_msg, _ = published[0]
    assert amqp_msg.headers.get("x-retry-count") == 2
