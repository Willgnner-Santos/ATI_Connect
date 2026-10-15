"""
Catalogacao de imagens via VLM (Claude Vision).
Extrai metadados tecnicos e indexa para busca semantica.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from loguru import logger

from src.bedrock.bedrock_client import bedrock_client
from src.storage.s3_client import s3_client
from src.storage.postgres_client import postgres_client
from src.processors.image_extractor import ExtractedImage
from src.config import settings
from src.scope import scope_storage_segment


class ImageCataloger:
    def __init__(self):
        self.bedrock = bedrock_client
        self.s3 = s3_client
        self.postgres = postgres_client

    @staticmethod
    def _as_list(raw) -> List[str]:
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            return [t.strip() for t in raw.split(",") if t.strip()]
        return []

    def catalog_and_store_image(
        self,
        extracted_image: ExtractedImage,
        org_id: str,
        tenant_id: Optional[str],
        bronze_id: str,
        document_name: str,
        context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            doc_context = context or f"Imagem extraida de {document_name}"
            page_context = getattr(extracted_image, "page_context", None)
            if page_context:
                catalog_context = (
                    f"{doc_context}\n\n"
                    f"Trecho do manual onde a imagem aparece "
                    f"(pagina {extracted_image.page_number}):\n{page_context}"
                )
            else:
                catalog_context = doc_context

            vlm_result = self.bedrock.catalog_image(
                image_bytes=extracted_image.image_data,
                context=catalog_context,
            )
            if not vlm_result:
                logger.warning(f"VLM nao catalogou imagem da pagina {extracted_image.page_number}")
                return None

            image_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"image::{org_id}::{tenant_id}::{bronze_id}::{extracted_image.image_hash}",
            ))
            scope_segment = scope_storage_segment(tenant_id)
            s3_path = (
                f"images/{org_id}/{scope_segment}/{bronze_id}/"
                f"{image_id}.{extracted_image.format}"
            )

            upload_ok = self.s3.upload_bytes(
                bucket_name=settings.S3_BUCKET,
                object_name=s3_path,
                data=extracted_image.image_data,
                content_type=f"image/{extracted_image.format}",
            )
            if not upload_ok:
                logger.error(f"Falha ao fazer upload da imagem {image_id} para S3")
                return None

            image_metadata = {
                "id": image_id,
                "org_id": org_id,
                "tenant_id": tenant_id,
                "bronze_id": bronze_id,
                "page_number": extracted_image.page_number,
                "image_index": extracted_image.image_index,
                "image_hash": extracted_image.image_hash,
                "object_path": s3_path,
                "width": extracted_image.width,
                "height": extracted_image.height,
                "format": extracted_image.format,
                "file_size": extracted_image.file_size,
                "x_position": extracted_image.x_position,
                "y_position": extracted_image.y_position,
                "colorspace": extracted_image.colorspace,
                "page_context": page_context,
                "extracted_at": datetime.now(timezone.utc),
                "vlm_description": vlm_result.get("description"),
                "vlm_category": vlm_result.get("category"),
                "vlm_tags": self._as_list(vlm_result.get("tags")),
                "vlm_detected_objects": vlm_result.get("detected_objects", []),
                "vlm_technical_context": vlm_result.get("technical_context", {}),
                "vlm_text_content": vlm_result.get("text_content", []),
                "vlm_quality_indicators": vlm_result.get("quality_indicators", {}),
                "vlm_confidence": vlm_result.get("confidence_score", 0.0),
                "status": "cataloged",
            }

            logger.info(
                f"Imagem catalogada: {image_id} | pagina {extracted_image.page_number} | "
                f"categoria: {vlm_result.get('category')} | "
                f"{len(vlm_result.get('detected_objects', []))} objetos"
            )
            return image_metadata
        except Exception as e:
            logger.error(f"Erro ao catalogar imagem: {e}")
            return None

    def catalog_images_batch(
        self,
        extracted_images: List[ExtractedImage],
        org_id: str,
        tenant_id: Optional[str],
        bronze_id: str,
        document_name: str,
        context: Optional[str] = None,
        max_images: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        cataloged = []
        images_to_process = extracted_images
        if max_images and len(extracted_images) > max_images:
            logger.info(f"Limitando catalogacao a {max_images} imagens")
            images_to_process = extracted_images[:max_images]

        for idx, img in enumerate(images_to_process, start=1):
            try:
                logger.info(
                    f"Catalogando {idx}/{len(images_to_process)}: "
                    f"pagina {img.page_number}, {img.width}x{img.height}"
                )
                metadata = self.catalog_and_store_image(
                    extracted_image=img,
                    org_id=org_id,
                    tenant_id=tenant_id,
                    bronze_id=bronze_id,
                    document_name=document_name,
                    context=context,
                )
                if metadata:
                    cataloged.append(metadata)
            except Exception as e:
                logger.error(f"Erro ao catalogar imagem {idx}: {e}")
                continue

        logger.info(f"Catalogacao: {len(cataloged)}/{len(images_to_process)} imagens OK")
        return cataloged

    def vectorize_and_store_image_description(
        self,
        image_id: str,
        org_id: str,
        tenant_id: Optional[str],
        bronze_id: str,
        vlm_description: str,
        vlm_category: str,
        vlm_tags: List[str],
        page_number: int,
        context_ids: Optional[List[str]] = None,
        page_context: Optional[str] = None,
    ) -> bool:
        try:
            from src.storage.qdrant_client import qdrant_client

            enriched_text = f"{vlm_description}\n\nCategoria: {vlm_category}\n"
            if vlm_tags:
                enriched_text += f"Tags: {', '.join(vlm_tags)}\n"
            if page_context:
                enriched_text += f"Contexto do manual: {page_context}"

            embedding = self.bedrock.generate_embedding(enriched_text)
            if not embedding:
                logger.error(f"Falha ao gerar embedding para imagem {image_id}")
                return False

            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"image::{image_id}"))

            vectors = [{
                "id": point_id,
                "vector": embedding,
                "document_id": bronze_id,
                "document_type": "image",
                "content_type": "image",
                "page_number": page_number,
                "vlm_category": vlm_category,
                "vlm_tags": vlm_tags,
                "text_excerpt": vlm_description[:2000],
                "page_context": (page_context or "")[:2000],
                "context_ids": list(context_ids or []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]

            inserted = qdrant_client.batch_insert_vectors(
                org_id=org_id,
                tenant_id=tenant_id,
                vectors=vectors,
                vector_name="image",
            )
            logger.info(f"Imagem {image_id} indexada no Qdrant (categoria={vlm_category})")
            return inserted > 0
        except Exception as e:
            logger.error(f"Erro ao vetorizar imagem {image_id}: {e}")
            return False

    def get_image_stats(self, cataloged_images: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not cataloged_images:
            return {"total_cataloged": 0, "categories": {}, "avg_confidence": 0.0, "total_objects_detected": 0}
        categories = {}
        total_confidence = 0.0
        total_objects = 0
        for img in cataloged_images:
            cat = img.get("vlm_category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
            total_confidence += img.get("vlm_confidence", 0.0)
            raw_objs = img.get("vlm_detected_objects", [])
            if isinstance(raw_objs, str):
                try:
                    raw_objs = json.loads(raw_objs)
                except Exception:
                    raw_objs = []
            total_objects += len(raw_objs) if isinstance(raw_objs, list) else 0
        return {
            "total_cataloged": len(cataloged_images),
            "categories": categories,
            "avg_confidence": round(total_confidence / len(cataloged_images), 3),
            "total_objects_detected": total_objects,
        }


image_cataloger = ImageCataloger()
