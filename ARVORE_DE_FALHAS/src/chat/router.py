"""
Endpoints de chat para arvore-de-falhas.

POST /chat          — envia mensagem e recebe resposta RAG
GET  /chat/history  — retorna histórico de mensagens
POST /chat/clear    — limpa histórico e resumo
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from src.auth.jwt_validator import TenantContext, require_permission
from src.chat.pipeline import run_chat_pipeline
from src.chat.schemas import ChatHistoryItem, ChatRequest, ChatResponse
from src.chat.store import chat_store, summary_store
from src.storage import postgres_client, s3_client
from src.storage.s3_client import resolve_bucket_and_key

router = APIRouter(prefix="/chat", tags=["chat"])


def _tenant_id(ctx: TenantContext) -> str:
    if not ctx.tenant_id:
        raise HTTPException(
            status_code=422,
            detail="tenant_id real é obrigatório para operações de equipamento.",
        )
    return ctx.tenant_id


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
) -> ChatResponse:
    """Processa uma mensagem de chat para um equipamento."""
    return run_chat_pipeline(
        equipment_id=req.equipment_id,
        org_id=tenant.org_id,
        tenant_id=_tenant_id(tenant),
        message=req.message,
        context_ids=req.context_ids or None,
        uploaded_images=req.uploaded_images,
        chat_store=chat_store,
        summary_store=summary_store,
    )


@router.get("/history", response_model=List[ChatHistoryItem])
def chat_history(
    equipment_id: str,
    limit: int = 100,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
) -> List[ChatHistoryItem]:
    """Retorna o histórico de chat para um equipamento."""
    rows = chat_store.read_all(
        equipment_id, tenant.org_id, _tenant_id(tenant), limit=limit
    )
    return [ChatHistoryItem(role=r["role"], content=r["content"], ts=r.get("ts", 0)) for r in rows]


@router.post("/clear")
def chat_clear(
    equipment_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
) -> dict:
    """Limpa histórico e resumo de chat para um equipamento."""
    tenant_id = _tenant_id(tenant)
    chat_store.delete(equipment_id, tenant.org_id, tenant_id)
    summary_store.delete(equipment_id, tenant.org_id, tenant_id)
    return {"equipment_id": equipment_id, "cleared": True}


@router.get("/documents/{document_id}/download")
def download_chat_document(
    document_id: str,
    equipment_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    """Redireciona para URL S3 temporária após validar o vínculo ao equipamento."""
    bindings = postgres_client.list_document_bindings(
        org_id=tenant.org_id,
        tenant_id=_tenant_id(tenant),
        target_type="equipment",
        target_id=equipment_id,
    )
    document = next((row for row in bindings if row.get("document_id") == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado para este equipamento.")
    bronze = postgres_client.get_bronze_document_any_tenant(document_id, tenant.org_id)
    object_path = str(document.get("object_path") or (bronze or {}).get("object_path") or "")
    bucket, object_name = resolve_bucket_and_key(object_path)
    if not object_name:
        raise HTTPException(status_code=404, detail="Arquivo do documento não encontrado.")
    url = s3_client.generate_download_url(bucket, object_name)
    if not url:
        raise HTTPException(status_code=503, detail="Não foi possível preparar o download.")
    return RedirectResponse(url=url, status_code=307)
