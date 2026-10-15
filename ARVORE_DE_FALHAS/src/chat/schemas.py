from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    equipment_id: str
    message: str = ""
    context_ids: List[str] = Field(default_factory=list)  # optional doc scoping for RAG
    uploaded_images: List["IncomingChatImage"] = Field(default_factory=list)


class IncomingChatImage(BaseModel):
    filename: str
    content_type: str
    data_base64: str


class ChatSource(BaseModel):
    filename: str
    doc_id: str
    chunk_id: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    score: float
    pdf_url: Optional[str] = None


class ChatImage(BaseModel):
    id: str
    url: str
    filename: str
    page_number: Optional[int] = None
    description: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    category: Optional[str] = None
    document_id: Optional[str] = None
    pdf_url: Optional[str] = None


class ChatDocument(BaseModel):
    id: str
    filename: str
    content_type: Optional[str] = None
    download_url: str


class ChatResponse(BaseModel):
    answer: str
    used_rag: bool
    found_in_doc: bool
    sources: List[ChatSource] = Field(default_factory=list)
    images: List[ChatImage] = Field(default_factory=list)
    documents: List[ChatDocument] = Field(default_factory=list)


class ChatHistoryItem(BaseModel):
    role: str
    content: str
    ts: int
