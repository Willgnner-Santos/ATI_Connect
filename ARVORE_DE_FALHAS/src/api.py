"""
API FastAPI — endpoints de ingestão e busca semântica.

Autenticação: id_token do Cognito validado localmente via JWKS,
com escopo por organização e tenant.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.auth.jwt_validator import TenantContext, is_global_role, require_permission
from src.orchestrator import orchestrator
from src.envelope import envelope_repository
from src.storage.postgres_client import postgres_client as db
from src.search import search_service
from src.search.schemas import SearchRequest, SearchResponse
from src.layers.silver_layer import silver_layer
from src.fault_tree.router import router as fault_tree_router
from src.chat.router import router as chat_router
from src.config import settings

router = APIRouter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # R11: guarda contra string vazia ("") — Pydantic já rejeita ausência total.
    # verify_aud está hardcoded como True em jwt_validator; audience vazio desligaria
    # a verificação silenciosamente se chegasse aqui.
    if not settings.COGNITO_CLIENT_ID:
        raise RuntimeError(
            "COGNITO_CLIENT_ID não configurado. "
            "Configure o app client ID do Cognito para habilitar a validação de audience JWT. "
            "Sem essa configuração, tokens emitidos para outros app clients do mesmo pool "
            "seriam aceitos indevidamente."
        )

    try:
        db.create_tables()
        logger.info("Tabelas Postgres verificadas no startup")
        recovered = db.recover_stale_work(settings.STALE_JOB_MINUTES)
        if recovered["ingest_jobs"] or recovered["generations"]:
            logger.warning(f"Trabalhos interrompidos recuperados: {recovered}")
    except Exception as e:
        logger.warning(f"create_tables falhou no startup: {e}")
    yield


app = FastAPI(
    title="Arvore de Falhas ATI",
    description="API de ingestao e busca semantica de manuais tecnicos industriais",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_origins = [
    origin.strip()
    for origin in settings.CORS_ALLOWED_ORIGINS.split(",")
    if origin.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


def _run_pipeline_background(
    job_id: str,
    tmp_path: str,
    org_id: str,
    tenant_id: Optional[str],
    file_name: str,
    metadata: Dict[str, Any],
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    model_id: Optional[str] = None,
    generate_fault_tree: bool = False,
) -> None:
    """Executado em background: roda o pipeline e persiste o resultado no job."""
    db.update_ingest_job(job_id, status="running")
    try:
        result = orchestrator.run_full_pipeline(
            file_path=tmp_path,
            org_id=org_id,
            tenant_id=tenant_id,
            metadata=metadata,
        )
        logger.info(
            f"[fault-tree] job={job_id} target_type={target_type!r} "
            f"target_id={target_id!r} generate_fault_tree={generate_fault_tree!r} "
            f"pipeline_status={result.get('status')!r}"
        )
        if (
            target_type
            and target_id
            and str(result.get("status") or "").lower() in {"completed", "partial"}
        ):
            logger.info(f"[fault-tree] disparando process_ingest_target para job={job_id}")
            try:
                from src.fault_tree.service import process_ingest_target

                result["fault_tree"] = process_ingest_target(
                    pipeline_result=result,
                    org_id=org_id,
                    tenant_id=tenant_id,
                    target_type=target_type,
                    target_id=target_id,
                    model_id=model_id,
                    file_name=file_name,
                    generate_fault_tree=generate_fault_tree,
                )
            except Exception as exc:
                logger.error(
                    f"Pós-processamento da árvore falhou (job={job_id}): {exc}"
                )
                result.setdefault("warnings", []).append(str(exc))
                result["fault_tree"] = {
                    "target_type": target_type,
                    "target_id": target_id,
                    "generation_status": "failed",
                    "error": str(exc),
                }
                result["status"] = "partial"
        status = str(result.get("status") or "").strip().lower()
        if status in {"completed", "ingested"}:
            job_status = "completed"
        elif status == "partial":
            job_status = "partial"
        else:
            job_status = "failed"
        db.update_ingest_job(
            job_id,
            status=job_status,
            result=result,
            error="; ".join(result.get("errors", [])) or None,
            bronze_id=result.get("bronze_id"),
            envelope_id=result.get("envelope_id"),
            document_type=result.get("document_type"),
        )
    except Exception as exc:
        logger.error(f"Background pipeline falhou (job={job_id}): {exc}")
        db.update_ingest_job(job_id, status="failed", error=str(exc))
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/ingest/file", status_code=202)
async def ingest_file(
    file: UploadFile = File(...),
    context_ids: Optional[str] = Form(default=None),
    target_type: Optional[str] = Form(default=None),
    target_id: Optional[str] = Form(default=None),
    model_id: Optional[str] = Form(default=None),
    generate_fault_tree: bool = Form(default=False),
    background_tasks: BackgroundTasks = None,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    """Ingere um arquivo de forma assíncrona.

    Retorna 202 Accepted imediatamente com um `job_id`.
    Use GET /ingest/status/{job_id} para consultar o resultado.
    """
    normalized_target_type = str(target_type or "").strip().lower() or None
    normalized_target_id = str(target_id or "").strip() or None
    normalized_model_id = str(model_id or "").strip() or None
    if bool(normalized_target_type) != bool(normalized_target_id):
        raise HTTPException(
            status_code=422,
            detail="target_type e target_id devem ser informados juntos.",
        )
    if normalized_target_type not in {None, "model", "equipment"}:
        raise HTTPException(
            status_code=422, detail="target_type deve ser 'model' ou 'equipment'."
        )

    effective_tenant_id = tenant.tenant_id
    if normalized_target_type == "model":
        if not is_global_role(tenant.organization_role):
            raise HTTPException(
                status_code=403,
                detail="Apenas roles globais podem ingerir documentos de modelo.",
            )
        effective_tenant_id = None
        normalized_model_id = normalized_target_id
    elif normalized_target_type == "equipment":
        try:
            normalized_target_id = str(uuid.UUID(normalized_target_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(
                status_code=422, detail="target_id deve ser um UUID de equipamento válido."
            ) from exc
        if not effective_tenant_id:
            raise HTTPException(
                status_code=422,
                detail="tenant_id real é obrigatório para documentos de equipamento.",
            )
    tmp_path = None
    try:
        ext = Path(file.filename or "upload").suffix.lower()
        if ext != ".pdf":
            raise HTTPException(status_code=422, detail="Apenas arquivos PDF são aceitos.")
        tmp_path = f"/tmp/upload_{uuid.uuid4()}{ext}"
        with open(tmp_path, "wb") as out:
            total_bytes = 0
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Arquivo excede o limite de {settings.MAX_UPLOAD_BYTES} bytes.",
                    )
                out.write(chunk)

        job_id = str(uuid.uuid4())
        file_name = file.filename or "upload"

        metadata: Dict[str, Any] = {"filename": file_name}
        if context_ids:
            metadata["context_ids"] = [
                c.strip() for c in context_ids.split(",") if c.strip()
            ]
        if normalized_target_id:
            existing_contexts = set(metadata.get("context_ids", []))
            existing_contexts.add(normalized_target_id)
            metadata["context_ids"] = sorted(existing_contexts)
            metadata["target_type"] = normalized_target_type
            metadata["target_id"] = normalized_target_id
            metadata["model_id"] = normalized_model_id

        if not db.create_ingest_job(
            job_id,
            org_id=tenant.org_id,
            tenant_id=effective_tenant_id,
            file_name=file_name,
        ):
            raise HTTPException(status_code=500, detail="Falha ao criar registro de job.")

        background_tasks.add_task(
            _run_pipeline_background,
            job_id,
            tmp_path,
            tenant.org_id,
            effective_tenant_id,
            file_name,
            metadata,
            normalized_target_type,
            normalized_target_id,
            normalized_model_id,
            generate_fault_tree,
        )

        logger.info(
            f"Job {job_id} enfileirado "
            f"(org={tenant.org_id}, tenant={effective_tenant_id}, file={file_name}, "
            f"target_type={normalized_target_type}, target_id={normalized_target_id}, "
            f"generate_fault_tree={generate_fault_tree})"
        )

        return JSONResponse(status_code=202, content={
            "job_id": job_id,
            "status": "pending",
            "file_name": file_name,
            "org_id": tenant.org_id,
            "tenant_id": effective_tenant_id,
            "target_type": normalized_target_type,
            "target_id": normalized_target_id,
        })
    except HTTPException:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
        raise
    except Exception as e:
        logger.error(f"Erro ao receber upload: {e}")
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=str(e))


def _user_friendly_ingest_status(job: dict) -> dict:
    """Mapeia o status interno do job para informação orientada ao usuário."""
    import json as _json

    raw_status = str(job.get("status") or "pending").lower()
    result_raw = job.get("result")
    result = None
    if result_raw:
        try:
            result = _json.loads(result_raw) if isinstance(result_raw, str) else result_raw
        except Exception:
            pass

    stage_map = {
        "pending": ("uploading", 5, "Aguardando início do processamento"),
        "running": ("extracting_document", 30, "Processando o documento"),
        "completed": ("completed", 100, "Processamento concluído"),
        "partial": ("indexing_document", 70, "Processamento parcialmente concluído"),
        "failed": ("failed", 0, "Falha no processamento do documento"),
        "interrupted": ("failed", 0, "Processamento interrompido"),
    }
    stage, progress, message = stage_map.get(raw_status, ("processing", 50, "Processando"))

    fault_tree = result.get("fault_tree") if isinstance(result, dict) else None
    if fault_tree and raw_status == "completed":
        ft_status = fault_tree.get("generation_status") or ""
        if ft_status == "completed":
            stage, progress, message = "completed", 100, "Processamento e geração concluídos"
        elif ft_status in ("queued", "running"):
            stage, progress, message = "generating_failure_modes", 80, "Gerando modos de falha"
        elif ft_status == "empty":
            stage, progress, message = "completed", 100, "Documento processado — sem modos de falha suficientes"
        elif ft_status == "failed":
            stage, progress, message = "saving_fault_tree", 90, "Documento processado; geração da árvore falhou"

    user_error = None
    raw_error = job.get("error")
    if raw_error and raw_status in ("failed", "interrupted"):
        user_error = _map_ingest_error(raw_error)

    return {
        "status": raw_status,
        "stage": stage,
        "progress": progress,
        "message": message,
        "document_id": job.get("bronze_id"),
        "error": user_error,
        "error_code": _ingest_error_code(raw_status, raw_error),
    }


def _map_ingest_error(raw: str) -> str:
    r = str(raw).lower()
    if "bedrock" in r or "llm" in r or "invoke" in r:
        return "Serviço de processamento de IA temporariamente indisponível."
    if "s3" in r or "upload" in r or "storage" in r:
        return "Falha ao armazenar o arquivo. Tente novamente."
    if "sha256" in r or "hash" in r or "corrupt" in r:
        return "O arquivo enviado parece estar corrompido."
    if "pdf" in r or "extract" in r:
        return "Não foi possível extrair o conteúdo do PDF."
    return "Falha no processamento do documento. Verifique o arquivo e tente novamente."


def _ingest_error_code(status: str, raw_error: Optional[str]) -> Optional[str]:
    if status not in ("failed", "interrupted"):
        return None
    if not raw_error:
        return "PROCESSING_FAILED"
    r = str(raw_error).lower()
    if "bedrock" in r or "llm" in r:
        return "GENERATION_SERVICE_UNAVAILABLE"
    if "s3" in r or "storage" in r:
        return "STORAGE_ERROR"
    if "pdf" in r or "extract" in r:
        return "DOCUMENT_EXTRACTION_FAILED"
    return "PROCESSING_FAILED"


@router.get("/ingest/status/{job_id}")
def ingest_status(
    job_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    """Consulta o status de um job de ingestão assíncrona."""
    import json as _json

    job = db.get_ingest_job(job_id, org_id=tenant.org_id, tenant_id=tenant.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    result_raw = job.get("result")
    result = None
    if result_raw:
        try:
            result = _json.loads(result_raw) if isinstance(result_raw, str) else result_raw
        except Exception:
            pass

    for dt_field in ("created_at", "updated_at"):
        if job.get(dt_field) is not None:
            job[dt_field] = job[dt_field].isoformat()

    user_status = _user_friendly_ingest_status(job)

    return {
        "job_id": job["id"],
        "org_id": job["org_id"],
        "tenant_id": job["tenant_id"],
        "file_name": job["file_name"],
        "status": job["status"],
        "stage": user_status["stage"],
        "progress": user_status["progress"],
        "message": user_status["message"],
        "document_id": user_status["document_id"],
        "bronze_id": job.get("bronze_id"),
        "envelope_id": job.get("envelope_id"),
        "document_type": job.get("document_type"),
        "error": user_status["error"] or job.get("error"),
        "error_code": user_status["error_code"],
        "result": result,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


@router.get("/envelopes/{envelope_id}")
def get_envelope(
    envelope_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    env = envelope_repository.get_by_id(
        envelope_id, org_id=tenant.org_id, tenant_id=tenant.tenant_id
    )
    if env is None:
        raise HTTPException(status_code=404, detail="Envelope não encontrado")
    return env


@router.get("/envelopes")
def list_envelopes(
    document_type: Optional[str] = None,
    limit: int = 100,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    return envelope_repository.list_by_tenant(
        org_id=tenant.org_id,
        tenant_id=tenant.tenant_id,
        document_type=document_type,
        limit=limit,
    )


@router.get("/bronze/{bronze_id}/images")
def get_bronze_images(
    bronze_id: str,
    page_number: Optional[int] = None,
    limit: int = 100,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    return db.get_document_images(
        org_id=tenant.org_id,
        tenant_id=tenant.tenant_id,
        bronze_id=bronze_id,
        page_number=page_number,
        limit=limit,
    )


@router.get("/bronze/{bronze_id}/image/{image_id}")
def get_bronze_image(
    bronze_id: str,
    image_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    img = db.get_image_by_id(
        image_id, org_id=tenant.org_id, tenant_id=tenant.tenant_id
    )
    if img is None:
        raise HTTPException(status_code=404, detail="Imagem não encontrada")
    return img


@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    return search_service.search(
        request, org_id=tenant.org_id, tenant_id=tenant.tenant_id
    )


@router.post("/bronze/{bronze_id}/reprocess")
def reprocess_bronze(
    bronze_id: str,
    tenant: TenantContext = Depends(require_permission("catalog", "write")),
):
    result = silver_layer.process_bronze_document(
        bronze_id, org_id=tenant.org_id, tenant_id=tenant.tenant_id
    )
    if result is None:
        raise HTTPException(status_code=500, detail="Falha no reprocessamento")
    return result.__dict__


@router.get("/status")
def pipeline_status(
    tenant: TenantContext = Depends(require_permission("catalog", "read")),
):
    return orchestrator.get_pipeline_status(
        org_id=tenant.org_id, tenant_id=tenant.tenant_id
    )


@router.get("/health")
def health():
    return {"status": "ok"}


app.include_router(router)
app.include_router(fault_tree_router)
app.include_router(chat_router)
