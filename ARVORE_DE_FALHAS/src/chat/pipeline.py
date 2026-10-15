"""
Pipeline de chat para arvore-de-falhas.
Portado do LTS _run_chat_pipeline, sem features de imagem (V1).
"""
from __future__ import annotations

import re
import unicodedata
import base64
from typing import Any

from loguru import logger

from src.bedrock.bedrock_client import bedrock_client
from src.chat.failure_mode_service import (
    FailureModeChatContext,
    build_failure_mode_chat_context,
    build_failure_mode_store_answer,
)
from src.chat.schemas import ChatDocument, ChatImage, ChatResponse, ChatSource, IncomingChatImage
from src.chat.store import ChatStore, SummaryStore
from src.config import settings
from src.fault_tree.ingest_adapter import IngestAdapter
from src.storage import s3_client
from src.storage.s3_client import resolve_bucket_and_key
from src.storage.postgres_client import postgres_client


# ── Claude wrapper ────────────────────────────────────────────────

class LLMUnavailableError(RuntimeError):
    """LLM (Bedrock/Claude) não pôde ser alcançado ou retornou erro inesperado."""


def _chat_claude(*, system: str | None = None, messages: list[dict], max_tokens: int = 1100) -> str:
    body: dict = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "messages": messages,
    }
    if system:
        body["system"] = system
    try:
        result = bedrock_client._invoke_model_raw(body)
        for block in result.get("content", []):
            if block.get("type") == "text":
                return block["text"]
        raise LLMUnavailableError("Bedrock retornou resposta sem bloco de texto")
    except LLMUnavailableError:
        raise
    except Exception as e:
        logger.error(f"_chat_claude error: {e}")
        raise LLMUnavailableError(str(e)) from e


# ── Text normalisation helpers ────────────────────────────────────

def _normalize_text(value: Any) -> str:
    base = unicodedata.normalize("NFKD", str(value or ""))
    no_accents = "".join(char for char in base if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s/-]", " ", no_accents.lower())).strip()


def _tokenize(text: str) -> list[str]:
    stopwords = {"a","o","as","os","um","uma","de","da","do","e","em","no","na","para","por","com",
                 "sem","sobre","que","qual","como","onde","quando","tem","ter","essa","esse","isto","isso"}
    normalized = _normalize_text(text)
    tokens: list[str] = []
    seen: set[str] = set()
    for token in normalized.split():
        if len(token) < 2 or token in stopwords or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


# ── Greeting / history helpers ────────────────────────────────────

