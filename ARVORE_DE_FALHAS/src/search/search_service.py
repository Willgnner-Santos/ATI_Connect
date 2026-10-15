"""
Servico de busca semantica (RAG).

Fluxo:
    1. Gera embedding da consulta via Titan (Bedrock).
    2. Busca vetores similares no Qdrant filtrados por org_id + tenant_id.
    3. Retorna os chunks mais relevantes (texto + metadados) para o front/LLM.
"""
from __future__ import annotations

import time
import re
import unicodedata
from typing import Any, Dict, List, Optional

from loguru import logger

from src.bedrock.bedrock_client import bedrock_client
from src.storage.qdrant_client import qdrant_client
from src.config import settings
from .schemas import Citation, SearchMetadata, SearchRequest, SearchResponse, SearchResultItem


class SearchService:
    def search(
        self,
        request: SearchRequest,
        org_id: str,
        tenant_id: Optional[str],
    ) -> SearchResponse:
        started_at = time.perf_counter()
        if not request.query.strip():
            return SearchResponse(query=request.query)

        if not org_id:
            logger.error("search chamado sem org_id")
            return SearchResponse(query=request.query)

        query_vector = bedrock_client.generate_embedding(request.query)
        if not query_vector:
            logger.error("Falha ao gerar embedding da consulta")
            return SearchResponse(query=request.query)

        extra_filters: Dict[str, str] = {}
        if request.document_type:
            extra_filters["document_type"] = request.document_type
        if request.content_type:
            extra_filters["content_type"] = request.content_type

        vector_name = "image" if request.content_type == "image" else "text"

        hits = qdrant_client.search_similar(
            query_vector=query_vector,
            org_id=org_id,
            tenant_id=tenant_id,
            limit=min(
                max(request.limit * settings.SEARCH_POOL_MULTIPLIER, request.limit),
                settings.SEARCH_POOL_MAX,
            ),
            vector_name=vector_name,
            extra_filters=extra_filters or None,
            any_filters={"context_ids": request.context_ids} if request.context_ids else None,
            score_threshold=request.min_score,
        )

        results: List[SearchResultItem] = []
        for hit in hits:
            payload = hit.get("payload") or {}
            results.append(
                SearchResultItem(
                    id=hit.get("id", ""),
                    score=hit.get("score", 0.0),
                    document_id=payload.get("document_id"),
                    document_type=payload.get("document_type"),
                    content_type=payload.get("content_type"),
                    page_number=payload.get("page_number"),
                    chunk_index=payload.get("chunk_index"),
                    text_excerpt=payload.get("text_excerpt"),
                    section_number=payload.get("section_number"),
                    section_title=payload.get("section_title"),
                    section_path=payload.get("section_path"),
                    payload=payload,
                )
            )

        if request.rerank and results:
            self._rerank_results(request.query, results)

        results = results[:request.limit]
        citations = [
            Citation(
                document_id=item.document_id,
                page_number=item.page_number,
                chunk_index=item.chunk_index,
                section_number=item.section_number,
                section_title=item.section_title,
                section_path=item.section_path,
                confidence_score=item.score,
                rerank_score=item.rerank_score,
            )
            for item in results
        ]
        answer = self._generate_answer(request.query, results) if request.generate_answer and results else None
        metadata = SearchMetadata(
            chunks_found=len(hits),
            chunks_used=len(results),
            processing_time_ms=round((time.perf_counter() - started_at) * 1000),
        )
        logger.info(
            "Busca concluida: found={}, used={}, rerank={}, generate_answer={}",
            len(hits), len(results), request.rerank, bool(answer),
        )

        return SearchResponse(
            query=request.query, results=results, total=len(results), answer=answer,
            citations=citations, metadata=metadata,
        )

    @staticmethod
    def _rerank_results(query: str, results: List[SearchResultItem]) -> None:
        """Combina score vetorial, coincidência lexical e LLM, com fallback local."""
        candidates = results[:settings.SEARCH_RERANK_MAX_CANDIDATES]
        query_terms = _tokenize_lexical(query)
        lexical_scores = [_lexical_score(query_terms, item.text_excerpt or "") for item in candidates]
        lexical_max = max(lexical_scores, default=0.0) or 1.0
        try:
            llm_scores = bedrock_client.rerank_documents(
                query, [{"text_excerpt": item.text_excerpt or ""} for item in candidates]
            )
        except Exception as exc:
            logger.warning(f"Reranking LLM indisponivel; usando ranking local: {exc}")
            llm_scores = []
        weight = max(0.0, min(1.0, settings.SEARCH_RERANK_WEIGHT))
        for index, item in enumerate(candidates):
            vector_score = max(0.0, min(1.0, item.score))
            lexical_score = lexical_scores[index] / lexical_max if settings.SEARCH_LEXICAL_BOOST_ENABLED else 0.0
            if len(llm_scores) == len(candidates):
                score = weight * llm_scores[index] + (1 - weight) * (0.6 * vector_score + 0.4 * lexical_score)
            else:
                score = 0.6 * vector_score + 0.4 * lexical_score
            item.rerank_score = round(score, 6)
        candidates.sort(key=lambda item: item.rerank_score or 0.0, reverse=True)
        results[:len(candidates)] = candidates

    @staticmethod
    def _generate_answer(query: str, results: List[SearchResultItem]) -> Optional[str]:
        context = "\n\n".join(
            f"[Documento {item.document_id}, pagina {item.page_number}]\n{item.text_excerpt or ''}"
            for item in results
        )
        try:
            return bedrock_client.invoke_model(
                f"Responda em portugues somente com base nos trechos abaixo. "
                f"Se nao houver evidencia, diga isso claramente.\n\nPergunta: {query}\n\nTrechos:\n{context}",
                max_tokens=1024,
                temperature=0.0,
            ) or None
        except Exception as exc:
            logger.warning(f"Falha ao gerar resposta de busca: {exc}")
            return None


search_service = SearchService()


_LEXICAL_STOPWORDS = {"a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "sobre", "que", "qual", "quais", "como", "onde", "quando", "quanto", "ao", "aos", "se", "ou"}


def _tokenize_lexical(text: str) -> List[str]:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).lower()
    normalized = re.sub(r"[^\w\s./-]", " ", normalized)
    return list(dict.fromkeys(
        token.strip("-/.") for token in normalized.split()
        if len(token.strip("-/.") ) >= 2 and token.strip("-/.") not in _LEXICAL_STOPWORDS
    ))


def _lexical_score(terms: List[str], text: str) -> float:
    normalized = " ".join(_tokenize_lexical(text))
    return sum(5.0 if any(char.isdigit() for char in term) else 1.0 for term in terms if term in normalized)
