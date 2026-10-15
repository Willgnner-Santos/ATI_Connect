"""
Orquestrador do pipeline — Route A only (Bronze → Envelope estruturado + vetores).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.layers.bronze_layer import bronze_layer
from src.layers.silver_layer import silver_layer
from src.storage.postgres_client import postgres_client
from src.config import settings
from src.messaging import outbox_repository, DocumentProcessedEvent, DocumentFailedEvent


class IngestOrchestrator:
    """Orquestra o pipeline completo de ingestão e processamento."""

    def __init__(self):
        self.max_workers = settings.MAX_WORKERS

    def run_full_pipeline(
        self,
        file_path: str,
        org_id: str,
        tenant_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not org_id:
            return {
                "file_path": file_path,
                "status": "error",
                "errors": ["org_id é obrigatório"],
            }
        try:
            logger.info(
                f"Iniciando pipeline completo: {file_path} "
                f"(org={org_id}, tenant={tenant_id})"
            )

            result = {
                "file_path": file_path,
                "org_id": org_id,
                "tenant_id": tenant_id,
                "bronze_id": None,
                "envelope_id": None,
                "document_type": None,
                "is_structured": None,
                "status": "started",
                "errors": [],
                "chunks_stored": 0,
                "chunks_generated": 0,
                "embeddings_succeeded": 0,
                "embeddings_failed": 0,
                "vector_index_status": "not_run",
                "warnings": [],
                "images_saved": 0,
            }
            file_name_for_event = (
                (metadata or {}).get("filename") or Path(file_path).name
            )

            # Camada Bronze
            logger.info("Etapa 1/2: Ingestão (Bronze)")
            bronze_id = bronze_layer.ingest_file(
                file_path, org_id=org_id, tenant_id=tenant_id, metadata=metadata
            )
            if not bronze_id:
                result["status"] = "failed_at_bronze"
                result["errors"].append("Falha na ingestão Bronze")
                self._emit_failed_event(
                    org_id=org_id,
                    tenant_id=tenant_id,
                    stage="bronze",
                    file_name=file_name_for_event,
                    errors=result["errors"],
                )
                return result

            result["bronze_id"] = bronze_id

            # Camada Silver
            logger.info("Etapa 2/2: Processamento (Silver)")
            silver_result = silver_layer.process_bronze_document(
                bronze_id, org_id=org_id, tenant_id=tenant_id
            )
            if silver_result is None:
                result["status"] = "failed_at_silver"
                result["errors"].append("Falha no processamento Silver")
                self._emit_failed_event(
                    org_id=org_id,
                    tenant_id=tenant_id,
                    stage="silver",
                    bronze_id=bronze_id,
                    file_name=file_name_for_event,
                    errors=result["errors"],
                )
                return result

            result["envelope_id"] = silver_result.envelope_id
            result["document_type"] = silver_result.document_type
            result["is_structured"] = silver_result.is_structured
            result["chunks_stored"] = int(getattr(silver_result, "chunks_stored", 0) or 0)
            result["chunks_generated"] = int(getattr(silver_result, "chunks_generated", 0) or 0)
            result["embeddings_succeeded"] = int(getattr(silver_result, "embeddings_succeeded", 0) or 0)
            result["embeddings_failed"] = int(getattr(silver_result, "embeddings_failed", 0) or 0)
            result["vector_index_status"] = str(
                getattr(silver_result, "vector_index_status", "not_run") or "not_run"
            )
            result["images_saved"] = int(getattr(silver_result, "images_saved", 0) or 0)

            if result["vector_index_status"] in {"failed", "partial"}:
                result["status"] = "partial"
                result["warnings"].append(
                    "Documento processado, mas a indexação vetorial textual "
                    f"terminou como {result['vector_index_status']}."
                )
            else:
                result["status"] = "completed"

            self._emit_processed_event(
                org_id=org_id,
                tenant_id=tenant_id,
                bronze_id=bronze_id,
                envelope_id=silver_result.envelope_id,
                document_type=silver_result.document_type,
                is_structured=silver_result.is_structured,
                file_name=file_name_for_event,
                vector_index_status=result["vector_index_status"],
            )
            logger.info(f"Pipeline concluído: {result}")
            return result

        except Exception as e:
            logger.error(f"Erro no pipeline: {e}")
            self._emit_failed_event(
                org_id=org_id,
                tenant_id=tenant_id,
                stage="unknown",
                file_name=(metadata or {}).get("filename") or Path(file_path).name,
                errors=[str(e)],
            )
            return {
                "file_path": file_path,
                "org_id": org_id,
                "tenant_id": tenant_id,
                "status": "error",
                "errors": [str(e)],
            }

    def _emit_processed_event(
        self,
        org_id: str,
        tenant_id: Optional[str],
        bronze_id: str,
        envelope_id: Optional[str],
        document_type: Optional[str],
        is_structured: Optional[bool],
        file_name: str,
        vector_index_status: Optional[str] = None,
    ) -> None:
        # save_event_with_session garante que a inserção na outbox e qualquer
        # leitura de estado ocorrem na mesma transação. O commit é feito aqui.
        # TODO(R09): mover este commit para dentro da transação da camada Silver,
        # eliminando o gap residual entre o commit do Silver e este.
        try:
            enrichment = self._load_event_enrichment(
                org_id=org_id, tenant_id=tenant_id, bronze_id=bronze_id
            )
            event = DocumentProcessedEvent(
                event_id=str(uuid.uuid4()),
                org_id=org_id,
                tenant_id=tenant_id,
                bronze_id=bronze_id,
                envelope_id=envelope_id,
                file_name=file_name,
                file_type=enrichment.get("file_type") or "unknown",
                document_type=document_type or enrichment.get("document_type"),
                is_structured=is_structured,
                object_path=enrichment.get("object_path"),
                vector_index_status=vector_index_status,
            )
            with postgres_client.get_session() as session:
                outbox_repository.save_event_with_session(event, session)
                session.commit()
        except Exception as e:
            logger.error(f"Falha ao salvar DocumentProcessedEvent na outbox: {e}")

    def _emit_failed_event(
        self,
        org_id: str,
        tenant_id: Optional[str],
        stage: str,
        errors: List[str],
        file_name: Optional[str] = None,
        bronze_id: Optional[str] = None,
    ) -> None:
        try:
            event = DocumentFailedEvent(
                event_id=str(uuid.uuid4()),
                org_id=org_id,
                tenant_id=tenant_id,
                stage=stage,
                bronze_id=bronze_id,
                file_name=file_name,
                errors=errors,
            )
            with postgres_client.get_session() as session:
                outbox_repository.save_event_with_session(event, session)
                session.commit()
        except Exception as e:
            logger.error(f"Falha ao salvar DocumentFailedEvent na outbox: {e}")

    def _load_event_enrichment(
        self, org_id: str, tenant_id: Optional[str], bronze_id: str
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        try:
            from sqlalchemy import text
            with postgres_client.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT file_type, object_path FROM bronze_documents "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                    ),
                    {"id": bronze_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is not None:
                    data["file_type"] = row.file_type
                    data["object_path"] = row.object_path
        except Exception as e:
            logger.warning(f"Enriquecimento do evento falhou (seguindo sem): {e}")
        return data

    def run_bronze_only(
        self,
        file_path: str,
        org_id: str,
        tenant_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info(
            f"Executando apenas camada Bronze: {file_path} "
            f"(org={org_id}, tenant={tenant_id})"
        )
        bronze_id = bronze_layer.ingest_file(
            file_path, org_id=org_id, tenant_id=tenant_id, metadata=metadata
        )
        return {
            "file_path": file_path,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "bronze_id": bronze_id,
            "status": "completed" if bronze_id else "failed",
        }

    def get_pipeline_status(
        self, org_id: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            with postgres_client.get_session() as session:
                from sqlalchemy import text

                where_clause = "org_id = :org_id"
                query_params: Dict[str, Any] = {"org_id": org_id}
                if tenant_id:
                    where_clause += " AND tenant_id = :tenant_id"
                    query_params["tenant_id"] = tenant_id
                else:
                    where_clause += " AND tenant_id IS NULL"

                bronze_count = session.execute(
                    text(
                        f"SELECT COUNT(*) FROM bronze_documents WHERE {where_clause}"
                    ),
                    query_params,
                ).scalar()

                envelope_count = session.execute(
                    text(
                        f"SELECT COUNT(*) FROM envelope_documents WHERE {where_clause}"
                    ),
                    query_params,
                ).scalar()

                image_count = session.execute(
                    text(
                        f"SELECT COUNT(*) FROM document_images WHERE {where_clause}"
                    ),
                    query_params,
                ).scalar()

                return {
                    "org_id": org_id,
                    "tenant_id": tenant_id,
                    "documents": {
                        "bronze": bronze_count,
                        "envelopes": envelope_count,
                        "images": image_count,
                    },
                }
        except Exception as e:
            logger.error(f"Erro ao obter status: {e}")
            return {"error": str(e)}

    def initialize_infrastructure(self) -> bool:
        try:
            logger.info("Inicializando infraestrutura...")
            postgres_client.create_tables()
            logger.info("Infraestrutura inicializada com sucesso")
            return True
        except Exception as e:
            logger.error(f"Erro ao inicializar infraestrutura: {e}")
            return False


orchestrator = IngestOrchestrator()