def parece_saudacao(msg: str) -> bool:
    texto = (msg or "").strip().lower()
    if not texto:
        return True
    if len(texto) <= 12 and texto in {"oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "eai", "e aí"}:
        return True
    if len(texto) <= 20 and any(
        texto == p or texto.startswith(p + " ")
        for p in ["oi", "olá", "ola", "bom", "boa"]
    ):
        return True
    return False


def normalizar_historico(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []

    def _append(role: str, text: str) -> None:
        if not text:
            return
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"][0]["text"] += "\n\n" + text
            return
        msgs.append({"role": role, "content": [{"type": "text", "text": text}]})

    for item in history:
        role = item.get("role")
        content = str(item.get("content", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        _append(role, content)

    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    return msgs


def pergunta_parece_independente(pergunta: str) -> bool:
    texto = (pergunta or "").strip()
    if not texto:
        return False
    texto_norm = _normalize_text(texto)
    marcadores_dependentes = ("e esse","e essa","e isso","e aquilo","o que devo","como devo","esse ai","aquele","aquela","isso","nisso","nele","nela")
    if any(texto_norm.startswith(m) for m in marcadores_dependentes):
        return False
    tokens = _tokenize(texto)
    if len(tokens) >= 2:
        return True
    if re.search(r"\b[wWhHgG]\d{2,3}\b", texto):
        return True
    if len(texto_norm) >= 16 and "?" in texto:
        return True
    return False


# ── Rolling summary ───────────────────────────────────────────────

_SUMMARY_SYSTEM = """Você cria e mantém um resumo de conversa.
Objetivo: registrar contexto, definições e decisões importantes (sem detalhes desnecessários).
Regras: escreva em português; seja conciso (máximo ~12 linhas); inclua componentes citados, parâmetros, restrições; não invente informações."""

def atualizar_resumo_se_necessario(
    *, equipment_id: str, org_id: str, tenant_id: str,
    chat_store: ChatStore, summary_store: SummaryStore,
    trigger_delta_lines: int = 30, chunk_lines: int = 60,
) -> str:
    resumo_atual, resumido_ate = summary_store.get(equipment_id, org_id, tenant_id)
    total = chat_store.count(equipment_id, org_id, tenant_id)
    if total - resumido_ate < trigger_delta_lines:
        return resumo_atual
    start = resumido_ate
    end = min(total, resumido_ate + chunk_lines)
    novos = chat_store.read_range(equipment_id, org_id, tenant_id, start=start, end=end)
    if not novos:
        return resumo_atual
    linhas = [f"{'Usuário' if m['role']=='user' else 'Assistente'}: {m['content']}"
              for m in novos if m.get("content")]
    bloco = "\n".join(linhas).strip()
    if not bloco:
        return resumo_atual
    prompt = (f"RESUMO ATUAL:\n{resumo_atual or '(vazio)'}\n\nNOVAS MENSAGENS:\n{bloco}\n\nRetorne o NOVO RESUMO atualizado:")
    try:
        novo_resumo = _chat_claude(
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            max_tokens=320,
        )
        novo_resumo = (novo_resumo or "").strip()
        if novo_resumo:
            summary_store.set(equipment_id, org_id, tenant_id, novo_resumo, end)
            return novo_resumo
    except Exception:
        pass
    return resumo_atual


# ── Question rewrite ──────────────────────────────────────────────

_REWRITE_SYSTEM = """Você reescreve perguntas curtas em perguntas completas para busca em manual técnico.
Regras: não responda a pergunta; retorne apenas a pergunta reescrita (uma única linha); só use o histórico se a pergunta atual realmente depender dele; preserve o idioma (português)."""

def reescrever_pergunta_para_busca(*, pergunta_atual: str, resumo: str, historico: list[dict[str, Any]]) -> str:
    pergunta = (pergunta_atual or "").strip()
    if not pergunta:
        return ""
    if pergunta_parece_independente(pergunta):
        return pergunta
    hist = historico[-10:] if historico else []
    partes: list[str] = []
    if resumo.strip():
        partes.append(f"[RESUMO]\n{resumo.strip()}")
    if hist:
        recent_exchange: list[str] = []
        recent_user_only: list[str] = []
        for mensagem in hist:
            role = str(mensagem.get("role", "")).strip()
            content = str(mensagem.get("content", "") or "").strip()
            if role == "user" and content:
                recent_user_only.append(f"Usuário: {content}")
            if role in {"user", "assistant"} and content:
                prefix = "Usuário" if role == "user" else "Assistente"
                recent_exchange.append(f"{prefix}: {content}")
        if recent_exchange:
            partes.append("[ÚLTIMA TROCA]\n" + "\n".join(recent_exchange[-2:]))
        if recent_user_only:
            partes.append("[PERGUNTAS RECENTES]\n" + "\n".join(recent_user_only[-4:]))
    contexto = "\n\n".join(partes).strip()
    user_text = (f"Reescreva a PERGUNTA ATUAL para que fique completa e independente para busca.\n\n"
                 f"{contexto}\n\nPERGUNTA ATUAL: {pergunta}\n\nRetorne apenas a pergunta reescrita:")
    try:
        out = _chat_claude(system=_REWRITE_SYSTEM,
                           messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
                           max_tokens=120)
        out = (out or "").strip().strip('"').strip("'")
        out_lines = [line.strip() for line in out.splitlines() if line.strip()]
        if not out_lines:
            return pergunta
        candidata = out_lines[0]
        if len(candidata) > len(pergunta) * 3:
            return pergunta
        return candidata
    except Exception:
        return pergunta


# ── Failure-mode intent detection ─────────────────────────────────

def _chat_question_wants_failure_modes(question: str) -> bool:
    normalized = _normalize_text(question)
    if not normalized:
        return False
    explicit_terms = ["modo de falha","modos de falha","arvore de falha","arvore de falhas","fmea",
                      "falha","falhas","defeito","defeitos","causa da falha","causas da falha",
                      "efeito da falha","efeitos da falha","acao de falha","acoes de falha",
                      "diagnostico de falha","sintoma de falha","sintomas de falha"]
    if any(_normalize_text(term) in normalized for term in explicit_terms):
        return True
    patterns = [r"\bger(e|ar|a)\s+.*\bfalhas?\b", r"\bcrie\s+.*\bfalhas?\b", r"\bliste\s+.*\bfalhas?\b",
                r"\bquais\s+.*\bfalhas?\b", r"\bo\s+que\s+pode\s+falhar\b", r"\bpor\s+que\s+.*\bfalha\b",
                r"\bcausas?\s+.*\bfalhas?\b", r"\befeit(os?|a)\s+.*\bfalhas?\b"]
    return any(re.search(p, normalized) for p in patterns)


def _chat_question_explicitly_targets_failure_mode_store(question: str) -> bool:
    normalized = _normalize_text(question)
    if not normalized:
        return False
    manual_markers = ("segundo o manual", "no manual", "do manual", "manual menciona", "manual apresenta")
    if any(m in normalized for m in manual_markers):
        return False
    store_markers = ("cadastrad","atualmente cadastr","tela de modos de falha","arvore de falha",
                     "arvore de falhas","fta","gerados por ia","geradas por ia","modos existentes","falhas existentes")
    if any(m in normalized for m in store_markers):
        return True
    if "modo de falha" in normalized or "modos de falha" in normalized:
        return True
    detail_terms = ("acoes", "acao", "causas", "causa", "efeitos", "efeito", "severidade")
    return "falha" in normalized and any(t in normalized for t in detail_terms)


def _build_failure_mode_search_query(question: str) -> str:
    normalized = _normalize_text(question)
    parts = [question,
             "falha fault erro error alarme alarm código code diagnóstico diagnostic troubleshooting causa efeito ação corretiva solução",
             "sistema elétrico controle automação PLC CNC sensor atuador comunicação desgaste travamento vibração",
             "processo qualidade defeito utilidades hidráulico pneumático pressão temperatura manutenção"]
    if "alarme" in normalized or "codigo" in normalized or "erro" in normalized:
        parts.append("tabela de alarmes alarm list fault code error code diagnostic cause remedy")
    return "\n".join(p for p in parts if str(p or "").strip())


# ── RAG search ────────────────────────────────────────────────────

def _search_ingest_context(adapter: IngestAdapter, *, query: str, context_ids: list[str] | None, doc_id_to_filename: dict[str, str] | None = None, limit: int = 8) -> dict[str, Any]:
    empty: dict[str, Any] = {"context_text": "", "sources": [], "filenames": []}
    # Explicit empty list means "no bound documents" — skip search entirely
    # to avoid unscoped searches across unrelated tenant documents.
    if context_ids is not None and len(context_ids) == 0:
        return empty
    safe_query = (query or "").strip()
    if not safe_query:
        return empty
    try:
        payload = adapter.search_text(query=safe_query, context_ids=context_ids, limit=limit, min_score=0.0)
        citations = payload.get("citations", [])
        if not citations:
            return empty
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        filenames: list[str] = []
        seen_keys: set[str] = set()
        for cit in citations:
            text = str(cit.get("text") or "").strip()
            doc_id = str(cit.get("document_id") or "").strip()
            chunk_id = str(cit.get("id") or "").strip()
            page = cit.get("page_number")
            score = float(cit.get("score") or 0.0)
            if text:
                parts.append(text)
            key = f"{doc_id}:{chunk_id}:{page}"
            if key not in seen_keys:
                seen_keys.add(key)
                fname = (doc_id_to_filename or {}).get(doc_id, doc_id)
                sources.append({"filename": fname, "doc_id": doc_id, "chunk_id": chunk_id,
                                 "page_start": page, "page_end": page, "score": score})
            if doc_id:
                fname = (doc_id_to_filename or {}).get(doc_id, doc_id)
                if fname not in filenames:
                    filenames.append(fname)
        return {"context_text": "\n\n".join(parts), "sources": sources, "filenames": filenames}
    except Exception as e:
        logger.warning(f"_search_ingest_context error: {e}")
        return empty


def _chat_manual_context_found(ingest_chat_context: dict[str, Any]) -> bool:
    if str(ingest_chat_context.get("context_text") or "").strip():
        return True
    sources = ingest_chat_context.get("sources", [])
    return isinstance(sources, list) and len(sources) > 0


def _apply_context_budget(context_text: str) -> str:
    """Limita o prompt sem descartar completamente os trechos finais recuperados."""
    budget = max(1_000, settings.CHAT_CONTEXT_MAX_CHARS)
    if len(context_text) <= budget:
        return context_text
    head_size = int(budget * 0.75)
    tail_size = budget - head_size
    return context_text[:head_size] + "\n\n[... contexto reduzido ...]\n\n" + context_text[-tail_size:]


# ── No-solution grounding ─────────────────────────────────────────

def _chat_no_solution_identifier_answer(*, question: str, search_query: str, context_text: str) -> str | None:
    normalized_q = _normalize_text(question)
    corrective_markers = ("o que devo verificar","o que fazer","qual a acao","como resolver","como corrigir","acao recomendada")
    if not any(m in normalized_q for m in corrective_markers):
        return None
    codes = re.findall(r"(?<!\d)\d{4,10}(?!\d)", str(search_query or ""))
    if not codes:
        return None
    normalized_context = _normalize_text(context_text)
    if "nenhuma solucao" not in normalized_context and "no solution" not in normalized_context:
        return None
    for code in codes:
        for match in re.finditer(re.escape(code), context_text):
            start = max(0, match.start() - 500)
            end = min(len(context_text), match.end() + 1000)
            segment = _normalize_text(context_text[start:end])
            if "nenhuma solucao" not in segment and "no solution" not in segment:
                continue
            kind = "mensagem informativa" if any(m in segment for m in ("mensagens plc","mensagem plc")) else "registro do manual"
            return (f"Para o código {code}, o trecho recuperado do manual registra \"Nenhuma solução\". "
                    f"Portanto, esse {kind} não possui uma verificação ou ação corretiva prescrita nesse trecho. "
                    "Não aplique um procedimento genérico de troubleshooting como se fosse um alarme de falha; "
                    "considere apenas a condição/status informado pelo manual.")
    return None


# ── Answer cleanup ────────────────────────────────────────────────

def _finalize_answer(answer: str, *, asked_failure_modes: bool) -> str:
    text = (answer or "").strip()
    if not text:
        return text
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    cleaned: list[str] = []
    for paragraph in paragraphs:
        normalized = _normalize_text(paragraph)
        if not asked_failure_modes and "modo" in normalized and "falha" in normalized:
            continue
        if "sem mencionar modos de falha" in normalized:
            continue
        if "os modos de falha nao foram consultados" in normalized:
            continue
        paragraph = paragraph.replace("manual processado pelo Data Ingest", "manual indexado")
        paragraph = paragraph.replace("Data Ingest", "manual")
        paragraph = re.sub(r"(?i)\s*,?\s*sem consulta a modos de falha cadastrados[^.]*\.", ".", paragraph)
        paragraph = re.sub(r"(?i)\s*portanto, a resposta se baseia exclusivamente[^.]*\.", "", paragraph).strip()
        if paragraph:
            cleaned.append(paragraph)
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(cleaned)).strip()


# ── Main pipeline ─────────────────────────────────────────────────

def run_chat_pipeline(
    *,
    equipment_id: str,
    org_id: str,
    tenant_id: str,
    message: str,
    context_ids: list[str] | None = None,
    uploaded_images: list[IncomingChatImage] | None = None,
    chat_store: ChatStore,
    summary_store: SummaryStore,
) -> ChatResponse:
    msg = (message or "").strip()
    image_blocks = _build_uploaded_image_blocks(uploaded_images or [])
    if not msg and not image_blocks:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Mensagem vazia.")

    if parece_saudacao(msg):
        answer = (
            "Olá! Posso responder perguntas sobre o manual técnico deste equipamento e consultar "
            "os modos de falha cadastrados na árvore de falhas. Como posso ajudar?"
        )
        chat_store.append(equipment_id, org_id, tenant_id, "user", msg)
        chat_store.append(equipment_id, org_id, tenant_id, "assistant", answer)
        return ChatResponse(answer=answer, used_rag=False, found_in_doc=False, sources=[])

    history = chat_store.read_last(equipment_id, org_id, tenant_id, limit=20)
    normalized_history = normalizar_historico(history[-8:])

    resumo = atualizar_resumo_se_necessario(
        equipment_id=equipment_id, org_id=org_id, tenant_id=tenant_id,
        chat_store=chat_store, summary_store=summary_store,
    )

    asked_failure_modes = _chat_question_wants_failure_modes(msg)
    explicit_failure_store_query = _chat_question_explicitly_targets_failure_mode_store(msg)

    query_para_busca = reescrever_pergunta_para_busca(pergunta_atual=msg, resumo=resumo, historico=history)

    if asked_failure_modes and not explicit_failure_store_query:
        query_para_busca = _build_failure_mode_search_query(msg)

    # Load failure modes from fault tree
    failure_modes_items: list[dict[str, Any]] = []
    try:
        tree = postgres_client.get_equipment_fault_tree(
            org_id=org_id, tenant_id=tenant_id, equipment_id=equipment_id
        )
        if tree:
            raw_fm = tree.get("failure_modes")
            if isinstance(raw_fm, list):
                failure_modes_items = raw_fm
            elif isinstance(raw_fm, dict):
                failure_modes_items = raw_fm.get("items", [])
    except Exception:
        failure_modes_items = []

    if asked_failure_modes:
        failure_query = msg if explicit_failure_store_query else "\n".join([query_para_busca, msg]).strip()
        failure_modes_context = build_failure_mode_chat_context(
            failure_modes_items, failure_query, max_items=12 if explicit_failure_store_query else 8,
        )
    else:
        failure_modes_context = build_failure_mode_chat_context([], "", max_items=0)

    # Explicit failure store query → deterministic answer
    if explicit_failure_store_query:
        answer = build_failure_mode_store_answer(failure_modes_context, msg)
        chat_store.append(equipment_id, org_id, tenant_id, "user", msg)
        chat_store.append(equipment_id, org_id, tenant_id, "assistant", answer)
        return ChatResponse(
            answer=answer,
            used_rag=bool(failure_modes_context.found),
            found_in_doc=bool(failure_modes_context.found),
            sources=[ChatSource(**s) for s in failure_modes_context.sources],
        )

    # Bindings são necessários tanto para o escopo padrão de RAG quanto para
    # anexos de resposta. Não dependem de o caller enviar context_ids próprios.
    bindings = postgres_client.list_document_bindings(
        org_id=org_id,
        tenant_id=tenant_id,
        target_type="equipment",
        target_id=equipment_id,
    )
    doc_id_to_filename: dict[str, str] = {
        str(binding["document_id"]): str(binding.get("file_name") or binding["document_id"])
        for binding in bindings if binding.get("document_id")
    }
    for binding in bindings:
        bronze_id = binding.get("document_id")
        if not bronze_id:
            continue
        envelope = postgres_client.get_envelope_by_bronze(
            bronze_id, org_id, binding.get("document_tenant_id", tenant_id)
        )
        if envelope and envelope.get("id"):
            doc_id_to_filename[str(envelope["id"])] = str(binding.get("file_name") or bronze_id)
    if context_ids is None:
        context_ids = [b["document_id"] for b in bindings if b.get("document_id")]
    else:
        # A request may narrow the scope, but cannot expand it beyond the
        # equipment's bound documents. The equipment id is also valid because
        # chunks ingested for an asset carry it in their context_ids metadata.
        allowed_context_ids = set(doc_id_to_filename)
        allowed_context_ids.add(equipment_id)
        context_ids = [context_id for context_id in context_ids if context_id in allowed_context_ids]

    # RAG search
    adapter = IngestAdapter(org_id=org_id, tenant_id=tenant_id)
    ingest_chat_context = _search_ingest_context(adapter, query=query_para_busca, context_ids=context_ids, doc_id_to_filename=doc_id_to_filename)

    # No-solution grounding
    no_solution_answer = _chat_no_solution_identifier_answer(
        question=msg, search_query=query_para_busca, context_text=str(ingest_chat_context.get("context_text") or ""),
    )
    if no_solution_answer:
        chat_store.append(equipment_id, org_id, tenant_id, "user", msg)
        chat_store.append(equipment_id, org_id, tenant_id, "assistant", no_solution_answer)
        raw_sources = ingest_chat_context.get("sources", [])
        sources = raw_sources if isinstance(raw_sources, list) else []
        _add_pdf_urls_to_sources(sources, bindings, org_id, tenant_id)
        return ChatResponse(
            answer=no_solution_answer, used_rag=True, found_in_doc=True,
            sources=[ChatSource(**s) for s in sources],
        )

    has_manual_context = _chat_manual_context_found(ingest_chat_context)
    has_failure_mode_context = bool(asked_failure_modes and failure_modes_context.found)
    found_in_doc = bool(has_manual_context or has_failure_mode_context)
    used_rag = found_in_doc

    # Build sources
    sources_raw: list[dict[str, Any]] = []
    source_keys: set[str] = set()

    def _add_source(source: dict[str, Any]) -> None:
        key = f'{source.get("doc_id")}:{source.get("chunk_id")}:{source.get("page_start")}:{source.get("page_end")}'
        if key in source_keys:
            return
        source_keys.add(key)
        sources_raw.append(source)

    if asked_failure_modes:
        for s in failure_modes_context.sources:
            _add_source(s)
    for s in ingest_chat_context.get("sources", []):
        if isinstance(s, dict):
            _add_source(s)
    _add_pdf_urls_to_sources(sources_raw, bindings, org_id, tenant_id)

    manuais_usados = ingest_chat_context.get("filenames", [])
    manuais_texto = ", ".join(str(f) for f in manuais_usados) if manuais_usados else "manual do equipamento"
    context_text = _apply_context_budget(str(ingest_chat_context.get("context_text") or ""))
    failure_modes_text = failure_modes_context.text if asked_failure_modes and failure_modes_context.text else ""

    optional_sections: list[str] = []
    if asked_failure_modes:
        optional_sections.append(
            "[MODOS DE FALHA CADASTRADOS/EDITADOS NO ATIVO]\n"
            + (failure_modes_text or "(não há modos de falha cadastrados relacionados)")
        )

    optional_context_sections = "\n\n".join(s for s in optional_sections if s.strip())

    if found_in_doc:
        user_prompt = (
            "Responda usando somente as fontes internas relevantes do equipamento quando existirem.\n"
            "A fonte principal é o manual processado pelo Data Ingest.\n"
            "Use os Modos de Falha somente quando a pergunta envolver falha, causa, efeito, ação, recomendação ou diagnóstico.\n\n"
            f"Manuais utilizados: {manuais_texto}\n\n"
            f"{optional_context_sections + chr(10) + chr(10) if optional_context_sections else ''}"
            f"[CONTEXTO TEXTUAL DO MANUAL]\n{context_text or '(sem contexto textual recuperado do manual)'}\n\n"
            f"[PERGUNTA DO USUÁRIO]\n{msg}\n\n"
            "Regras obrigatórias:\n"
            "- Baseie a resposta no manual quando houver trecho, citação ou página relacionada.\n"
            "- Não invente página, causa, ação ou procedimento que não esteja no contexto.\n"
            "- Se a pergunta não pedir modos de falha, não cite modos de falha espontaneamente.\n"
            "- Não explique ao usuário quais fontes internas foram ou não consultadas; apenas responda.\n"
            "- Quando a evidência do manual for parcial, deixe isso claro.\n"
            "- Seja técnico, objetivo e prático."
        )
    else:
        user_prompt = (
            "Não foi encontrado conteúdo relacionado no manual indexado do equipamento.\n\n"
            f"{optional_context_sections + chr(10) + chr(10) if optional_context_sections else ''}"
            f"[PERGUNTA DO USUÁRIO]\n{msg}\n\n"
            "Regras obrigatórias:\n"
            "- Comece deixando claro que não foi encontrado conteúdo relacionado no manual.\n"
            "- Depois disso, dê uma orientação geral, sem fingir que veio do manual.\n"
            "- Não cite páginas ou fontes internas que não foram recuperadas.\n"
            "- Seja objetivo e útil."
        )

    system_text = (
        "Você é um assistente técnico do sistema ATI.\n"
        "Responda sempre em português do Brasil.\n"
        "A prioridade é responder com base no manual processado para o equipamento selecionado.\n"
        "Use Modos de Falha cadastrados/editados apenas quando a pergunta pedir falhas, causas de falha, efeitos, ações de falha ou diagnóstico.\n"
        "Se não houver relação nas fontes internas, diga explicitamente que não foi encontrado conteúdo relacionado no manual e só então responda de forma genérica.\n"
        "Nunca exponha cabeçalhos internos, scores, chunks ou nomes de blocos internos do pipeline.\n"
        "Não invente fonte, página, causa ou ação."
    )

    current_question_is_independent = pergunta_parece_independente(msg)
    if resumo.strip() and not current_question_is_independent:
        system_text += "\n\n[RESUMO DA CONVERSA]\n" + resumo.strip()

    messages_for_llm = list(normalized_history) + [{
        "role": "user", "content": [*image_blocks, {"type": "text", "text": user_prompt}]
    }]

    chat_store.append(equipment_id, org_id, tenant_id, "user", msg)

    try:
        answer = _chat_claude(system=system_text, messages=messages_for_llm, max_tokens=1100)
        answer = _finalize_answer(answer, asked_failure_modes=asked_failure_modes)
    except LLMUnavailableError as exc:
        logger.error(f"LLM unavailable: {exc}")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Serviço de linguagem temporariamente indisponível.")
    except Exception as exc:
        logger.error(f"chat_claude failed: {exc}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(exc))

    chat_store.append(equipment_id, org_id, tenant_id, "assistant", answer)

    if settings.CHAT_DEBUG:
        logger.debug(
            "chat trace: equipment={}, sources={}, found_in_doc={}, context_chars={}",
            equipment_id, len(sources_raw), found_in_doc, len(context_text),
        )

    manual_images = _manual_images_for_chat(bindings, sources_raw, msg, org_id, tenant_id)
    documents = _documents_for_chat(bindings, msg)
    return ChatResponse(
        answer=answer,
        used_rag=used_rag,
        found_in_doc=found_in_doc,
        sources=[ChatSource(**s) for s in sources_raw],
        images=manual_images,
        documents=documents,
    )


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _build_uploaded_image_blocks(images: list[IncomingChatImage]) -> list[dict[str, Any]]:
    """Converte anexos seguros em blocos Vision; o conteúdo não é persistido."""
    blocks: list[dict[str, Any]] = []
    for image in images[:4]:
        if image.content_type not in _ALLOWED_IMAGE_TYPES:
            continue
        try:
            raw = base64.b64decode(image.data_base64, validate=True)
        except Exception:
            continue
        if not raw or len(raw) > 5 * 1024 * 1024:
            continue
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": image.content_type, "data": image.data_base64},
        })
    return blocks


def _manual_images_for_chat(
    bindings: list[dict[str, Any]], sources: list[dict[str, Any]], question: str,
    org_id: str, tenant_id: str,
) -> list[ChatImage]:
    explicit_visual_request = bool(re.search(r"\b(imagem|foto|figura|diagrama|esquema|desenho|mostrar)\b", question or "", re.I))
    cited_pages = {source.get("page_start") for source in sources if source.get("page_start") is not None}
    cited_filenames = {str(source.get("filename") or "") for source in sources if source.get("filename")}
    if not explicit_visual_request and not cited_pages:
        return []
    matched_bindings = [
        row for row in bindings
        if row.get("document_id") and (
            not cited_filenames or str(row.get("file_name") or "") in cited_filenames
        )
    ]
    if not matched_bindings:
        return []
    images = []
    for binding in matched_bindings:
        bronze_id = binding["document_id"]
        document_tenant_id = binding.get("document_tenant_id", tenant_id)
        page_candidates = cited_pages or {None}
        for page_number in page_candidates:
            for row in postgres_client.get_document_images(org_id, document_tenant_id, bronze_id=bronze_id, page_number=page_number, limit=4):
                object_path = str(row.get("object_path") or "")
                bucket, object_name = resolve_bucket_and_key(object_path)
                if not object_name:
                    continue
                url = s3_client.generate_download_url(bucket, object_name)
                if url:
                    images.append(ChatImage(
                        id=str(row["id"]), url=url,
                        filename=str(row.get("image_hash") or row["id"]),
                        page_number=row.get("page_number"), description=row.get("vlm_description"),
                        width=row.get("width"), height=row.get("height"),
                        category=row.get("vlm_category"), document_id=str(bronze_id),
                    pdf_url=_pdf_url_for_binding(binding, row.get("page_number")),
                    ))
                if len(images) >= 4:
                    return images
    return images


def _documents_for_chat(bindings: list[dict[str, Any]], question: str) -> list[ChatDocument]:
    if not re.search(r"\b(documento|manual|pdf|baixar|download|arquivo)\b", question or "", re.I):
        return []
    documents: list[ChatDocument] = []
    for row in bindings:
        object_path = str(row.get("object_path") or "")
        if not row.get("document_id") or "/" not in object_path:
            continue
        bucket, object_name = resolve_bucket_and_key(object_path)
        if not object_name:
            continue
        url = s3_client.generate_download_url(bucket, object_name)
        if url:
            documents.append(ChatDocument(
                id=str(row["document_id"]), filename=str(row.get("file_name") or row["document_id"]),
                content_type=row.get("file_type"), download_url=url,
            ))
    return documents[:8]


def _pdf_url_for_binding(binding: dict[str, Any], page_number: Any = None) -> str | None:
    bucket, object_name = resolve_bucket_and_key(binding.get("object_path") or "")
    if not object_name:
        return None
    url = s3_client.generate_download_url(bucket, object_name, expires_in=3600)
    if not url:
        return None
    try:
        page = int(page_number) if page_number is not None else None
    except (TypeError, ValueError):
        page = None
    return f"{url}#page={page}" if page and page > 0 else url


def _add_pdf_urls_to_sources(
    sources: list[dict[str, Any]], bindings: list[dict[str, Any]], org_id: str, tenant_id: str,
) -> None:
    by_document_id = {str(row["document_id"]): row for row in bindings if row.get("document_id")}
    for binding in bindings:
        bronze_id = binding.get("document_id")
        if not bronze_id:
            continue
        document_tenant_id = binding.get("document_tenant_id", tenant_id)
        envelope = postgres_client.get_envelope_by_bronze(bronze_id, org_id, document_tenant_id)
        if envelope and envelope.get("id"):
            by_document_id[str(envelope["id"])] = binding
    for source in sources:
        binding = by_document_id.get(str(source.get("doc_id") or ""))
        if binding:
            source["pdf_url"] = _pdf_url_for_binding(binding, source.get("page_start"))
