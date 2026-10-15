"""Orquestra vínculos, versões e geração de árvores de falhas."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, List, Optional

from src.storage.postgres_client import postgres_client as db


class EmptyGenerationError(RuntimeError):
    """Geração retornou resultado vazio; versão ativa foi preservada."""


@dataclass
class GenerationDocument:
    """Documento resolvido para uso na geração da árvore de falhas."""
    document_id: str        # bronze_id — identificador primário do documento
    bronze_id: str          # == document_id
    envelope_id: Optional[str]
    filename: str
    relationship: str       # "source" ou "inherited"
    scope_type: str         # "tenant" ou "organization"
    storage_tenant_id: Optional[str]  # tenant_id onde o doc foi armazenado (None = org-scope)


def resolve_generation_documents(
    org_id: str,
    tenant_id: Optional[str],
    target_type: str,
    target_id: str,
    document_id: Optional[str] = None,
) -> List[GenerationDocument]:
    """Resolve documentos elegíveis para geração a partir dos bindings do alvo.

    Retorna lista de GenerationDocument resolvidos. Se document_id for fornecido,
    filtra apenas o binding correspondente. Caso contrário, retorna todos os
    bindings ativos com documento em estado elegível.

    O storage_tenant_id retornado representa onde o documento está fisicamente
    armazenado — pode diferir do tenant_id do equipamento para documentos herdados
    de modelo com escopo organizacional.
    """
    bindings = db.list_document_bindings(
        org_id=org_id,
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id,
    )
    if not bindings:
        return []

    results: List[GenerationDocument] = []
    for binding in bindings:
        b_doc_id = binding.get("document_id")
        if not b_doc_id:
            continue
        if document_id and b_doc_id != document_id:
            continue

        doc_status = binding.get("document_status") or ""
        if doc_status not in {"ingested", "processed", "completed", "partial", ""}:
            # Documentos com falha explícita não são elegíveis.
            if doc_status == "failed":
                continue

        storage_tenant = binding.get("document_tenant_id")
        relationship = binding.get("relationship") or "source"
        scope_type = "organization" if storage_tenant is None else "tenant"

        # Resolve envelope_id a partir do banco se não disponível no binding.
        envelope_id = binding.get("envelope_id")
        filename = binding.get("file_name") or ""

        if not envelope_id:
            envelope = db.get_envelope_by_bronze_any_tenant(b_doc_id, org_id)
            if envelope:
                envelope_id = envelope.get("id")
                if not filename:
                    filename = envelope.get("file_name") or ""

        if not filename:
            bronze = db.get_bronze_document_any_tenant(b_doc_id, org_id)
            if bronze:
                filename = bronze.get("file_name") or ""

        results.append(GenerationDocument(
            document_id=b_doc_id,
            bronze_id=b_doc_id,
            envelope_id=envelope_id,
            filename=filename,
            relationship=relationship,
            scope_type=scope_type,
            storage_tenant_id=storage_tenant,
        ))

    return results


def _empty_tree(title: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "titulo": title,
        "causas": [],
        "acoes": [],
        "filhos": [],
    }


def ensure_target_tree(
    *,
    org_id: str,
    tenant_id: Optional[str],
    target_type: str,
    target_id: str,
    model_id: Optional[str],
) -> dict[str, Any]:
    """Obtém ou cria a árvore sem alterar template nem outros equipamentos."""
    if target_type == "model":
        tree = db.get_fault_tree_by_model(target_id, org_id)
        resolved_model_id = target_id
    elif target_type == "equipment":
        if not tenant_id:
            raise ValueError("tenant_id é obrigatório para alvo do tipo equipment")
        tree = db.get_fault_tree_by_equipment(target_id, org_id, tenant_id)
        resolved_model_id = model_id
    else:
        raise ValueError("target_type inválido")

    if tree is not None:
        if (
            target_type == "equipment"
            and resolved_model_id is not None
            and tree.get("model_id") != resolved_model_id
        ):
            raise ValueError("model_id não corresponde ao equipamento já registrado")
        return tree

    tree_id = str(uuid.uuid4())
    if not db.create_fault_tree({
        "id": tree_id,
        "org_id": org_id,
        "model_id": resolved_model_id,
        "tenant_id": tenant_id if target_type == "equipment" else None,
        "equipment_id": target_id if target_type == "equipment" else None,
        "tree_data": _empty_tree(f"Árvore de falhas {target_id}"),
    }):
        raise RuntimeError("Falha ao criar árvore")

    if target_type == "model":
        tree = db.get_fault_tree_by_model(target_id, org_id)
    else:
        tree = db.get_fault_tree_by_equipment(target_id, org_id, tenant_id)
    if tree is None:
        raise RuntimeError("Árvore não encontrada após criação")
    return tree


def create_generation_candidate(
    *,
    tree: dict[str, Any],
    document_id: str,
    envelope_id: Optional[str],
    file_name: str,
    origin_type: str,
    origin_id: str,
) -> dict[str, Any]:
    # Captura snapshot da versão ativa atual para CAS na promoção (R10).
    # Duas gerações simultâneas receberão o mesmo supersedes_version_id.
    # A que terminar por último terá rowcount=0 no supersede step e será descartada.
    active_version = db.get_active_fault_tree_version(tree["id"], tree["org_id"])
    supersedes_version_id = active_version["id"] if active_version else None

    version = db.create_fault_tree_version({
        "tree_id": tree["id"],
        "org_id": tree["org_id"],
        "tenant_id": tree.get("tenant_id"),
        "model_id": tree["model_id"],
        "equipment_id": tree.get("equipment_id"),
        "document_id": document_id,
        "envelope_id": envelope_id,
        "file_name": file_name,
        "status": "queued",
        "supersedes_version_id": supersedes_version_id,
    })
    if version is None:
        raise RuntimeError("Falha ao criar versão candidata da árvore")
    new_version_id = version["id"]
    if not db.link_version_document(
        version_id=new_version_id,
        document_id=document_id,
        envelope_id=envelope_id,
        relationship="source",
        origin_type=origin_type,
        origin_id=origin_id,
    ):
        db.update_fault_tree_version_status(
            new_version_id, tree["org_id"], "failed", "Falha ao vincular documento à versão."
        )
        raise RuntimeError("Falha ao vincular documento à versão")
    db.update_fault_tree_generation_status(tree["id"], tree["org_id"], "queued")
    return version


def run_generation_candidate(
    *,
    tree: dict[str, Any],
    version: dict[str, Any],
    document_id: str,
    file_name: str,
    envelope_id: Optional[str],
    storage_tenant_id: Any = None,
    storage_scope: str = "tenant",
) -> list[dict[str, Any]]:
    """Gera e promove atomicamente; em falha, o resultado ativo anterior permanece.

    storage_scope: "tenant" usa storage_tenant_id (ou equipment tenant como fallback).
                   "organization" força busca com escopo org (tenant=None no Qdrant).
    """
    from src.fault_tree.generator import generate_failure_modes, _STORAGE_TENANT_UNSET

    tree_id = tree["id"]
    org_id = tree["org_id"]

    # Se o documento é org-scoped, passa None explícito (não o unset sentinel).
    # Caso contrário, usa o storage_tenant_id fornecido ou deixa o generator usar
    # o equipment tenant como fallback.
    if storage_scope == "organization":
        effective_storage = None   # None explícito → query org-scoped no Qdrant
    elif storage_tenant_id is not None:
        effective_storage = storage_tenant_id
    else:
        effective_storage = _STORAGE_TENANT_UNSET  # fallback: equipment tenant

    version_id = version["id"]

    def _progress_cb(event: dict) -> None:
        db.update_fault_tree_version_progress(
            version_id=version_id,
            org_id=org_id,
            percent=event.get("percent", 0),
            phase=event.get("phase", ""),
            message=event.get("message", ""),
        )

    db.update_fault_tree_version_status(version_id, org_id, "running")
    db.update_fault_tree_generation_status(tree_id, org_id, "running")
    try:
        items = generate_failure_modes(
            org_id=org_id,
            tenant_id=tree.get("tenant_id"),
            doc_id=document_id,
            filename=file_name,
            bronze_id=document_id,
            envelope_id=envelope_id,
            storage_tenant_id=effective_storage,
            progress_callback=_progress_cb,
        )
        if not items:
            # Resultado vazio: marcar como "empty" e NÃO sobrescrever a versão ativa.
            db.update_fault_tree_version_status(version_id, org_id, "empty")
            db.update_fault_tree_generation_status(tree_id, org_id, "empty")
            raise EmptyGenerationError(
                "Geração retornou zero modos de falha — versão ativa preservada."
            )
        if not db.activate_fault_tree_version(
            version_id, tree_id, org_id, items,
            supersedes_version_id=version.get("supersedes_version_id"),
        ):
            raise RuntimeError("Falha ao promover a nova versão da árvore")
        return items
    except EmptyGenerationError:
        raise  # estado "empty" já gravado; não sobrescrever para "failed"
    except Exception as exc:
        db.update_fault_tree_version_status(version_id, org_id, "failed", str(exc))
        db.update_fault_tree_generation_status(tree_id, org_id, "failed", error=str(exc))
        raise


def process_ingest_target(
    *,
    pipeline_result: dict[str, Any],
    org_id: str,
    tenant_id: Optional[str],
    target_type: str,
    target_id: str,
    model_id: Optional[str],
    file_name: str,
    generate_fault_tree: bool,
) -> dict[str, Any]:
    """Associa o documento e opcionalmente cria uma versão candidata e a promove."""
    bronze_id = str(pipeline_result.get("bronze_id") or "")
    envelope_id = str(pipeline_result.get("envelope_id") or "") or None
    if not bronze_id:
        raise RuntimeError("Pipeline não retornou bronze_id para associar ao alvo")
    if target_type == "equipment" and not tenant_id:
        raise ValueError("tenant_id é obrigatório para alvo do tipo equipment")
    if not db.bind_document(
        document_id=bronze_id,
        envelope_id=envelope_id,
        org_id=org_id,
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id,
        model_id=model_id,
    ):
        raise RuntimeError("Falha ao persistir associação do documento")

    tree = ensure_target_tree(
        org_id=org_id,
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id,
        model_id=model_id,
    )
    response = {
        "tree_id": tree["id"],
        "target_type": target_type,
        "target_id": target_id,
        "model_id": tree["model_id"],
        "generation_status": tree.get("generation_status"),
    }
    if not generate_fault_tree:
        return response

    version = create_generation_candidate(
        tree=tree,
        document_id=bronze_id,
        envelope_id=envelope_id,
        file_name=file_name,
        origin_type=target_type,
        origin_id=target_id,
    )
    response["generation_id"] = version["id"]
    try:
        items = run_generation_candidate(
            tree=tree,
            version=version,
            document_id=bronze_id,
            file_name=file_name,
            envelope_id=envelope_id,
        )
    except EmptyGenerationError as exc:
        # Geração vazia não é falha do pipeline — a versão ativa foi preservada.
        response["generation_status"] = "empty"
        response["generation_note"] = str(exc)
        return response
    response["generation_status"] = "completed"
    response["failure_modes_count"] = len(items)
    return response
