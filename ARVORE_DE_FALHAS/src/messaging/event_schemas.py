"""
Schemas dos eventos publicados no RabbitMQ.

Todos herdam de BaseEvent (metadados comuns) e expõem um método `routing_key()`
que determina para qual routing key do exchange topic o evento deve ir.

Todos os eventos incluem org_id + tenant_id obrigatórios — o consumidor usa para
rotear para o banco/partição correto (multi-tenancy).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """Tipos de eventos publicados/consumidos pelo sistema."""

    DOCUMENT_PROCESSED = "document.processed"
    DOCUMENT_FAILED = "document.failed"
    INGEST_REQUESTED = "ingest.requested"


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class BaseEvent(BaseModel):
    """Campos comuns a todos os eventos."""

    event_id: str = Field(..., description="UUID do evento — único por publicação")
    event_type: EventType
    occurred_at: str = Field(default_factory=_utc_now_iso)
    org_id: str = Field(..., description="Organização dona do dado")
    tenant_id: Optional[str] = Field(
        default=None, description="Tenant dono do dado; nulo para escopo organizacional"
    )
    version: int = Field(default=1, description="Versão do schema")

    def routing_key(self) -> str:
        return self.event_type.value

    def to_json(self) -> str:
        return self.model_dump_json()


class DocumentProcessedEvent(BaseEvent):
    """Pipeline de ingestão finalizou.

    Schema v3: inclui vector_index_status.
    Consumidores devem checar vector_index_status para distinguir indexação completa
    (success) de parcial (partial/failed/empty) — o evento é emitido em ambos os casos.
    """

    model_config = ConfigDict(extra="forbid")

    event_type: EventType = EventType.DOCUMENT_PROCESSED
    version: int = Field(default=3, description="Versão do schema")

    bronze_id: str
    envelope_id: Optional[str] = Field(
        default=None, description="ID do envelope estruturado (Route A)"
    )

    file_name: str
    file_type: str
    document_type: Optional[str] = None
    is_structured: Optional[bool] = None
    object_path: Optional[str] = None
    vector_index_status: Optional[str] = Field(
        default=None,
        description="success | partial | failed | empty | not_run — "
        "use este campo para distinguir indexação completa de parcial.",
    )


class DocumentFailedEvent(BaseEvent):
    """Falha em alguma etapa do pipeline."""

    event_type: EventType = EventType.DOCUMENT_FAILED

    stage: str = Field(..., description="bronze | silver")
    bronze_id: Optional[str] = None
    file_name: Optional[str] = None
    errors: List[str] = Field(default_factory=list)


class IngestRequestedEvent(BaseEvent):
    """
    Requisição de ingestão vinda pela fila (ATI → arvore-de-falhas).

    O consumer pega esse evento e chama o pipeline internamente, como se fosse
    uma chamada ao endpoint /ingest/file.
    """

    event_type: EventType = EventType.INGEST_REQUESTED

    source_url: Optional[str] = None
    content_base64: Optional[str] = None
    file_name: str
    full_pipeline: bool = True
    callback_correlation_id: Optional[str] = None
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    generate_fault_tree: bool = False
