"""
Testes de regressão para R15 — fallback de sobreposição de tokens em
_evidence_quote_is_grounded é permissivo demais.

Bug: 75% de sobreposição de tokens (sem considerar ordem ou frase contígua)
permite que uma citação inventada seja "validada" se suas palavras aparecem
dispersas na evidência.

Correto: exigir apenas containment literal (normalized_quote in normalized_evidence).
         Remover o fallback de token overlap.

B1 — Palavras presentes em evidência mas não como frase contígua → False.
     Antes da correção retorna True (75% overlap com palavras dispersas).

B2 — Citação que é realmente uma substring da evidência → True.
     Não deve regredir.

B3 — Citação vazia → False (invariante).

B4 — Fallback de token overlap deve ter sido removido do código-fonte.
"""
from __future__ import annotations

import inspect


def _get_func():
    from src.fault_tree.generator import _evidence_quote_is_grounded
    return _evidence_quote_is_grounded


# ── B1: palavras dispersas na evidência não devem ser aceitas como citação ────

def test_r15_b1_dispersed_tokens_not_grounded():
    """
    Palavras do quote existem na evidência, mas não como frase contígua.
    O fallback 75% as aceita incorretamente — a correção deve rejeitá-las.
    """
    fn = _get_func()
    evidence = (
        "bearing failure causes vibration noise heat generation motor unit alarm"
    )
    # Quote contém palavras todas presentes na evidência, mas não como frase
    quote = "bearing motor vibration heat"
    raw_item = {"evidence_quote": quote}
    result = fn(raw_item, evidence)
    assert result is False, (
        f"Quote com palavras dispersas NÃO deve ser aceita como grounded. "
        f"Recebeu: {result}"
    )


def test_r15_b1_scrambled_words_not_grounded():
    """
    Mesmas palavras da evidência, ordem diferente — não é substring literal.
    """
    fn = _get_func()
    evidence = "the pump operates at maximum pressure with nominal flow rate"
    quote = "nominal pressure pump maximum"   # palavras na evidência, ordem errada
    raw_item = {"evidence_quote": quote}
    result = fn(raw_item, evidence)
    assert result is False, (
        f"Quote com palavras em ordem errada NÃO deve ser grounded. Recebeu: {result}"
    )


# ── B2: substring literal deve continuar sendo aceita ────────────────────────

def test_r15_b2_exact_phrase_is_grounded():
    """
    Substring literal exata deve continuar retornando True.
    """
    fn = _get_func()
    evidence = "the pump operates at maximum pressure with nominal flow rate safely"
    quote = "maximum pressure with nominal flow"
    raw_item = {"evidence_quote": quote}
    assert fn(raw_item, evidence) is True


def test_r15_b2_short_phrase_grounded():
    """Frase curta presente literalmente na evidência → True."""
    fn = _get_func()
    evidence = "check oil level before start"
    quote = "oil level"
    raw_item = {"evidence_quote": quote}
    assert fn(raw_item, evidence) is True


# ── B3: invariantes de borda ─────────────────────────────────────────────────

def test_r15_b3_empty_quote_not_grounded():
    """Quote vazia → False."""
    fn = _get_func()
    assert fn({"evidence_quote": ""}, "qualquer evidência aqui") is False


def test_r15_b3_empty_evidence_not_grounded():
    """Evidência vazia → False."""
    fn = _get_func()
    assert fn({"evidence_quote": "alguma citação"}, "") is False


# ── B4: fallback de token overlap deve ter sido removido ─────────────────────

def test_r15_b4_token_overlap_fallback_removed():
    """
    O fallback de sobreposição de tokens (0.75) não deve mais existir
    na implementação de _evidence_quote_is_grounded.
    """
    fn = _get_func()
    source = inspect.getsource(fn)
    has_overlap_fallback = "0.75" in source or "75" in source
    assert not has_overlap_fallback, (
        "O fallback de 75% token overlap deve ter sido removido de _evidence_quote_is_grounded"
    )
