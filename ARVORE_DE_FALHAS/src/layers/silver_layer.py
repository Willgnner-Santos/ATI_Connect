"""
Camada Silver — processamento de documentos bronze.
Route A only: OCR cascata → classificacao → extracao → imagens → chunks → embeddings.
"""
from __future__ import annotations

import json
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.storage import s3_client, postgres_client, qdrant_client
from src.bedrock.bedrock_client import bedrock_client
from src.processors import pdf_processor
from src.processors.image_extractor import image_extractor
from src.processors.image_cataloger import image_cataloger
from src.config import settings
from src.envelope import (
    envelope_builder, BuildContext, chunk_pages, envelope_repository,
)


@dataclass
class SilverResult:
    silver_id: Optional[str] = None
    envelope_id: Optional[str] = None
    document_type: Optional[str] = None
    is_structured: Optional[bool] = None
    chunks_stored: int = 0
    chunks_generated: int = 0
    embeddings_succeeded: int = 0
    embeddings_failed: int = 0
    qdrant_failed: int = 0
    vector_index_status: str = "not_run"
    images_saved: int = 0


class SilverLayer:
    def process_bronze_document(
        self, bronze_id: str, org_id: str, tenant_id: Optional[str]
    ) -> Optional[SilverResult]:
        try:
            with postgres_client.get_session() as session:
                from sqlalchemy import text
                row = session.execute(
                    text(
                        "SELECT id, org_id, tenant_id, file_name, file_type, "
                        "object_path, metadata, document_hash "
                        "FROM bronze_documents "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                    ),
                    {"id": bronze_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is None:
                    logger.error(f"Bronze nao encontrado: {bronze_id}")
                    return None
                doc = dict(row._mapping)

            file_type = doc.get("file_type", "unknown")
            s3_path = doc.get("object_path", "")
            if "/" not in s3_path:
                logger.error(f"object_path invalido: {s3_path}")
                return None
            bucket, object_name = s3_path.split("/", 1)

            with tempfile.NamedTemporaryFile(
                delete=False, suffix=Path(doc.get("file_name", "")).suffix
            ) as tmp:
                tmp_path = tmp.name
            try:
                if not s3_client.download_file(bucket, object_name, tmp_path):
                    logger.error(f"Falha ao baixar do S3: {s3_path}")
                    return None

                extracted_text, method = pdf_processor.extract_text_with_method(tmp_path)
                if not extracted_text:
                    logger.warning(f"Nenhum texto extraido de {bronze_id}")
                    return None

                pages, _pages_method = pdf_processor.extract_pages(tmp_path)
                context_ids = self._extract_context_ids(doc.get("metadata"))

                images_saved = self._extract_and_catalog_images(
                    tmp_path, org_id, tenant_id, bronze_id, doc.get("file_name", ""), extracted_text
                )

                build_ctx = BuildContext(
                    org_id=org_id,
                    tenant_id=tenant_id,
                    bronze_id=bronze_id,
                    document_hash=doc.get("document_hash", ""),
                    file_name=doc.get("file_name", ""),
                )
                envelope = envelope_builder.build(build_ctx, extracted_text)
                if not envelope_repository.save_envelope(envelope):
                    raise RuntimeError(
                        f"Falha ao persistir envelope para documento {bronze_id}"
                    )

                chunks_stored, embeddings_ok, embeddings_fail, chunks_generated = self._store_chunks(
                    org_id, tenant_id, envelope.envelope_id, pages, context_ids
                )
                qdrant_failed = max(0, embeddings_ok - chunks_stored)

                if chunks_generated == 0:
                    vector_index_status = "empty"
                elif chunks_stored == chunks_generated:
                    vector_index_status = "success"
                elif chunks_stored > 0:
                    vector_index_status = "partial"
                else:
                    vector_index_status = "failed"

                postgres_client.log_processing({
                    "id": str(uuid.uuid4()),
                    "org_id": org_id,
                    "tenant_id": tenant_id,
                    "document_id": bronze_id,
                    "layer": "silver",
                    "operation": "process",
                    "status": vector_index_status,
                    "metadata": json.dumps({
                        "method": method,
                        "chars": len(extracted_text),
                        "chunks_generated": chunks_generated,
                        "embeddings_succeeded": embeddings_ok,
                        "embeddings_failed": embeddings_fail,
                        "qdrant_failed": qdrant_failed,
                    }),
                })

                return SilverResult(
                    envelope_id=envelope.envelope_id,
                    document_type=envelope.document_type.value,
                    is_structured=envelope.is_structured,
                    chunks_stored=chunks_stored,
                    chunks_generated=chunks_generated,
                    embeddings_succeeded=embeddings_ok,
                    embeddings_failed=embeddings_fail,
                    qdrant_failed=qdrant_failed,
                    vector_index_status=vector_index_status,
                    images_saved=images_saved,
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Erro no silver layer: {e}")
            return None

    def _extract_context_ids(self, metadata_raw: Any) -> List[str]:
        if not metadata_raw:
            return []
        try:
            meta = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
            raw_ids = meta.get("context_ids", [])
            return [str(x) for x in raw_ids if x] if isinstance(raw_ids, list) else []
        except Exception:
            return []

    def _extract_and_catalog_images(
        self, file_path: str, org_id: str, tenant_id: Optional[str],
        bronze_id: str, doc_name: str, raw_text: str,
    ) -> int:
        try:
            extracted = image_extractor.extract_images(file_path)
            if not extracted:
                return 0
            deduped = image_extractor.deduplicate_images(extracted)
            cataloged = image_cataloger.catalog_images_batch(
                extracted_images=deduped,
                org_id=org_id,
                tenant_id=tenant_id,
                bronze_id=bronze_id,
                document_name=doc_name,
                context=f"Manual tecnico: {doc_name}",
                max_images=settings.IMAGE_CATALOG_MAX_PER_DOC,
            )
            saved = 0
            for img_meta in cataloged:
                if postgres_client.insert_document_image(img_meta):
                    saved += 1
                vectorized = image_cataloger.vectorize_and_store_image_description(
                    image_id=img_meta["id"],
                    org_id=org_id,
                    tenant_id=tenant_id,
                    bronze_id=bronze_id,
                    vlm_description=img_meta.get("vlm_description", ""),
                    vlm_category=img_meta.get("vlm_category", "outro"),
                    vlm_tags=img_meta.get("vlm_tags", []),
                    page_number=img_meta.get("page_number", 1),
                    context_ids=[bronze_id],
                    page_context=img_meta.get("page_context"),
                )
            logger.info(f"Imagens: {saved} salvas de {len(deduped)} extraidas")
            return saved
        except Exception as e:
            logger.error(f"Erro no pipeline de imagens: {e}")
            return 0

    def _store_chunks(
        self, org_id: str, tenant_id: Optional[str], document_id: str,
        pages: List, context_ids: List[str],
    ) -> tuple:
        """Retorna (stored, ok, fail, total_chunks)."""
        if not pages:
            return 0, 0, 0, 0
        chunk_size, overlap = self._select_chunking_window(pages)
        chunks = chunk_pages(pages, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            return 0, 0, 0, 0
        total_chunks = len(chunks)

        vectors = []
        ok = 0
        fail = 0
        for chunk in chunks:
            embedding = bedrock_client.generate_embedding(chunk.text)
            if embedding:
                vectors.append({
                    "id": str(uuid.uuid5(
                        uuid.NAMESPACE_URL, f"{document_id}::{chunk.index}"
                    )),
                    "vector": embedding,
                    "document_id": document_id,
                    "document_type": "manual_tecnico",
                    "content_type": "text_chunk",
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.index,
                    "text_excerpt": chunk.text[:settings.CHUNK_PAYLOAD_MAX_CHARS],
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "section_number": chunk.section_number,
                    "section_title": chunk.section_title,
                    "section_path": chunk.section_path,
                    "context_ids": context_ids,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                ok += 1
            else:
                fail += 1

        stored = qdrant_client.batch_insert_vectors(org_id, tenant_id, vectors)
        return stored, ok, fail, total_chunks

    @staticmethod
    def _select_chunking_window(pages: List) -> tuple[int, int]:
        """Ajusta chunks para documentos muito densos sem alterar o contrato externo."""
        text_lengths = [
            len(page[1] or "")
            for page in pages
            if isinstance(page, (tuple, list)) and len(page) >= 2
        ]
        if not text_lengths:
            return settings.CHUNK_SIZE, min(100, settings.CHUNK_SIZE - 1)
        density = sum(text_lengths) / len(text_lengths)
        if density > 6000:
            size = min(settings.CHUNK_SIZE + 300, 1600)
        elif density < 800:
            size = max(settings.CHUNK_SIZE - 250, 400)
        else:
            size = settings.CHUNK_SIZE
        return size, min(max(50, size // 10), size - 1)


silver_layer = SilverLayer()
