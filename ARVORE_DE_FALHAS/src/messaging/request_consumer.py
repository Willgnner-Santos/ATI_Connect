"""
Consumer assíncrono para a fila de requisições de ingestão.

Escuta `settings.REQUEST_QUEUE` (default: docs.ingest.requests). Cada mensagem
deve ser um JSON compatível com `IngestRequestedEvent` contendo:

    {
      "event_id": "...", "event_type": "ingest.requested",
      "org_id": "...", "tenant_id": "...", "file_name": "doc.pdf",
      "source_url": "https://..." | "content_base64": "...",
      "full_pipeline": true,
      "callback_correlation_id": "opcional"
    }

O consumer baixa/decodifica o arquivo para um path temporário e dispara o
orchestrator. O resultado do pipeline já gera eventos document.processed /
document.failed via outbox.

Política de retry/DLQ:
  - payload inválido, SSRF, arquivo proibido → DLQ imediato (sem retry)
  - timeout, 5xx, indisponibilidade, pipeline failed → retry com backoff
    (máximo _MAX_RETRIES tentativas via header x-retry-count) → DLQ
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import socket
import tempfile
import threading
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urljoin, urlparse

import aio_pika
from aio_pika import IncomingMessage
from loguru import logger
from pydantic import ValidationError

from src.config import settings
from .event_schemas import IngestRequestedEvent


# ── Política de retry ─────────────────────────────────────────────────────────

_MAX_RETRIES: int = 3
_RETRY_DELAYS: tuple[int, ...] = (10, 60, 300)  # segundos por tentativa


class _PermanentError(Exception):
    """Falha definitiva — payload inválido, SSRF ou arquivo proibido. Vai para DLQ sem retry."""


class _TransientError(Exception):
    """Falha transitória — timeout, 5xx ou indisponibilidade. Será reprocessada com backoff."""


# ── SSRF guard ────────────────────────────────────────────────────────────────

_MAX_DOWNLOAD_BYTES: int = 50 * 1024 * 1024   # 50 MB
_MAX_REDIRECTS: int = 3

# Lista explícita complementa o check fail-closed para ranges que Python < 3.11
# pode não reconhecer como não-globais (benchmarking, IETF assignments, TEST-NET).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback IPv4
    ipaddress.ip_network("::1/128"),           # loopback IPv6
    ipaddress.ip_network("10.0.0.0/8"),        # private class A
    ipaddress.ip_network("172.16.0.0/12"),     # private class B
    ipaddress.ip_network("192.168.0.0/16"),    # private class C
    ipaddress.ip_network("169.254.0.0/16"),    # link-local IPv4 / cloud metadata
    ipaddress.ip_network("fe80::/10"),         # link-local IPv6
    ipaddress.ip_network("fc00::/7"),          # unique-local IPv6
    ipaddress.ip_network("100.64.0.0/10"),     # shared address (RFC 6598)
    ipaddress.ip_network("0.0.0.0/8"),         # "this" network
    ipaddress.ip_network("240.0.0.0/4"),       # reserved (class E)
    ipaddress.ip_network("::ffff:0:0/96"),     # IPv4-mapped IPv6
    ipaddress.ip_network("192.0.0.0/24"),      # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1 (documentação RFC)
    ipaddress.ip_network("198.18.0.0/15"),     # Benchmarking (RFC 2544)
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2 (documentação RFC)
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3 (documentação RFC)
]

# ── DNS rebinding prevention ──────────────────────────────────────────────────
#
# Problema: validar o IP via getaddrinfo antes de requests.get() não garante
# que o requests conecte ao mesmo IP — o DNS pode resolver para outro endereço
# entre a validação e a conexão (DNS rebinding / TOCTOU).
#
# Solução: instalar um wrapper thread-safe em socket.getaddrinfo que "pina"
# a resolução do hostname ao IP validado durante a janela de conexão. Assim,
# quando urllib3/requests chamar getaddrinfo internamente, receberá o IP que
# já foi verificado, não uma nova resolução.

_original_getaddrinfo = socket.getaddrinfo
_thread_local = threading.local()


def _ssrf_aware_getaddrinfo(host, port=None, family=0, type=0, proto=0, flags=0):
    """Wrapper de socket.getaddrinfo que honra pinnings de DNS por thread."""
    pinnings: dict = getattr(_thread_local, "dns_pinnings", {})
    if host in pinnings:
        pinned_ip = pinnings[host]
        af = socket.AF_INET6 if ":" in pinned_ip else socket.AF_INET
        if af == socket.AF_INET6:
            return [(af, socket.SOCK_STREAM, 0, "", (pinned_ip, port or 0, 0, 0))]
        return [(af, socket.SOCK_STREAM, 0, "", (pinned_ip, port or 0))]
    return _original_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _ssrf_aware_getaddrinfo


@contextlib.contextmanager
def _pin_hostname(hostname: str, ip: str) -> Iterator[None]:
    """Pina hostname → ip no getaddrinfo deste thread para prevenir DNS rebinding."""
    if not hasattr(_thread_local, "dns_pinnings"):
        _thread_local.dns_pinnings = {}
    _thread_local.dns_pinnings[hostname] = ip
    try:
        yield
    finally:
        _thread_local.dns_pinnings.pop(hostname, None)


def _is_blocked_ip(addr: str) -> bool:
    """Retorna True se o IP não é roteável publicamente (fail-closed)."""
    try:
        ip = ipaddress.ip_address(addr)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        # Fail-closed: bloquear qualquer endereço não-global (privado, loopback, reservado, etc.).
        # `is_global` do módulo ipaddress cobre a maioria dos casos; a lista explícita
        # complementa para ranges que versões antigas de Python podem classificar mal.
        if not ip.is_global or ip.is_multicast:
            return True
        return any(ip in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        return True  # formato desconhecido → bloquear


def _validate_url_for_download(url: str) -> str:
    """Valida URL antes de download externo e retorna o primeiro IP resolvido.

    Levanta ValueError se a URL usar esquema não permitido ou apontar para
    um endereço IP privado, loopback ou reservado.

    Retorna o IP resolvido para uso com _pin_hostname() e prevenir DNS rebinding.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Esquema '{parsed.scheme}' não permitido. Apenas http/https são aceitos."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL sem hostname.")

    try:
        # Usar getaddrinfo original — evita interferência de pinnings existentes.
        addr_infos = _original_getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Não foi possível resolver '{hostname}': {exc}")

    resolved_ip: Optional[str] = None
    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        if _is_blocked_ip(ip_str):
            raise ValueError(
                f"Destino '{hostname}' ({ip_str}) está em intervalo de IP bloqueado "
                "(privado, loopback ou reservado). Download não permitido."
            )
        if resolved_ip is None:
            resolved_ip = ip_str

    return resolved_ip  # type: ignore[return-value]  # addr_infos nunca vazio


