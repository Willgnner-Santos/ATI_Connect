"""
Cliente Qdrant — busca vetorial com multi-tenancy (org_id + tenant_id).
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from qdrant_client import QdrantClient as _Qdrant
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    SearchParams,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import settings
from src.scope import scope_type_for


class QdrantClient:
    """Cliente Qdrant com filtro org_id + tenant_id em todas as operacoes."""

    def __init__(self) -> None:
        self._client: Optional[_Qdrant] = None

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    def _connect_with_retry(self) -> _Qdrant:
        client = _Qdrant(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
        client.get_collections()
        return client

    def _get_client(self) -> _Qdrant:
        if self._client is None:
            self._client = self._connect_with_retry()
        return self._client

    def _ensure_collection(self) -> None:
        try:
            client = self._get_client()
            collections = [c.name for c in client.get_collections().collections]
            if settings.QDRANT_COLLECTION not in collections:
                client.create_collection(
                    collection_name=settings.QDRANT_COLLECTION,
                    vectors_config={
                        "text": {"size": settings.EMBEDDING_DIMENSION, "distance": "Cosine"},
                        "image": {"size": settings.EMBEDDING_DIMENSION, "distance": "Cosine"},
                    },
                )
                logger.info(f"Colecao Qdrant '{settings.QDRANT_COLLECTION}' criada")
        except Exception as e:
            logger.error(f"Erro ao verificar/criar colecao Qdrant: {e}")

    def _build_filter(
        self,
        org_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        extra_filters: Optional[Dict[str, str]] = None,
        any_filters: Optional[Dict[str, List[Any]]] = None,
    ) -> Optional[Filter]:
        conditions = []
        if org_id:
            conditions.append(
                FieldCondition(key="org_id", match=MatchValue(value=org_id))
            )
        if tenant_id:
            conditions.append(
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
            )
        else:
            conditions.append(
                Filter(should=[
                    FieldCondition(
                        key="scope_type", match=MatchValue(value="organization")
                    ),
                    FieldCondition(
                        key="tenant_id", match=MatchValue(value="PLATFORM")
                    ),
                ])
            )
        if extra_filters:
            for key, value in extra_filters.items():
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
        if any_filters:
            for key, values in any_filters.items():
                if values:
                    conditions.append(FieldCondition(key=key, match=MatchAny(any=values)))
        if not conditions:
            return None
        return Filter(must=conditions)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    def _upsert_batch(self, points: List[PointStruct]) -> None:
        self._get_client().upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=points,
        )

    def batch_insert_vectors(
        self,
        org_id: str,
        tenant_id: Optional[str],
        vectors: List[Dict[str, Any]],
        vector_name: str = "text",
    ) -> int:
        """Insere vetores em batch. Retorna quantidade inserida."""
        if not vectors:
            return 0
        self._ensure_collection()
        batch_size = settings.QDRANT_UPSERT_BATCH_SIZE
        total_inserted = 0
        try:
            client = self._get_client()
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i : i + batch_size]
                points = []
                for v in batch:
                    point = PointStruct(
                        id=v["id"],
                        vector={vector_name: v["vector"]},
                        payload={
                            "org_id": org_id,
                            "scope_type": scope_type_for(tenant_id),
                            **({"tenant_id": tenant_id} if tenant_id else {}),
                            "document_id": v.get("document_id"),
                            "document_type": v.get("document_type"),
                            "content_type": v.get("content_type", "text_chunk"),
                            "page_number": v.get("page_number"),
                            "chunk_index": v.get("chunk_index"),
                            "text_excerpt": v.get("text_excerpt", "")[:2000],
                            "char_start": v.get("char_start"),
                            "char_end": v.get("char_end"),
                            "section_number": v.get("section_number"),
                            "section_title": v.get("section_title"),
                            "section_path": v.get("section_path"),
                            "context_ids": v.get("context_ids", []),
                            "timestamp": v.get("timestamp"),
                        },
                    )
                    points.append(point)
                self._upsert_batch(points)
                total_inserted += len(batch)
            return total_inserted
        except Exception as e:
            logger.error(f"Erro ao inserir vetores no Qdrant: {e}")
            return total_inserted

    def search_similar(
        self,
        query_vector: List[float],
        org_id: str,
        tenant_id: Optional[str],
        limit: int = 5,
        vector_name: str = "text",
        extra_filters: Optional[Dict[str, str]] = None,
        any_filters: Optional[Dict[str, List[Any]]] = None,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        try:
            client = self._get_client()
            query_filter = self._build_filter(org_id, tenant_id, extra_filters, any_filters)
            results = client.query_points(
                collection_name=settings.QDRANT_COLLECTION,
                query=query_vector,
                using=vector_name,
                limit=limit,
                query_filter=query_filter,
                search_params=SearchParams(exact=False, hnsw_ef=128),
            )
            output = []
            for hit in results.points:
                if score_threshold is not None and hit.score < score_threshold:
                    continue
                output.append({
                    "id": str(hit.id),
                    "score": hit.score,
                    "payload": hit.payload or {},
                })
            return output
        except Exception as e:
            logger.error(f"Erro na busca vetorial: {e}")
            return []

    def fetch_chunk_payloads(
        self,
        org_id: str,
        tenant_id: Optional[str],
        document_ids: Optional[List[str]] = None,
        context_ids: Optional[List[str]] = None,
        limit: int = 1000,
        content_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            client = self._get_client()
            _content_types = content_types or ["text_chunk"]
            conditions = [
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="content_type", match=MatchAny(any=_content_types))
                if len(_content_types) > 1
                else FieldCondition(key="content_type", match=MatchValue(value=_content_types[0])),
            ]
            if tenant_id:
                conditions.append(
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
                )
            else:
                conditions.append(
                    Filter(should=[
                        FieldCondition(
                            key="scope_type", match=MatchValue(value="organization")
                        ),
                        FieldCondition(
                            key="tenant_id", match=MatchValue(value="PLATFORM")
                        ),
                    ])
                )
            if document_ids:
                if len(document_ids) == 1:
                    conditions.append(
                        FieldCondition(key="document_id", match=MatchValue(value=document_ids[0]))
                    )
                else:
                    conditions.append(
                        FieldCondition(key="document_id", match=MatchAny(any=document_ids))
                    )
            if context_ids:
                conditions.append(
                    FieldCondition(key="context_ids", match=MatchAny(any=context_ids))
                    if len(context_ids) > 1
                    else FieldCondition(key="context_ids", match=MatchValue(value=context_ids[0]))
                )
            query_filter = Filter(must=conditions)
            all_points = []
            offset = None
            page_size = min(limit, 250)
            while len(all_points) < limit:
                results = client.scroll(
                    collection_name=settings.QDRANT_COLLECTION,
                    scroll_filter=query_filter,
                    limit=page_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                batch, next_offset = results
                all_points.extend(batch)
                if next_offset is None:
                    break
                offset = next_offset
            output = []
            for p in all_points[:limit]:
                output.append({"id": str(p.id), "payload": p.payload or {}})
            return output
        except Exception as e:
            logger.error(f"Erro ao buscar chunks no Qdrant: {e}")
            return []

    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        try:
            info = self._get_client().get_collection(settings.QDRANT_COLLECTION)
            return {
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": str(info.status),
            }
        except Exception as e:
            logger.error(f"Erro ao obter info da colecao: {e}")
            return None

    def delete_points_by_filter(
        self,
        org_id: str,
        tenant_id: Optional[str],
        extra_filters: Optional[Dict[str, str]] = None,
    ) -> bool:
        try:
            client = self._get_client()
            f = self._build_filter(org_id, tenant_id, extra_filters)
            if f is None:
                return False
            client.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=f,
            )
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar vetores: {e}")
            return False


qdrant_client = QdrantClient()
