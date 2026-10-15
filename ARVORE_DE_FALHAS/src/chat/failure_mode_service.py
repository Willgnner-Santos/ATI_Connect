"""
Lógica pura de seleção e formatação de modos de falha para o chat.
Copiado de lts_front/backend/app/services/failure_mode_chat_service.py — sem dependências externas.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


STOPWORDS = {
    "a","o","as","os","um","uma","uns","umas","de","da","do","das","dos","e","em","no","na",
    "nos","nas","para","por","com","sem","sobre","que","qual","quais","como","onde","quando",
    "quanto","quantos","tem","ter","esta","este","essa","esse","isso","isto","aquela","aquele",
    "dele","dela","ele","ela","me","minha","meu","suas","seus","fale","fala","explique","explica",
    "informar","informacoes","informação","informações","atualmente","possui","possuem","relacao",
    "relação","relacionado","relacionados","relacionada","relacionadas","direto","direta","diretos","diretas",
}

FAILURE_MODE_INTENT_TERMS = {
    "falha","falhas","modo","modos","causa","causas","acao","acoes","ação","ações",
    "recomendacao","recomendação","recomendacoes","recomendações","procedimento","procedimentos",
    "corrigir","correcao","correção","resolver","solucao","solução","fazer","faco","faço","devo",
    "proceder","manutencao","manutenção","diagnostico","diagnóstico","sintoma","sintomas",
    "efeito","efeitos","risco","defeito","defeitos","problema","problemas",
}

GLOBAL_QUERY_TERMS = {
    "todos","todas","liste","listar","lista","resumo","resuma","geral",
    "cadastrados","cadastradas","existem","tenho","mostrar","mostre",
}

SEVERITY_LABELS = {
    "baixa": "Baixa",
    "media": "Média",
    "alta": "Alta",
    "critica": "Crítica",
}


@dataclass
class FailureModeMatch:
    item: dict[str, Any]
    score: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class FailureModeChatContext:
    text: str
    matches: list[FailureModeMatch]
    sources: list[dict[str, Any]]
    found: bool
    total_items: int


def _normalize_text(value: Any) -> str:
    base = unicodedata.normalize("NFKD", str(value or ""))
    no_accents = "".join(char for char in base if not unicodedata.combining(char))
    no_accents = no_accents.lower()
    no_accents = re.sub(r"[^\w\s/-]", " ", no_accents)
    no_accents = re.sub(r"\s+", " ", no_accents).strip()
    return no_accents


def _tokenize(value: Any) -> list[str]:
    normalized = _normalize_text(value)
    tokens: list[str] = []
    seen: set[str] = set()
    for token in normalized.split():
        token = token.strip()
        if len(token) < 2:
            continue
        if token in STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        text = str(value or "").strip()
        raw = re.split(r"\n+|;|•|\|", text) if text else []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        cleaned = re.sub(r"\s+", " ", str(item or "").strip(" -•\t\r\n"))
        if not cleaned:
            continue
        key = _normalize_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _cause_groups(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw_groups = item.get("cause_groups")
    groups: list[dict[str, Any]] = []
    if isinstance(raw_groups, list):
        for index, raw_group in enumerate(raw_groups, start=1):
            if isinstance(raw_group, dict):
                cause = str(raw_group.get("cause") or raw_group.get("title") or "").strip()
                effects = _as_list(raw_group.get("effects") or raw_group.get("impact") or [])
                actions = _as_list(raw_group.get("actions") or raw_group.get("recommendation") or [])
                cause_id = str(raw_group.get("cause_id") or f"cause-{index}").strip() or f"cause-{index}"
            else:
                cause = str(raw_group or "").strip()
                effects = []
                actions = []
                cause_id = f"cause-{index}"
            if not cause and not effects and not actions:
                continue
            groups.append({"cause_id": cause_id, "cause": cause or "Diagnóstico geral", "effects": effects, "actions": actions})
    if groups:
        return groups
    causes = _as_list(item.get("causes") or item.get("probable_cause") or [])
    effects = _as_list(item.get("effects") or item.get("impact") or [])
    actions = _as_list(item.get("actions") or item.get("recommendation") or [])
    if len(causes) == 1:
        return [{"cause_id": "flat-1", "cause": causes[0], "effects": effects, "actions": actions}]
    if len(causes) > 1:
        output = [{"cause_id": f"flat-{i}", "cause": c, "effects": [], "actions": []} for i, c in enumerate(causes, start=1)]
        if effects or actions:
            output.append({"cause_id": "flat-actions", "cause": "Diagnóstico geral", "effects": effects, "actions": actions})
        return output
    if effects or actions:
        return [{"cause_id": "flat-actions", "cause": "Diagnóstico geral", "effects": effects, "actions": actions}]
    return []


def _item_text_parts(item: dict[str, Any]) -> dict[str, str]:
    tags = _as_list(item.get("tags") or item.get("symptoms") or [])
    causes = _as_list(item.get("causes") or item.get("probable_cause") or [])
    effects = _as_list(item.get("effects") or item.get("impact") or [])
    actions = _as_list(item.get("actions") or item.get("recommendation") or [])
    groups = _cause_groups(item)
    group_text = " ".join(
        " ".join([str(g.get("cause") or ""), " ".join(_as_list(g.get("effects") or [])), " ".join(_as_list(g.get("actions") or []))])
        for g in groups
    )
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()
    category = str(item.get("category") or "Geral").strip()
    severity = str(item.get("severity") or "media").strip()
    return {
        "title": title, "description": description, "category": category, "severity": severity,
        "tags": " ".join(tags), "causes": " ".join(causes), "effects": " ".join(effects),
        "actions": " ".join(actions), "groups": group_text,
        "all": " ".join([title, description, category, severity, " ".join(tags), " ".join(causes), " ".join(effects),
                         " ".join(actions), group_text, str(item.get("probable_cause") or ""),
                         str(item.get("symptoms") or ""), str(item.get("impact") or ""),
                         str(item.get("recommendation") or ""), str(item.get("source_doc_filename") or "")]),
    }


def _query_mentions_failure_modes(query: str) -> bool:
    normalized = _normalize_text(query)
    tokens = set(_tokenize(query))
    intent_tokens = {_normalize_text(term) for term in FAILURE_MODE_INTENT_TERMS}
    if tokens & intent_tokens:
        return True
    return any(term in normalized for term in intent_tokens if len(term) >= 4)


def _query_is_global(query: str) -> bool:
    normalized = _normalize_text(query)
    tokens = set(_tokenize(query))
    global_tokens = {_normalize_text(term) for term in GLOBAL_QUERY_TERMS}
    if tokens & global_tokens:
        return True
    global_phrases = ("quais falhas", "modos de falha", "causas cadastradas", "acoes cadastradas",
                      "ações cadastradas", "o que tem cadastrado", "me mostre as falhas")
    return any(_normalize_text(phrase) in normalized for phrase in global_phrases)


def _query_subject_tokens(query: str) -> list[str]:
    ignored = {_normalize_text(term) for term in (FAILURE_MODE_INTENT_TERMS | GLOBAL_QUERY_TERMS)}
    ignored.update({"atualmente","possui","possuem","relacao","relacionado","relacionados","relacionada",
                    "relacionadas","direto","direta","critico","critica","criticos","criticas","alta","altas",
                    "severidade","prioridade","prioritarias","prioritarios","cadastrado","cadastrada",
                    "cadastrados","cadastradas","sao","ser","seriam","seria"})
    return [token for token in _tokenize(query) if token not in ignored]


def _item_has_direct_subject_match(item: dict[str, Any], subject_tokens: list[str], *, strict_title: bool = False) -> bool:
    if not subject_tokens:
        return True
    parts = _item_text_parts(item)
    title = _normalize_text(parts.get("title"))
    category = _normalize_text(parts.get("category"))
    tags = _normalize_text(parts.get("tags"))
    primary = " ".join([title, category]).strip()
    if strict_title:
        return all(token in title for token in subject_tokens)
    if all(token in primary for token in subject_tokens):
        return True
    if len(subject_tokens) == 1 and subject_tokens[0] in title:
        return True
    if len(subject_tokens) >= 2:
        return False
    return False


def _score_item(item: dict[str, Any], query_tokens: list[str]) -> FailureModeMatch:
    parts = _item_text_parts(item)
    normalized_parts = {name: _normalize_text(text) for name, text in parts.items()}
    score = 0.0
    matched_terms: list[str] = []
    for token in query_tokens:
        token_score = 0.0
        if token in normalized_parts["title"]:
            token_score += 5.0
        if token in normalized_parts["tags"]:
            token_score += 4.0
        if token in normalized_parts["causes"] or token in normalized_parts["groups"]:
            token_score += 3.5
        if token in normalized_parts["actions"]:
            token_score += 3.5
        if token in normalized_parts["description"]:
            token_score += 2.5
        if token in normalized_parts["category"] or token in normalized_parts["severity"]:
            token_score += 1.5
        if token in normalized_parts["effects"]:
            token_score += 2.0
        if token in normalized_parts["all"] and token_score <= 0:
            token_score += 1.0
        if token_score > 0:
            score += token_score
            matched_terms.append(token)
    source_type = str(item.get("source_type") or "").strip().lower()
    if source_type == "manual":
        score += 0.35
    if _as_list(item.get("actions") or item.get("recommendation") or []):
        score += 0.2
    return FailureModeMatch(item=item, score=score, matched_terms=matched_terms)


def _query_requests_critical_modes(query: str) -> bool:
    normalized = _normalize_text(query)
    return any(phrase in normalized for phrase in (
        "falhas criticas","falhas críticas","modos criticos","modos críticos",
        "modos de falha criticos","modos de falha críticos","acoes criticas",
        "ações críticas","severidade critica","severidade crítica",
    ))


def _query_requests_actions(query: str) -> bool:
    tokens = set(_tokenize(query))
    return bool(tokens.intersection({"acao","acoes","recomendacao","recomendacoes","procedimento","procedimentos","corrigir","resolver"}))


def _query_requests_causes(query: str) -> bool:
    tokens = set(_tokenize(query))
    return bool(tokens.intersection({"causa","causas","origem","motivo","motivos"}))


def _query_requests_effects(query: str) -> bool:
    tokens = set(_tokenize(query))
    return bool(tokens.intersection({"efeito","efeitos","impacto","impactos","consequencia","consequencias"}))


def build_failure_mode_store_answer(context: FailureModeChatContext, query: str) -> str:
    if not context.found:
        return "Não há modos de falha cadastrados para este ativo."
    if not context.matches:
        return (f"Há {context.total_items} modo(s) de falha cadastrado(s) neste ativo, "
                "mas nenhum deles possui relação direta com o assunto solicitado.")
    want_actions = _query_requests_actions(query)
    want_causes = _query_requests_causes(query)
    want_effects = _query_requests_effects(query)
    want_critical = _query_requests_critical_modes(query)
    lines: list[str] = ["Com base nos Modos de Falha atualmente cadastrados neste ativo:"]
    shown = 0
    for match in context.matches:
        item = match.item
        severity = _normalize_text(item.get("severity") or "media")
        if want_critical and severity not in {"alta", "critica", "critical", "high"}:
            continue
        shown += 1
        title = str(item.get("title") or "Modo de falha sem título").strip()
        category = str(item.get("category") or "Geral").strip()
        severity_label = SEVERITY_LABELS.get(severity, str(item.get("severity") or "Média").strip())
        pages = [str(p) for p in (item.get("source_pages") or []) if str(p).strip()]
        alarms = _as_list(item.get("alarm_codes") or [])
        causes = _as_list(item.get("causes") or item.get("probable_cause") or [])
        effects = _as_list(item.get("effects") or item.get("impact") or [])
        actions = _as_list(item.get("actions") or item.get("recommendation") or [])
        meta = [f"categoria: {category}", f"severidade: {severity_label}"]
        if pages:
            meta.append("páginas: " + ", ".join(pages[:6]))
        if alarms:
            meta.append("alarmes: " + ", ".join(alarms[:3]))
        lines.append(f"\n{shown}. {title} ({'; '.join(meta)})")
        if want_causes:
            lines.extend(f"   - Causa: {v}" for v in causes[:4])
            if not causes:
                lines.append("   - Causa: não cadastrada.")
        if want_effects:
            lines.extend(f"   - Efeito: {v}" for v in effects[:4])
            if not effects:
                lines.append("   - Efeito: não cadastrado.")
        if want_actions:
            lines.extend(f"   - Ação: {v}" for v in actions[:5])
            if not actions:
                lines.append("   - Ação: não cadastrada.")
        if not (want_actions or want_causes or want_effects):
            description = str(item.get("description") or "").strip()
            if description:
                lines.append(f"   - {description[:320]}")
    if shown == 0 and want_critical:
        return ("Não encontrei modos de falha de severidade alta/crítica entre os itens "
                "cadastrados que correspondem ao filtro solicitado.")
    return "\n".join(lines).strip()


def select_relevant_failure_modes(items: list[dict[str, Any]], query: str, *, max_items: int = 8) -> list[FailureModeMatch]:
    valid_items = [item for item in items if isinstance(item, dict)]
    if not valid_items:
        return []
    if _query_requests_critical_modes(query):
        critical_items = [item for item in valid_items
                          if _normalize_text(item.get("severity") or "") in {"alta","critica","critical","high"}]
        if critical_items:
            valid_items = critical_items
    query_tokens = _tokenize(query)
    subject_tokens = _query_subject_tokens(query)
    global_query = _query_is_global(query)
    has_failure_mode_intent = _query_mentions_failure_modes(query)
    scored = [_score_item(item, query_tokens) for item in valid_items]
    if global_query and not subject_tokens:
        scored.sort(key=lambda m: (m.score, str(m.item.get("updated_at") or "")), reverse=True)
        return scored[:max_items]
    if has_failure_mode_intent and subject_tokens:
        normalized_query = _normalize_text(query)
        strict_title = any(phrase in normalized_query for phrase in (
            "relacao direta","diretamente relacionado","diretamente relacionados",
            "diretamente relacionada","diretamente relacionadas",
        ))
        scored = [m for m in scored if _item_has_direct_subject_match(m.item, subject_tokens, strict_title=strict_title)]
    if has_failure_mode_intent and not query_tokens:
        scored.sort(key=lambda m: (m.score, str(m.item.get("updated_at") or "")), reverse=True)
        return scored[:max_items]
    threshold = 1.0
    if has_failure_mode_intent:
        threshold = 0.35
    relevant = [m for m in scored if m.score >= threshold]
    relevant.sort(key=lambda m: m.score, reverse=True)
    if relevant:
        return relevant[:max_items]
    if has_failure_mode_intent:
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:min(max_items, 5)]
    return []


def _format_bullet_list(values: Any, *, indent: str = "  ") -> list[str]:
    return [f"{indent}- {item}" for item in _as_list(values)]


def _format_failure_mode_match(match: FailureModeMatch, index: int) -> str:
    item = match.item
    mode_id = str(item.get("mode_id") or f"modo-{index}").strip() or f"modo-{index}"
    title = str(item.get("title") or "Modo de falha sem título").strip()
    description = str(item.get("description") or "").strip()
    category = str(item.get("category") or "Geral").strip() or "Geral"
    severity = str(item.get("severity") or "media").strip().lower() or "media"
    severity_label = SEVERITY_LABELS.get(severity, severity)
    source_type = str(item.get("source_type") or "manual").strip().lower()
    source_label = "manual/editado" if source_type == "manual" else "automático"
    source_doc_filename = str(item.get("source_doc_filename") or "").strip()
    tags = _as_list(item.get("tags") or item.get("symptoms") or [])
    source_pages = [str(p) for p in item.get("source_pages", []) if str(p).strip()] if isinstance(item.get("source_pages"), list) else []
    alarm_codes = _as_list(item.get("alarm_codes") or [])
    alarm_evidence = item.get("alarm_evidence") if isinstance(item.get("alarm_evidence"), list) else []
    alarm_labels = {str(e.get("code") or "").strip(): str(e.get("label") or "").strip()
                    for e in alarm_evidence if isinstance(e, dict) and str(e.get("code") or "").strip()}
    groups = _cause_groups(item)
    actions = _as_list(item.get("actions") or item.get("recommendation") or [])
    effects = _as_list(item.get("effects") or item.get("impact") or [])
    lines: list[str] = []
    lines.append(f"Modo {index}: {title}")
    lines.append(f"- id: {mode_id}")
    lines.append(f"- categoria: {category}")
    lines.append(f"- severidade: {severity_label}")
    lines.append(f"- origem: {source_label}" + (f" ({source_doc_filename})" if source_doc_filename else ""))
    if description:
        lines.append(f"- descrição: {description}")
    if tags:
        lines.append(f"- tags: {', '.join(tags)}")
    if source_pages:
        lines.append(f"- páginas de evidência: {', '.join(source_pages)}")
    if alarm_codes:
        rendered_alarms = [f"{code} — {alarm_labels[code]}" if alarm_labels.get(code) else code for code in alarm_codes]
        lines.append(f"- códigos de alarme/erro: {', '.join(rendered_alarms)}")
    if match.matched_terms:
        lines.append(f"- termos encontrados na pergunta: {', '.join(match.matched_terms)}")
    if groups:
        lines.append("- causas vinculadas, efeitos e ações:")
        for gi, group in enumerate(groups, start=1):
            cause = str(group.get("cause") or "Diagnóstico geral").strip() or "Diagnóstico geral"
            lines.append(f"  Causa {gi}: {cause}")
            group_effects = _as_list(group.get("effects") or [])
            group_actions = _as_list(group.get("actions") or [])
            if group_effects:
                lines.append("    Efeitos/impactos:")
                lines.extend(_format_bullet_list(group_effects, indent="      "))
            if group_actions:
                lines.append("    Ações recomendadas:")
                lines.extend(_format_bullet_list(group_actions, indent="      "))
            if not group_effects and not group_actions:
                lines.append("    Ações recomendadas: (nenhuma ação específica vinculada a esta causa)")
    else:
        lines.append("- causas vinculadas, efeitos e ações: não cadastrados")
    if actions:
        lines.append("- ações agregadas do modo:")
        lines.extend(_format_bullet_list(actions, indent="  "))
    if effects:
        lines.append("- efeitos/impactos agregados:")
        lines.extend(_format_bullet_list(effects, indent="  "))
    return "\n".join(lines).strip()


def build_failure_mode_sources(matches: list[FailureModeMatch]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for index, match in enumerate(matches, start=1):
        item = match.item
        mode_id = str(item.get("mode_id") or f"modo-{index}").strip() or f"modo-{index}"
        title = str(item.get("title") or f"Modo de falha {index}").strip() or f"Modo de falha {index}"
        source_doc_filename = str(item.get("source_doc_filename") or "").strip()
        source_doc_id = str(item.get("source_doc_id") or "failure-modes").strip() or "failure-modes"
        pages: list[int] = []
        raw_pages = item.get("source_pages")
        if isinstance(raw_pages, list):
            for raw_page in raw_pages:
                try:
                    page = int(raw_page)
                except (TypeError, ValueError):
                    continue
                if page > 0 and page not in pages:
                    pages.append(page)
        pages.sort()
        filename = (f"{source_doc_filename} - Modo de Falha - {title[:70]}" if source_doc_filename
                    else f"Modos de Falha - {title[:80]}")
        sources.append({
            "filename": filename, "doc_id": source_doc_id, "chunk_id": f"failure-mode:{mode_id}",
            "page_start": pages[0] if pages else None, "page_end": pages[-1] if pages else None,
            "score": float(round(match.score, 4)),
        })
    return sources


def build_failure_mode_chat_context(items: list[dict[str, Any]], query: str, *, max_items: int = 8) -> FailureModeChatContext:
    valid_items = [item for item in items if isinstance(item, dict)]
    matches = select_relevant_failure_modes(valid_items, query, max_items=max_items)
    if not valid_items:
        return FailureModeChatContext(text="(nenhum modo de falha cadastrado para este ativo)", matches=[], sources=[], found=False, total_items=0)
    total_items = len(valid_items)
    auto_count = sum(1 for item in valid_items if str(item.get("source_type") or "manual").strip().lower() in {"auto","automatico","automático","ia","ai"})
    manual_count = total_items - auto_count
    auto_percent = round((auto_count / total_items) * 100) if total_items else 0
    manual_percent = 100 - auto_percent if total_items else 0
    if not matches:
        return FailureModeChatContext(
            text=(f"Resumo real dos Modos de Falha do ativo:\n"
                  f"- Total cadastrado: {total_items} modo(s).\n"
                  f"- Gerados por IA: {auto_count} de {total_items} ({auto_percent}%).\n"
                  f"- Criados/editados manualmente: {manual_count} de {total_items} ({manual_percent}%).\n\n"
                  "Há modos de falha cadastrados no ativo, mas nenhum parece diretamente relacionado à pergunta atual. "
                  "Se a pergunta for sobre quantidade, percentual, origem IA/manual ou resumo geral, use obrigatoriamente os números acima."),
            matches=[], sources=[], found=True, total_items=total_items,
        )
    header = [
        "Estes dados vêm da tela 'Modos de Falha' do ativo e podem ter sido editados manualmente pelo usuário.",
        "Use este bloco como fonte prioritária quando a pergunta envolver falhas, causas, efeitos, recomendações, ações, quantidade, percentual ou origem dos modos de falha.",
        "Resumo real dos Modos de Falha do ativo:",
        f"- Total cadastrado: {total_items} modo(s).",
        f"- Gerados por IA: {auto_count} de {total_items} ({auto_percent}%).",
        f"- Criados/editados manualmente: {manual_count} de {total_items} ({manual_percent}%).",
        f"- Itens detalhados enviados neste contexto: {len(matches)}.",
        "Regra importante: quando a pergunta pedir quantidade ou porcentagem, responda usando o total real cadastrado, não apenas a quantidade de itens detalhados enviados no contexto.",
    ]
    blocks = [_format_failure_mode_match(match, index) for index, match in enumerate(matches, start=1)]
    text = "\n".join(header).strip() + "\n\n" + "\n\n".join(blocks).strip()
    return FailureModeChatContext(text=text, matches=matches, sources=build_failure_mode_sources(matches), found=True, total_items=total_items)
