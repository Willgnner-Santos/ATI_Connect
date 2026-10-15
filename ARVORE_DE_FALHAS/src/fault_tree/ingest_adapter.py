"""
IngestAdapter — substitui o IngestClient do LTS com chamadas diretas ao
Qdrant e PostgreSQL do serviço arvore-de-falhas (multi-tenant ATI).

Interface esperada pelo generator.py:
  adapter.get_document(envelope_id)              → {"extracted_data": {...}}
  adapter.get_document_chunks(doc_id, ctx, lim)  → {"chunks": [...]}
  adapter.search_text(query, ctx, lim, score)    → {"citations": [...]}
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.bedrock.bedrock_client import bedrock_client
from src.storage.postgres_client import postgres_client
from src.storage.qdrant_client import qdrant_client


class IngestAdapter:
    def __init__(self, *, org_id: str, tenant_id: str | None) -> None:
        self.org_id = org_id
        self.tenant_id = tenant_id

    # ── get_document ──────────────────────────────────────────────

    def get_document(self, envelope_id: str) -> dict[str, Any]:
        """Return envelope document as {"extracted_data": {...}}."""
        try:
            row = postgres_client.get_envelope_by_id(
                envelope_id, self.org_id, self.tenant_id
            )
            if row is None:
                return {}
            extracted = row.get("extracted_data") or {}
            if isinstance(extracted, str):
                try:
                    extracted = json.loads(extracted)
                except Exception:
                    extracted = {}
            return {"extracted_data": extracted}
        except Exception as e:
            logger.warning(f"IngestAdapter.get_document error: {e}")
            return {}

    # ── get_document_chunks ───────────────────────────────────────

    def get_document_chunks(
        self,
        document_id: str,
        context_ids: list[str] | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Return Qdrant text and image chunks as {"chunks": [...]}.

        Direct lookup by document_id only — context_ids are intentionally NOT
        forwarded to the Qdrant filter here. The document_id (envelope_id) is
        already a specific UUID identifier; adding context_ids to the must-clause
        causes zero results whenever the ingest pipeline stored chunks with
        equipment/model context_ids instead of document-level IDs.
        Context-based isolation is enforced in search_text (semantic path).
        """
        try:
            points = qdrant_client.fetch_chunk_payloads(
                org_id=self.org_id,
                tenant_id=self.tenant_id,
                document_ids=[document_id] if document_id else None,
                context_ids=None,
                limit=limit,
                content_types=["text_chunk", "image"],
            )
            chunks = [_point_to_chunk(p) for p in points]
            return {"chunks": chunks}
        except Exception as e:
            logger.warning(f"IngestAdapter.get_document_chunks error: {e}")
            return {"chunks": []}

    # ── search_text ───────────────────────────────────────────────

    def search_text(
        self,
        query: str,
        context_ids: list[str] | None = None,
        limit: int = 20,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """Embed query → vector search → return {"citations": [...]}.

        When context_ids are provided, only chunks whose document_id or
        context_ids payload intersects with the requested context_ids are
        returned. This mirrors the LTS isolation guarantee: a document from
        another asset/tenant cannot contaminate the current fault analysis.
        """
        try:
            vector = bedrock_client.generate_embedding(query)
            if not vector:
                return {"citations": []}

            ctx_set = set(context_ids or [])

            # Fetch extra candidates so post-filtering doesn't leave us short.
            fetch_limit = limit * 4 if ctx_set else limit
            results = qdrant_client.search_similar(
                query_vector=vector,
                org_id=self.org_id,
                tenant_id=self.tenant_id,
                limit=fetch_limit,
                vector_name="text",
            )

            citations = []
            for hit in results:
                if hit["score"] < min_score:
                    continue
                payload = hit.get("payload", {})

                # Document-level isolation: keep only chunks that belong to
                # the document(s) identified by context_ids.
                if ctx_set:
                    doc_id = payload.get("document_id") or ""
                    chunk_ctx = set(payload.get("context_ids") or [])
                    if doc_id not in ctx_set and not chunk_ctx.intersection(ctx_set):
                        continue

                citations.append({
                    "id": hit["id"],
                    "score": hit["score"],
                    "text": payload.get("text_excerpt", ""),
                    "page_number": payload.get("page_number"),
                    "document_id": payload.get("document_id"),
                    "context_ids": payload.get("context_ids", []),
                    "chunk_index": payload.get("chunk_index"),
                })
                if len(citations) >= limit:
                    break

            return {"citations": citations}
        except Exception as e:
            logger.warning(f"IngestAdapter.search_text error: {e}")
            return {"citations": []}


def _point_to_chunk(point: dict[str, Any]) -> dict[str, Any]:
    payload = point.get("payload", {})
    return {
        "id": point.get("id"),
        "text": payload.get("text_excerpt", ""),
        "page_number": payload.get("page_number"),
        "document_id": payload.get("document_id"),
        "context_ids": payload.get("context_ids", []),
        "chunk_index": payload.get("chunk_index"),
        "char_start": payload.get("char_start"),
        "char_end": payload.get("char_end"),
    }
