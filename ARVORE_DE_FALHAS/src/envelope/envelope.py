"""
Schema do envelope de documento — Enum de tipos e modelo de dados.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class DocumentType(str, Enum):
    MANUAL_TECNICO = "manual_tecnico"
    UNKNOWN = "unknown"


class BaseDocumentData(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnvelopeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope_id: str
    org_id: str
    tenant_id: Optional[str]
    bronze_id: str
    document_type: DocumentType
    document_hash: str
    file_name: str
    is_structured: bool = False
    confidence_score: float = 0.0
    extracted_data: Optional[Dict[str, Any]] = None
    raw_text: str = ""
    processed_at: datetime = None

    def __init__(self, **data: Any) -> None:
        if data.get("processed_at") is None:
            data["processed_at"] = datetime.now(timezone.utc)
        super().__init__(**data)

    def to_db_payload(self) -> Dict[str, Any]:
        extracted = self.extracted_data
        if extracted is not None and isinstance(extracted, dict):
            extracted = json.dumps(extracted, ensure_ascii=False)
        raw = self.raw_text or ""
        raw = raw.replace("\x00", "")
        return {
            "id": self.envelope_id,
            "org_id": self.org_id,
            "tenant_id": self.tenant_id,
            "bronze_id": self.bronze_id,
            "document_type": self.document_type.value,
            "document_hash": self.document_hash,
            "file_name": self.file_name,
            "is_structured": self.is_structured,
            "confidence_score": self.confidence_score,
            "extracted_data": extracted,
            "raw_text": raw,
            "processed_at": self.processed_at,
            "status": "processed",
        }
