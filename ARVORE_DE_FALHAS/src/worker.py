"""
Worker — processa ingest para cada mensagem recebida.

Uso:
    python -m src.worker

Flags:
    ENABLE_OUTBOX_PUBLISHER=false  — desliga o outbox publisher
    ENABLE_REQUEST_CONSUMER=false  — desliga o request consumer
"""
from __future__ import annotations

import asyncio
import signal
from typing import Any, Dict, Optional

from loguru import logger

from src.config import settings
from src.orchestrator import orchestrator


def process_ingest_request(
    org_id: str,
    tenant_id: str,
    file_path: str,
    full_pipeline: bool,
    metadata: Optional[Dict[str, Any]] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    generate_fault_tree: bool = False,
) -> Dict[str, Any]:
    """Handler chamado pelo RequestConsumer para cada requisição da fila."""
    metadata = metadata or {}
    if full_pipeline:
        result = orchestrator.run_full_pipeline(
            file_path=file_path,
            org_id=org_id,
            tenant_id=tenant_id,
            metadata=metadata,
        )
    else:
        result = orchestrator.run_bronze_only(
            file_path=file_path,
            org_id=org_id,
            tenant_id=tenant_id,
            metadata=metadata,
        )

    if (
        target_type
        and target_id
        and str(result.get("status") or "").lower() in {"completed", "partial"}
    ):
        try:
            from src.fault_tree.service import process_ingest_target

            file_name = metadata.get("filename", "")
            model_id = target_id if target_type == "model" else None
            result["fault_tree"] = process_ingest_target(
                pipeline_result=result,
                org_id=org_id,
                tenant_id=tenant_id,
                target_type=target_type,
                target_id=target_id,
                model_id=model_id,
                file_name=file_name,
                generate_fault_tree=generate_fault_tree,
            )
        except Exception as exc:
            logger.error(f"Pós-processamento da árvore falhou (worker): {exc}")
            result.setdefault("warnings", []).append(str(exc))
            result["fault_tree"] = {
                "target_type": target_type,
                "target_id": target_id,
                "generation_status": "failed",
                "error": str(exc),
            }

    return {
        "status": result.get("status", "error"),
        "result": result,
    }


async def main():
    from src.storage.postgres_client import postgres_client
    try:
        postgres_client.create_tables()
        logger.info("Tabelas Postgres OK")
    except Exception as e:
        logger.warning(f"create_tables falhou no startup do worker: {e}")

    tasks = []
    outbox_worker = None
    request_consumer = None

    if settings.ENABLE_OUTBOX_PUBLISHER:
        from src.messaging.outbox_worker import OutboxWorker
        outbox_worker = OutboxWorker()
        tasks.append(asyncio.create_task(outbox_worker.run(), name="outbox_worker"))
    else:
        logger.info("ENABLE_OUTBOX_PUBLISHER=false — outbox worker desligado")

    if settings.ENABLE_REQUEST_CONSUMER:
        from src.messaging.request_consumer import RequestConsumer
        request_consumer = RequestConsumer(pipeline_handler=process_ingest_request)
        tasks.append(asyncio.create_task(request_consumer.run(), name="request_consumer"))
    else:
        logger.info("ENABLE_REQUEST_CONSUMER=false — request consumer desligado")

    if not tasks:
        logger.error("Nenhum worker habilitado — encerrando")
        return

    stop_event = asyncio.Event()

    def _request_stop(*_):
        logger.info("Sinal de parada recebido — encerrando workers")
        stop_event.set()
        if outbox_worker:
            outbox_worker.stop()
        if request_consumer:
            request_consumer.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_stop())

    logger.info(f"Worker iniciado com {len(tasks)} task(s)")
    try:
        await asyncio.gather(*tasks, return_exceptions=False)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Worker encerrado")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário")
