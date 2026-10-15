"""
Schemas de busca semantica (RAG) — request e response.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., description="Consulta semantica do usuario")
    limit: int = Field(default=5, ge=1, le=50)
    document_type: Optional[str] = Field(
        default=None, description="Filtro opcional por tipo de documento"
    )
    content_type: Optional[str] = Field(
        default="text_chunk",
        description="Tipo de conteudo a buscar (text_chunk | image)",
    )
    context_ids: List[str] = Field(default_factory=list)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank: bool = False
    generate_answer: bool = False


class SearchResultItem(BaseModel):
    id: str
    score: float
    document_id: Optional[str] = None
    document_type: Optional[str] = None
    content_type: Optional[str] = None
    page_number: Optional[int] = None
    chunk_index: Optional[int] = None
    text_excerpt: Optional[str] = None
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    section_path: Optional[str] = None
    rerank_score: Optional[float] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem] = Field(default_factory=list)
    total: int = 0
    answer: Optional[str] = None
    citations: List["Citation"] = Field(default_factory=list)
    metadata: Optional["SearchMetadata"] = None


class Citation(BaseModel):
    document_id: Optional[str] = None
    page_number: Optional[int] = None
    chunk_index: Optional[int] = None
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    section_path: Optional[str] = None
    confidence_score: float = 0.0
    rerank_score: Optional[float] = None


class SearchMetadata(BaseModel):
    chunks_found: int = 0
    chunks_used: int = 0
    processing_time_ms: int = 0
