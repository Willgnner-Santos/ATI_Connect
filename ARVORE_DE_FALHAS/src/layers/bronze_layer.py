"""
Camada Bronze — ingestao de arquivos brutos.
Hash SHA256, dedupe, upload para S3, registro no PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from src.storage import s3_client, postgres_client
from src.config import settings
from src.scope import scope_storage_segment, scope_type_for


def compute_sha256(file_path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.error(f"Erro ao calcular SHA256: {e}")
        return None


def file_size_bytes(file_path: str) -> Optional[int]:
    try:
        return Path(file_path).stat().st_size
    except Exception:
        return None


class BronzeLayer:
    def __init__(self):
        self.bucket = settings.S3_BUCKET

    def ingest_file(
        self,
        file_path: str,
        org_id: str,
        tenant_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not org_id:
            logger.error("ingest_file chamado sem org_id")
            return None
        _t = time.perf_counter()
        try:
            p = Path(file_path)
            if not p.exists():
                logger.error(f"Arquivo nao encontrado: {file_path}")
                return None

            size = file_size_bytes(file_path) or 0
            if size == 0:
                logger.error(f"Arquivo vazio: {file_path}")
                return None
            if size > settings.MAX_UPLOAD_BYTES:
                logger.error(f"Arquivo excede limite: {size} bytes")
                return None

            doc_hash = compute_sha256(file_path)
            if not doc_hash:
                return None

            existing = postgres_client.find_bronze_by_hash(org_id, tenant_id, doc_hash)
            if existing is not None:
                merged = postgres_client.merge_bronze_metadata(
                    org_id, tenant_id, str(existing["id"]), metadata or {}
                )
                logger.info(f"Duplicado detectado — reaproveitando bronze_id: {existing['id']}")
                return existing["id"]

            doc_id = str(uuid.uuid4())
            file_type = self._determine_type(p.suffix.lower())
            file_metadata = self._extract_metadata(file_path, file_type)
            if metadata:
                file_metadata.update(metadata)

            original_name = (metadata or {}).get("filename") or p.name

            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            scope_segment = scope_storage_segment(tenant_id)
            object_name = f"{org_id}/{scope_segment}/{ts}_{doc_id}_{original_name}"

            upload_ok = s3_client.upload_file(
                bucket_name=self.bucket,
                object_name=object_name,
                file_path=file_path,
                metadata={
                    "doc_id": doc_id,
                    "org_id": org_id,
                    "scope_type": scope_type_for(tenant_id),
                    **({"tenant_id": tenant_id} if tenant_id else {}),
                    "document_hash": doc_hash,
                    "original_name": original_name,
                },
            )
            if not upload_ok:
                return None

            doc_data = {
                "id": doc_id,
                "org_id": org_id,
                "tenant_id": tenant_id,
                "file_name": original_name,
                "file_type": file_type,
                "file_size": size,
                "document_hash": doc_hash,
                "object_path": f"{self.bucket}/{object_name}",
                "metadata": json.dumps(file_metadata, ensure_ascii=False),
                "status": "ingested",
            }
            if not postgres_client.insert_bronze_document(doc_data):
                return None

            postgres_client.log_processing({
                "id": str(uuid.uuid4()),
                "org_id": org_id,
                "tenant_id": tenant_id,
                "document_id": doc_id,
                "layer": "bronze",
                "operation": "ingest",
                "status": "success",
                "metadata": json.dumps({
                    "file_name": p.name,
                    "file_type": file_type,
                    "file_size": size,
                    "duration_ms": int((time.perf_counter() - _t) * 1000),
                }),
            })
            logger.info(f"Bronze: {doc_id} (org={org_id}, tenant={tenant_id})")
            return doc_id
        except Exception as e:
            logger.error(f"Erro ao ingerir: {e}")
            return None

    def _determine_type(self, ext: str) -> str:
        mapping = {
            ".pdf": "pdf", ".txt": "text", ".md": "text",
            ".docx": "docx", ".doc": "docx",
        }
        return mapping.get(ext, "unknown")

    def _extract_metadata(self, file_path: str, file_type: str) -> Dict[str, Any]:
        try:
            if file_type == "pdf":
                from src.processors.pdf_processor import pdf_processor
                return pdf_processor.get_metadata(file_path)
        except Exception as e:
            logger.error(f"Erro ao extrair metadata: {e}")
        return {"file_name": Path(file_path).name, "file_size": Path(file_path).stat().st_size}


bronze_layer = BronzeLayer()
