"""
Endpoints de árvore de falhas.

- Rotas /fault-trees/model/... usam autenticação Cognito (catalog:read / catalog:write).
- Rota POST /fault-trees/model/{model_id}/copy usa chave interna (X-Internal-Key),
  chamada pelo ATI backend ao criar um equipamento.
"""
from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from src.auth.jwt_validator import (
    TenantContext,
    require_permission,
    get_current_tenant_optional,
    _resolve_role_data,
    _check_permission,
)
from src.config import settings
from src.fault_tree.schemas import (
    CopyFaultTreeRequest,
    CreateModelFaultTreeRequest,
    DocumentBindingResponse,
    FailureModeItem,
    FaultTreeResponse,
    FaultTreeVersionResponse,
    GenerateFaultTreeRequest,
    GenerationStatusResponse,
    UpdateFaultTreeRequest,
)
from src.fault_tree import service as fault_tree_service
from src.fault_tree.service import (
    EmptyGenerationError,
    GenerationDocument,
    resolve_generation_documents,
)
from src.storage.s3_client import s3_client
from src.storage.postgres_client import postgres_client as db

router = APIRouter(prefix="/fault-trees", tags=["fault-trees"])


# ── Auth interna ──────────────────────────────────────────────────────────────

async def require_internal_key(x_internal_key: str = Header(...)) -> None:
    if not settings.INTERNAL_API_KEY or x_internal_key != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Chave interna inválida.")


def _assert_permission(tenant: TenantContext, module: str, action: str) -> None:
    """Levanta 403 se o tenant não possuir a permissão module:action."""
    role_data = _resolve_role_data(tenant.org_id, tenant.tenant_id, tenant.organization_role)
    if not _check_permission(role_data, module, action):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Acesso negado: permissão '{module}:{action}' não disponível "
                f"para a role '{tenant.organization_role}'."
            ),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

_GENERATION_ERROR_MAP = {
    "bedrock": ("GENERATION_SERVICE_UNAVAILABLE", "Não foi possível concluir a geração neste momento."),
    "invoke": ("GENERATION_SERVICE_UNAVAILABLE", "Não foi possível concluir a geração neste momento."),
    "qdrant": ("RETRIEVAL_ERROR", "Falha ao acessar o conteúdo do manual. Tente novamente."),
    "empty": ("NO_FAILURE_MODES_FOUND", "Não foram encontrados modos de falha suficientes no documento."),
    "insufficient": ("INSUFFICIENT_DOCUMENT_CONTENT", "O documento não contém informações técnicas suficientes para gerar a árvore."),
    "not_ready": ("DOCUMENT_NOT_READY", "O documento ainda não terminou o processamento."),
}


def _map_generation_error(exc: Exception) -> tuple[str, str]:
    """Retorna (error_code, user_message) para uma exceção de geração."""
    msg = str(exc).lower()
    for keyword, (code, human_msg) in _GENERATION_ERROR_MAP.items():
        if keyword in msg:
            return code, human_msg
    return "GENERATION_FAILED", "A geração da árvore de falhas falhou. Tente novamente."


def _generation_error_response(exc: Exception) -> JSONResponse:
    code, message = _map_generation_error(exc)
    return JSONResponse(
        status_code=502,
        content={"status": "failed", "code": code, "message": message},
    )


def _serialize_tree(row: dict) -> FaultTreeResponse:
    for field in ("created_at", "updated_at"):
        if row.get(field) is not None and hasattr(row[field], "isoformat"):
            row[field] = row[field].isoformat()
    return FaultTreeResponse(**row)


def _equipment_tenant(tenant: TenantContext) -> str:
    if not tenant.tenant_id:
        raise HTTPException(
            status_code=422,
            detail="tenant_id real é obrigatório para árvores de equipamento.",
        )
    return tenant.tenant_id