# O callable recebe (org_id, tenant_id, file_path, full_pipeline, metadata,
# target_type, target_id, generate_fault_tree) e devolve um dict com o resultado.
PipelineHandler = Callable[[str, str, str, bool, dict, Optional[str], Optional[str], bool], dict]


class RequestConsumer:
    """Escuta a fila de requests e dispara o pipeline para cada mensagem."""

    def __init__(
        self,
        pipeline_handler: PipelineHandler,
        amqp_url: Optional[str] = None,
        queue_name: Optional[str] = None,
        download_timeout_seconds: int = 60,
    ):
        self.pipeline_handler = pipeline_handler
        self.amqp_url = amqp_url or settings.AMQP_URL
        self.queue_name = queue_name or settings.REQUEST_QUEUE
        self._dlq_name = f"{self.queue_name}.dlq" if self.queue_name else "docs.ingest.requests.dlq"
        self.download_timeout = download_timeout_seconds
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractRobustChannel] = None
        self._stopped = asyncio.Event()

    async def run(self):
        if not self.amqp_url:
            logger.warning("AMQP_URL não configurado — request consumer desabilitado")
            return
        if not self.queue_name:
            logger.warning("REQUEST_QUEUE não configurada — request consumer desabilitado")
            return

        logger.info(f"RequestConsumer iniciado (queue='{self.queue_name}', dlq='{self._dlq_name}')")
        try:
            self._connection = await aio_pika.connect_robust(self.amqp_url)
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=1)

            await self._channel.declare_queue(self._dlq_name, durable=True)
            queue = await self._channel.declare_queue(self.queue_name, durable=True)

            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    if self._stopped.is_set():
                        break
                    await self._handle_message(message)
        except Exception as e:
            logger.error(f"RequestConsumer: erro fatal no loop: {e}")
        finally:
            await self._close()
            logger.info("RequestConsumer parado")

    async def _handle_message(self, message: IncomingMessage) -> None:
        body = message.body.decode("utf-8", errors="replace")
        logger.info(f"RequestConsumer: mensagem recebida ({len(body)} bytes)")
        retry_count = int((message.headers or {}).get("x-retry-count", 0))

        tmp_path: Optional[str] = None
        try:
            # Parse e validação do payload
            try:
                raw = json.loads(body)
            except json.JSONDecodeError as e:
                raise _PermanentError(f"JSON inválido: {e}") from e

            if not isinstance(raw, dict):
                raise _PermanentError(
                    f"Payload deve ser um objeto JSON, recebido: {type(raw).__name__}"
                )

            try:
                event = IngestRequestedEvent(**raw)
            except (ValidationError, TypeError) as e:
                raise _PermanentError(f"Payload inválido: {e}") from e

            # Materializar arquivo
            tmp_path = await self._materialize_file(event)

            # Executar pipeline
            metadata = {
                "filename": event.file_name,
                "correlation_id": event.callback_correlation_id,
                **event.extra_metadata,
            }
            result = await asyncio.to_thread(
                self.pipeline_handler,
                event.org_id,
                event.tenant_id,
                tmp_path,
                event.full_pipeline,
                metadata,
                event.target_type,
                event.target_id,
                event.generate_fault_tree,
            )

            status = result.get("status") if isinstance(result, dict) else None
            logger.info(
                f"RequestConsumer: pipeline finalizado "
                f"(org={event.org_id}, tenant={event.tenant_id}, status={status})"
            )

            if status in ("failed", "error"):
                raise _TransientError(f"Pipeline retornou status={status!r}")

            await message.ack()

        except _PermanentError as e:
            logger.error(f"Erro permanente — enviando para DLQ: {e}")
            await self._send_to_dlq(message, str(e))
            await message.ack()

        except (_TransientError, Exception) as e:
            is_transient = isinstance(e, _TransientError)
            label = "transitório" if is_transient else "inesperado"
            if retry_count >= _MAX_RETRIES:
                logger.error(
                    f"Erro {label} após {retry_count} tentativas — enviando para DLQ: {e}"
                )
                await self._send_to_dlq(message, f"max retries exceeded: {e}")
                await message.ack()
            else:
                delay = _RETRY_DELAYS[min(retry_count, len(_RETRY_DELAYS) - 1)]
                logger.warning(
                    f"Erro {label} (tentativa {retry_count + 1}/{_MAX_RETRIES}), "
                    f"reenfileirando em {delay}s: {e}"
                )
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                await self._requeue_with_retry(message, retry_count + 1)
                await message.ack()

        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    async def _send_to_dlq(self, message: IncomingMessage, reason: str) -> None:
        if self._channel is None or self._channel.is_closed:
            logger.error(f"Canal fechado — não foi possível enviar para DLQ. Motivo: {reason}")
            return
        headers = dict(message.headers or {})
        headers["x-dlq-reason"] = reason[:500]
        dlq_message = aio_pika.Message(
            body=message.body,
            headers=headers,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self._channel.default_exchange.publish(dlq_message, routing_key=self._dlq_name)
        logger.warning(f"Mensagem enviada para DLQ ({self._dlq_name}): {reason[:200]}")

    async def _requeue_with_retry(self, message: IncomingMessage, retry_count: int) -> None:
        if self._channel is None or self._channel.is_closed:
            logger.error("Canal fechado — não foi possível reenfileirar mensagem")
            return
        headers = dict(message.headers or {})
        headers["x-retry-count"] = retry_count
        new_message = aio_pika.Message(
            body=message.body,
            headers=headers,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self._channel.default_exchange.publish(new_message, routing_key=self.queue_name)

    async def _materialize_file(self, event: IngestRequestedEvent) -> str:
        """Retorna o path temporário. Levanta _PermanentError ou _TransientError em falha."""
        suffix = Path(event.file_name).suffix or ""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        try:
            if event.content_base64:
                estimated = len(event.content_base64) * 3 // 4
                if estimated > _MAX_DOWNLOAD_BYTES:
                    tmp.close()
                    Path(tmp_path).unlink(missing_ok=True)
                    raise _PermanentError(
                        f"content_base64 excede limite de {_MAX_DOWNLOAD_BYTES} bytes "
                        f"(estimado={estimated}, event_id={event.event_id})"
                    )
                tmp.write(base64.b64decode(event.content_base64))
                tmp.flush()
                tmp.close()
                return tmp_path

            if event.source_url:
                tmp.close()
                await self._download_to_file(event.source_url, tmp_path)
                return tmp_path

            tmp.close()
            raise _PermanentError(
                f"IngestRequestedEvent sem content_base64 nem source_url (event_id={event.event_id})"
            )

        except (_PermanentError, _TransientError):
            Path(tmp_path).unlink(missing_ok=True)
            raise
        except Exception as e:
            tmp.close()
            Path(tmp_path).unlink(missing_ok=True)
            raise _TransientError(f"Erro inesperado ao materializar arquivo: {e}") from e

    async def _download_to_file(self, url: str, dest_path: str) -> None:
        """Baixa url para dest_path. Levanta _PermanentError ou _TransientError."""
        import requests as req_lib

        def _do_download() -> None:
            # Session isolada: não herda HTTP_PROXY/HTTPS_PROXY do ambiente.
            session = req_lib.Session()
            session.trust_env = False

            current_url = url
            for _ in range(_MAX_REDIRECTS + 1):
                try:
                    resolved_ip = _validate_url_for_download(current_url)
                except ValueError as exc:
                    raise _PermanentError(f"URL bloqueada por política SSRF: {exc}") from exc

                parsed_current = urlparse(current_url)
                hostname = parsed_current.hostname or ""
                try:
                    with _pin_hostname(hostname, resolved_ip):
                        resp = session.get(
                            current_url,
                            stream=True,
                            timeout=self.download_timeout,
                            allow_redirects=False,
                        )
                except Exception as exc:
                    raise _TransientError(f"Erro de conexão ({current_url}): {exc}") from exc

                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "").strip()
                    resp.close()
                    if not location:
                        raise _PermanentError("Redirecionamento sem Location header")
                    current_url = urljoin(current_url, location)
                    continue

                if resp.status_code >= 400:
                    resp.close()
                    if 400 <= resp.status_code < 500:
                        raise _PermanentError(
                            f"HTTP {resp.status_code} em {current_url} (erro permanente)"
                        )
                    raise _TransientError(
                        f"HTTP {resp.status_code} em {current_url} (erro transitório)"
                    )

                downloaded = 0
                try:
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                downloaded += len(chunk)
                                if downloaded > _MAX_DOWNLOAD_BYTES:
                                    resp.close()
                                    raise _PermanentError(
                                        f"Download abortado: excedeu limite de "
                                        f"{_MAX_DOWNLOAD_BYTES} bytes ({current_url})"
                                    )
                                f.write(chunk)
                    return
                except (_PermanentError, _TransientError):
                    raise
                except Exception as exc:
                    raise _TransientError(f"Erro ao gravar arquivo ({current_url}): {exc}") from exc

            raise _PermanentError(
                f"Excedido limite de {_MAX_REDIRECTS} redirecionamentos para {url}"
            )

        await asyncio.to_thread(_do_download)

    async def _close(self):
        try:
            if self._channel and not self._channel.is_closed:
                await self._channel.close()
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
        except Exception as e:
            logger.warning(f"Erro ao fechar conexão do consumer: {e}")
        finally:
            self._channel = None
            self._connection = None

    def stop(self):
        self._stopped.set()
