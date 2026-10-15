from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── AI-generated failure mode schemas ────────────────────────────────────────

class FailureModeAlarmEvidenceItem(BaseModel):
    alarm_code: str = ""
    description: str = ""
    source_pages: List[int] = Field(default_factory=list)


class FailureModeCauseGroupItem(BaseModel):
    group_name: str = ""
    causes: List[str] = Field(default_factory=list)
    effects: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)


class FailureModeItem(BaseModel):
    mode_id: Optional[str] = None
    root_id: Optional[str] = None
    title: str = ""
    description: str = ""
    category: str = "Geral"
    severity: str = "media"
    tags: List[str] = Field(default_factory=list)
    source_pages: List[int] = Field(default_factory=list)
    alarm_codes: List[str] = Field(default_factory=list)
    alarm_evidence: List[FailureModeAlarmEvidenceItem] = Field(default_factory=list)
    causes: List[str] = Field(default_factory=list)
    effects: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    cause_groups: List[FailureModeCauseGroupItem] = Field(default_factory=list)
    probable_cause: str = ""
    symptoms: str = ""
    impact: str = ""
    recommendation: str = ""
    source_type: str = "auto"
    source_doc_id: Optional[str] = None
    source_doc_filename: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


class FailureModeRoot(BaseModel):
    doc_id: str
    filename: str
    items: List[FailureModeItem] = Field(default_factory=list)


class GenerateFaultTreeRequest(BaseModel):
    """Requisição de geração da árvore de falhas.

    Contrato simplificado: apenas document_id é necessário. O backend resolve
    bronze_id, envelope_id e filename a partir dos bindings do equipamento.

    Os campos doc_id, filename, bronze_id e envelope_id são mantidos por
    compatibilidade com clientes existentes, mas não são obrigatórios quando
    document_id é fornecido.
    """
    document_id: Optional[str] = None
    # Campos legados — aceitos mas não obrigatórios
    doc_id: Optional[str] = None
    filename: Optional[str] = None
    bronze_id: Optional[str] = None
    envelope_id: Optional[str] = None
    model_id: Optional[str] = None


class GenerationStatusResponse(BaseModel):
    id: str
    org_id: str
    model_id: Optional[str] = None
    tenant_id: Optional[str] = None
    equipment_id: Optional[str] = None
    generation_status: Optional[str] = None
    generation_error: Optional[str] = None
    generation_updated_at: Any = None
    generation_id: Optional[str] = None
    active_failure_modes: Optional[Any] = None


class FaultTreeVersionResponse(BaseModel):
    id: str
    tree_id: str
    org_id: str
    tenant_id: Optional[str] = None
    model_id: Optional[str] = None
    equipment_id: Optional[str] = None
    document_id: Optional[str] = None
    envelope_id: Optional[str] = None
    file_name: Optional[str] = None
    status: str
    failure_modes: Optional[Any] = None
    error: Optional[str] = None
    created_at: Any = None
    updated_at: Any = None
    activated_at: Any = None


class DocumentBindingResponse(BaseModel):
    """Documento associado ao alvo (equipamento ou modelo).

    Campos user-friendly: relationship, scope e status descrevem a origem e
    estado do documento sem expor IDs internos de armazenamento.
    """
    document_id: str
    filename: Optional[str] = Field(None, alias="file_name")
    relationship: str = "source"           # "source" ou "inherited"
    scope: Optional[str] = None            # "tenant" ou "organization"
    status: Optional[str] = None           # "ready", "processing", "failed", "unknown"
    # Campos complementares
    id: str
    envelope_id: Optional[str] = None
    org_id: str
    tenant_id: Optional[str] = None
    target_type: str
    target_id: str
    model_id: Optional[str] = None
    object_path: Optional[str] = None
    upload_date: Any = None
    created_at: Any = None

    model_config = {"populate_by_name": True}

    @classmethod
    def from_binding_row(cls, row: dict) -> "DocumentBindingResponse":
        """Constrói a resposta a partir de uma linha do list_document_bindings."""
        raw_status = row.get("document_status") or ""
        if raw_status in {"processed", "completed", "ingested"}:
            status = "ready"
        elif raw_status in {"pending", "running", "partial"}:
            status = "processing"
        elif raw_status == "failed":
            status = "failed"
        else:
            status = "unknown"

        storage_tenant = row.get("document_tenant_id")
        scope = "organization" if storage_tenant is None else "tenant"

        return cls(
            document_id=row.get("document_id", ""),
            file_name=row.get("file_name"),
            relationship=row.get("relationship") or "source",
            scope=scope,
            status=status,
            id=row.get("id", ""),
            envelope_id=row.get("envelope_id"),
            org_id=row.get("org_id", ""),
            tenant_id=row.get("tenant_id"),
            target_type=row.get("target_type", ""),
            target_id=row.get("target_id", ""),
            model_id=row.get("model_id"),
            object_path=row.get("object_path"),
            upload_date=row.get("upload_date"),
            created_at=row.get("created_at"),
        )


# ── Manual fault tree schemas ─────────────────────────────────────────────────

class FaultTreeNode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    titulo: str
    causas: List[str] = []
    acoes: List[str] = []
    filhos: List["FaultTreeNode"] = []


FaultTreeNode.model_rebuild()


class CreateModelFaultTreeRequest(BaseModel):
    model_id: str
    tree_data: FaultTreeNode


class UpdateFaultTreeRequest(BaseModel):
    tree_data: FaultTreeNode


class CopyFaultTreeRequest(BaseModel):
    equipment_id: str
    tenant_id: str
    org_id: str


class FaultTreeResponse(BaseModel):
    id: str
    org_id: str
    model_id: Optional[str] = None
    tenant_id: Optional[str] = None
    equipment_id: Optional[str] = None
    tree_data: Dict[str, Any]
    failure_modes: Optional[Any] = None
    generation_status: Optional[str] = None
    generation_error: Optional[str] = None
    generation_updated_at: Any = None
    created_at: Any
    updated_at: Any