def _validate_equipment_id(equipment_id: str) -> str:
    try:
        return str(uuid.UUID(str(equipment_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=422, detail="equipment_id deve ser um UUID válido."
        ) from exc


# ── Model template endpoints (Cognito) ───────────────────────────────────────

@router.post("/model", status_code=201, response_model=FaultTreeResponse)
def create_model_fault_tree(
    body: CreateModelFaultTreeRequest,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    existing = db.get_fault_tree_by_model(body.model_id, tenant.org_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe uma árvore de falhas para o modelo '{body.model_id}'.",
        )

    tree_id = str(uuid.uuid4())
    ok = db.create_fault_tree({
        "id": tree_id,
        "org_id": tenant.org_id,
        "model_id": body.model_id,
        "tenant_id": None,
        "equipment_id": None,
        "tree_data": body.tree_data.model_dump(),
    })
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao salvar árvore de falhas.")

    row = db.get_fault_tree_by_model(body.model_id, tenant.org_id)
    return _serialize_tree(row)


@router.get("/model/{model_id}", response_model=FaultTreeResponse)
def get_model_fault_tree(
    model_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    row = db.get_fault_tree_by_model(model_id, tenant.org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")
    return _serialize_tree(row)


@router.put("/model/{model_id}", response_model=FaultTreeResponse)
def update_model_fault_tree(
    model_id: str,
    body: UpdateFaultTreeRequest,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    row = db.get_fault_tree_by_model(model_id, tenant.org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")

    ok = db.update_fault_tree(row["id"], tenant.org_id, body.tree_data.model_dump())
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao atualizar árvore de falhas.")

    updated = db.get_fault_tree_by_model(model_id, tenant.org_id)
    return _serialize_tree(updated)


def _copy_model_snapshot(
    *, model_id: str, equipment_id: str, org_id: str, tenant_id: str
) -> FaultTreeResponse:
    template = db.get_fault_tree_by_model(model_id, org_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhuma árvore de falhas definida para o modelo '{model_id}'.",
        )

    existing = db.get_equipment_fault_tree(
        org_id=org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe uma árvore de falhas para o equipamento '{equipment_id}'.",
        )

    tree_id = str(uuid.uuid4())
    tree_snapshot = copy.deepcopy(template["tree_data"])
    failure_modes_snapshot = copy.deepcopy(template.get("failure_modes"))

    ok = db.create_fault_tree({
        "id": tree_id,
        "org_id": org_id,
        "model_id": model_id,
        "tenant_id": tenant_id,
        "equipment_id": equipment_id,
        "tree_data": tree_snapshot,
        "failure_modes": failure_modes_snapshot,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao copiar árvore de falhas.")

    row = db.get_equipment_fault_tree(
        org_id=org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    copied_bindings = db.copy_model_document_bindings(
        model_id=model_id,
        equipment_id=equipment_id,
        org_id=org_id,
        tenant_id=tenant_id,
    )
    if row and row.get("failure_modes") is not None:
        version = db.create_fault_tree_version({
            "tree_id": row["id"],
            "org_id": org_id,
            "tenant_id": tenant_id,
            "model_id": model_id,
            "equipment_id": equipment_id,
            "status": "active",
            "failure_modes": row.get("failure_modes"),
            "file_name": "Árvore herdada do modelo",
        })
        if version:
            for binding in copied_bindings:
                db.link_version_document(
                    version_id=version["id"],
                    document_id=binding["document_id"],
                    envelope_id=binding.get("envelope_id"),
                    relationship="inherited",
                    origin_type="model",
                    origin_id=model_id,
                )
    return _serialize_tree(row)


# ── Copy endpoints ────────────────────────────────────────────────────────────

@router.post("/model/{model_id}/copy", status_code=201, response_model=FaultTreeResponse)
def copy_model_fault_tree(
    model_id: str,
    body: CopyFaultTreeRequest,
    _: None = Depends(require_internal_key),
):
    return _copy_model_snapshot(
        model_id=model_id,
        equipment_id=_validate_equipment_id(body.equipment_id),
        org_id=body.org_id,
        tenant_id=body.tenant_id,
    )


@router.post(
    "/equipment/{equipment_id}/inherit/{model_id}",
    status_code=201,
    response_model=FaultTreeResponse,
)
def inherit_model_fault_tree(
    equipment_id: str,
    model_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    """Permite ao frontend autenticado criar o snapshot herdado no próprio tenant."""
    return _copy_model_snapshot(
        model_id=model_id,
        equipment_id=_validate_equipment_id(equipment_id),
        org_id=tenant.org_id,
        tenant_id=_equipment_tenant(tenant),
    )


# ── Equipment tree endpoints (Cognito) ───────────────────────────────────────

@router.get("/equipment/{equipment_id}", response_model=FaultTreeResponse)
def get_equipment_fault_tree_endpoint(
    equipment_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    row = db.get_equipment_fault_tree(
        org_id=tenant.org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")
    return _serialize_tree(row)


@router.put("/equipment/{equipment_id}", response_model=FaultTreeResponse)
def update_equipment_fault_tree(
    equipment_id: str,
    body: UpdateFaultTreeRequest,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    row = db.get_equipment_fault_tree(
        org_id=tenant.org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")

    ok = db.update_fault_tree(row["id"], tenant.org_id, body.tree_data.model_dump())
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao atualizar árvore de falhas.")

    updated = db.get_equipment_fault_tree(
        org_id=tenant.org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    return _serialize_tree(updated)


@router.delete("/equipment/{equipment_id}", status_code=204)
def delete_equipment_fault_tree(
    equipment_id: str,
    org_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    x_internal_key: Optional[str] = Header(default=None),
    tenant: Optional[TenantContext] = Depends(get_current_tenant_optional),
):
    equipment_id = _validate_equipment_id(equipment_id)
    if x_internal_key:
        if not settings.INTERNAL_API_KEY or x_internal_key != settings.INTERNAL_API_KEY:
            raise HTTPException(status_code=401, detail="Chave interna inválida.")
        if not org_id:
            raise HTTPException(status_code=400, detail="org_id obrigatório ao usar chave interna.")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id obrigatório ao usar chave interna.")
        resolved_org = org_id
        resolved_tenant = tenant_id
    elif tenant:
        _assert_permission(tenant, "catalog", "write")
        resolved_org = tenant.org_id
        resolved_tenant = _equipment_tenant(tenant)
    else:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")

    row = db.get_equipment_fault_tree(
        org_id=resolved_org, tenant_id=resolved_tenant, equipment_id=equipment_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")

    ok = db.delete_fault_tree(row["id"], resolved_org)
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao deletar árvore de falhas.")

# ── Generation helpers ────────────────────────────────────────────────────────

def _run_generation(
    tree: dict[str, Any],
    version: dict[str, Any],
    gen_doc: GenerationDocument,
) -> None:
    try:
        fault_tree_service.run_generation_candidate(
            tree=tree,
            version=version,
            document_id=gen_doc.bronze_id,
            file_name=gen_doc.filename,
            envelope_id=gen_doc.envelope_id,
            storage_tenant_id=gen_doc.storage_tenant_id,
            storage_scope=gen_doc.scope_type,
        )
    except EmptyGenerationError:
        pass  # estado "empty" já gravado pelo service
    except Exception as exc:
        error_code, _ = _map_generation_error(exc)
        # Persiste o código amigável junto com o erro bruto já salvo pelo service.
        db.update_fault_tree_version_status(
            version["id"], tree["org_id"], "failed",
            f"[{error_code}] {exc}",
        )


def _resolve_gen_doc(
    *,
    tree: dict[str, Any],
    body: GenerateFaultTreeRequest,
    target_type: str,
    target_id: str,
    tenant_id: Optional[str],
) -> GenerationDocument:
    """Resolve o GenerationDocument a usar na geração.

    Se body.document_id for fornecido, usa o resolver centralizado.
    Para clientes legados que ainda passam doc_id/bronze_id/filename, cria
    um GenerationDocument diretamente — preservando compatibilidade.
    """
    org_id = tree["org_id"]

    # Caminho novo: document_id resolve tudo internamente.
    if body.document_id:
        docs = resolve_generation_documents(
            org_id=org_id,
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            document_id=body.document_id,
        )
        if not docs:
            raise HTTPException(
                status_code=422,
                detail="Documento não encontrado ou não está vinculado ao alvo neste escopo.",
            )
        return docs[0]

    # Caminho legado: bronze_id / doc_id fornecidos pelo cliente.
    legacy_doc_id = body.bronze_id or body.doc_id
    if not legacy_doc_id:
        # Sem document_id nem doc_id legado: resolve todos os bindings disponíveis.
        docs = resolve_generation_documents(
            org_id=org_id,
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
        )
        if not docs:
            raise HTTPException(
                status_code=422,
                detail="Nenhum documento está vinculado ao alvo neste escopo.",
            )
        if len(docs) > 1:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "DOCUMENT_SELECTION_REQUIRED",
                    "message": (
                        "Múltiplos documentos vinculados ao alvo. "
                        "Informe document_id para selecionar qual usar."
                    ),
                    "available_documents": [
                        {
                            "document_id": d.document_id,
                            "filename": d.filename,
                            "relationship": d.relationship,
                            "scope": d.scope_type,
                        }
                        for d in docs
                    ],
                },
            )
        return docs[0]

    # Legado com doc_id explícito: valida binding e enriquece com envelope.
    binding = db.get_document_binding(
        document_id=legacy_doc_id,
        org_id=org_id,
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id,
    )
    if binding is None:
        raise HTTPException(
            status_code=422,
            detail="Manual não está vinculado ao alvo informado neste escopo.",
        )
    # Tenta resolver envelope_id se não fornecido pelo cliente.
    resolved_envelope = body.envelope_id or binding.get("envelope_id")
    resolved_filename = body.filename or binding.get("file_name") or ""
    storage_tenant = binding.get("document_tenant_id")

    if not resolved_envelope:
        envelope = db.get_envelope_by_bronze_any_tenant(legacy_doc_id, org_id)
        if envelope:
            resolved_envelope = envelope.get("id")
            if not resolved_filename:
                resolved_filename = envelope.get("file_name") or ""

    if not resolved_filename:
        bronze = db.get_bronze_document_any_tenant(legacy_doc_id, org_id)
        if bronze:
            resolved_filename = bronze.get("file_name") or ""

    return GenerationDocument(
        document_id=legacy_doc_id,
        bronze_id=legacy_doc_id,
        envelope_id=resolved_envelope,
        filename=resolved_filename,
        relationship=binding.get("relationship") or "source",
        scope_type="organization" if storage_tenant is None else "tenant",
        storage_tenant_id=storage_tenant,
    )


def _start_generation(
    *,
    tree: dict[str, Any],
    body: GenerateFaultTreeRequest,
    target_type: str,
    target_id: str,
    tenant_id: Optional[str],
    background_tasks: BackgroundTasks,
) -> GenerationStatusResponse:
    if tree.get("generation_status") in ("queued", "running"):
        raise HTTPException(status_code=409, detail="Geração já em andamento.")

    gen_doc = _resolve_gen_doc(
        tree=tree,
        body=body,
        target_type=target_type,
        target_id=target_id,
        tenant_id=tenant_id,
    )

    version = fault_tree_service.create_generation_candidate(
        tree=tree,
        document_id=gen_doc.bronze_id,
        envelope_id=gen_doc.envelope_id,
        file_name=gen_doc.filename,
        origin_type=target_type,
        origin_id=target_id,
    )
    background_tasks.add_task(_run_generation, tree, version, gen_doc)
    updated = (
        db.get_fault_tree_by_model(target_id, tree["org_id"])
        if target_type == "model"
        else db.get_equipment_fault_tree(
            org_id=tree["org_id"], tenant_id=tenant_id, equipment_id=target_id
        )
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Falha ao consultar a geração criada.")
    payload = {
        k: updated[k]
        for k in GenerationStatusResponse.model_fields
        if k in updated
    }
    payload["generation_id"] = version["id"]
    payload["active_failure_modes"] = updated.get("failure_modes")
    return GenerationStatusResponse(**payload)


def _parse_failure_modes(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = row.get("failure_modes")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return []


# ── Model generate / failure-modes endpoints ─────────────────────────────────

@router.post("/model/{model_id}/generate", response_model=GenerationStatusResponse, status_code=202)
def generate_model_fault_tree(
    model_id: str,
    body: GenerateFaultTreeRequest,
    background_tasks: BackgroundTasks,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    row = db.get_fault_tree_by_model(model_id, tenant.org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")
    return _start_generation(
        tree=row,
        body=body,
        target_type="model",
        target_id=model_id,
        tenant_id=None,
        background_tasks=background_tasks,
    )


@router.get("/model/{model_id}/failure-modes", response_model=List[FailureModeItem])
def get_model_failure_modes(
    model_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    row = db.get_fault_tree_by_model(model_id, tenant.org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")
    return _parse_failure_modes(row)


# ── Equipment generate / failure-modes endpoints ─────────────────────────────

@router.post("/equipment/{equipment_id}/generate", response_model=GenerationStatusResponse, status_code=202)
def generate_equipment_fault_tree(
    equipment_id: str,
    body: GenerateFaultTreeRequest,
    background_tasks: BackgroundTasks,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    """Inicia geração da árvore de falhas para um equipamento.

    Contrato simplificado: document_id é opcional — se omitido, o backend
    resolve o documento elegível a partir dos bindings do equipamento.
    Bronze_id, envelope_id e filename são resolvidos internamente.
    """
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    row = db.get_equipment_fault_tree(
        org_id=tenant.org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    if row is None:
        # Árvore não existe ainda: verifica se há algum documento vinculado antes de criar.
        docs = resolve_generation_documents(
            org_id=tenant.org_id,
            tenant_id=tenant_id,
            target_type="equipment",
            target_id=equipment_id,
            document_id=body.document_id or body.bronze_id or body.doc_id or None,
        )
        if not docs:
            raise HTTPException(
                status_code=422,
                detail="Nenhum documento está vinculado ao equipamento neste escopo.",
            )
        try:
            row = fault_tree_service.ensure_target_tree(
                org_id=tenant.org_id,
                tenant_id=tenant_id,
                target_type="equipment",
                target_id=equipment_id,
                model_id=body.model_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif body.model_id and body.model_id != row.get("model_id"):
        raise HTTPException(
            status_code=409,
            detail="model_id não corresponde ao equipamento já registrado.",
        )
    return _start_generation(
        tree=row,
        body=body,
        target_type="equipment",
        target_id=equipment_id,
        tenant_id=tenant_id,
        background_tasks=background_tasks,
    )


@router.get("/equipment/{equipment_id}/failure-modes", response_model=List[FailureModeItem])
def get_equipment_failure_modes(
    equipment_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    row = db.get_equipment_fault_tree(
        org_id=tenant.org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")
    return _parse_failure_modes(row)


@router.get(
    "/model/{model_id}/documents",
    response_model=List[DocumentBindingResponse],
)
def list_model_documents(
    model_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    rows = db.list_document_bindings(
        org_id=tenant.org_id,
        tenant_id=None,
        target_type="model",
        target_id=model_id,
    )
    return [DocumentBindingResponse.from_binding_row(r) for r in rows]


@router.get(
    "/equipment/{equipment_id}/documents",
    response_model=List[DocumentBindingResponse],
)
def list_equipment_documents(
    equipment_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    rows = db.list_document_bindings(
        org_id=tenant.org_id,
        tenant_id=tenant_id,
        target_type="equipment",
        target_id=equipment_id,
    )
    return [DocumentBindingResponse.from_binding_row(r) for r in rows]


@router.get(
    "/equipment/{equipment_id}/versions",
    response_model=List[FaultTreeVersionResponse],
)
def list_equipment_versions(
    equipment_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    row = db.get_equipment_fault_tree(
        org_id=tenant.org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")
    return db.list_fault_tree_versions(row["id"], tenant.org_id)


@router.get(
    "/equipment/{equipment_id}/generations/{generation_id}",
    response_model=FaultTreeVersionResponse,
)
def get_equipment_generation(
    equipment_id: str,
    generation_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    tree = db.get_equipment_fault_tree(
        org_id=tenant.org_id, tenant_id=tenant_id, equipment_id=equipment_id
    )
    if tree is None:
        raise HTTPException(status_code=404, detail="Árvore de falhas não encontrada.")
    version = db.get_fault_tree_version(generation_id, tenant.org_id)
    if version is None or version.get("tree_id") != tree["id"]:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    return version


@router.get("/equipment/{equipment_id}/images")
def list_equipment_images(
    equipment_id: str,
    limit: int = 100,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    equipment_id = _validate_equipment_id(equipment_id)
    tenant_id = _equipment_tenant(tenant)
    bindings = db.list_document_bindings(
        org_id=tenant.org_id,
        tenant_id=tenant_id,
        target_type="equipment",
        target_id=equipment_id,
    )
    images: List[Dict[str, Any]] = []
    for binding in bindings:
        # Imagens herdadas mantêm o escopo original do documento; o vínculo do
        # equipamento é a autorização explícita para expô-las neste endpoint.
        document_tenant = binding.get("document_tenant_id")
        for item in db.get_document_images(
            org_id=tenant.org_id,
            tenant_id=document_tenant,
            bronze_id=binding["document_id"],
            limit=max(1, min(limit, 500)),
        ):
            data = dict(item)
            if data.get("object_path"):
                data["download_url"] = s3_client.generate_download_url(
                    settings.S3_BUCKET, data["object_path"]
                )
            images.append(data)
            if len(images) >= limit:
                return images
    return images
