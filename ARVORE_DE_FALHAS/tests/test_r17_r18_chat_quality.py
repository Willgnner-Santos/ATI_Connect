"""
Testes de regressão para R17 e R18 — qualidade do pipeline de chat.

R18 — parece_saudacao false-positive:
  startswith("bom") / startswith("boa") sem word boundary captura "bomba não liga",
  "boavida", etc. como saudações quando não são.

  Correto: exigir que o prefixo seja seguido de espaço ou seja toda a mensagem.

R17 — _chat_claude engole erros e retorna "":
  Quando o bedrock falha, _chat_claude loga e retorna "", resultando em resposta
  vazia com HTTP 200 — o cliente não sabe que houve falha.

  Correto: _chat_claude deve levantar LLMUnavailableError; o pipeline deve retornar 503.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch


# ══════════════════════════════════════════════════════════════════
# R18 — word boundary no classificador de saudação
# ══════════════════════════════════════════════════════════════════

def test_r18_b1_bomba_nao_liga_is_not_greeting():
    """'bomba não liga' começa com 'bom' mas não é saudação."""
    from src.chat.pipeline import parece_saudacao
    assert parece_saudacao("bomba não liga") is False, (
        "'bomba não liga' não deve ser classificado como saudação"
    )


def test_r18_b1_bom_dia_is_greeting():
    """'bom dia' deve continuar sendo classificado como saudação."""
    from src.chat.pipeline import parece_saudacao
    assert parece_saudacao("bom dia") is True


def test_r18_b1_boa_tarde_is_greeting():
    """'boa tarde' deve continuar sendo classificado como saudação."""
    from src.chat.pipeline import parece_saudacao
    assert parece_saudacao("boa tarde") is True


def test_r18_b1_boavida_not_greeting():
    """'boavida' começa com 'boa' mas não é saudação (sem espaço)."""
    from src.chat.pipeline import parece_saudacao
    assert parece_saudacao("boavida") is False, (
        "'boavida' não deve ser classificado como saudação"
    )


def test_r18_b2_boa_noite_is_greeting():
    """'boa noite' deve continuar sendo classificado como saudação."""
    from src.chat.pipeline import parece_saudacao
    assert parece_saudacao("boa noite") is True


def test_r18_b2_olá_tudo_bem_is_greeting():
    """'olá, tudo bem?' (curto) deve ser saudação."""
    from src.chat.pipeline import parece_saudacao
    assert parece_saudacao("olá tudo bem") is True


def test_r18_b2_bomba_hidraulica_falhou_not_greeting():
    """Frases técnicas que começam com 'bom'/'boa' não são saudações."""
    from src.chat.pipeline import parece_saudacao
    assert parece_saudacao("bomba hidráulica falhou") is False
    assert parece_saudacao("boa, mas o sensor falhou") is False  # "boa," com vírgula


# ══════════════════════════════════════════════════════════════════
# R17 — _chat_claude deve levantar exceção, não retornar ""
# ══════════════════════════════════════════════════════════════════

def test_r17_b1_llm_error_raises_not_returns_empty():
    """
    Antes: exceção do bedrock era capturada e _chat_claude retornava "".
    Após: deve levantar LLMUnavailableError (ou similar) para que o caller possa
    retornar 503 em vez de resposta vazia com 200.
    """
    from src.chat.pipeline import _chat_claude

    with patch("src.chat.pipeline.bedrock_client") as mock_bedrock:
        mock_bedrock._invoke_model_raw.side_effect = RuntimeError("connection refused")
        try:
            result = _chat_claude(messages=[{"role": "user", "content": [{"type": "text", "text": "oi"}]}])
        except Exception:
            return  # levantou exceção — correto
        # Se chegou aqui, retornou "" silenciosamente — errado
        assert result != "", (
            "_chat_claude deve levantar exceção quando bedrock falha, não retornar ''"
        )


def test_r17_b1_llmunvailable_error_exists():
    """LLMUnavailableError deve existir em src.chat.pipeline."""
    import src.chat.pipeline as pipeline
    assert hasattr(pipeline, "LLMUnavailableError"), (
        "src.chat.pipeline deve definir LLMUnavailableError"
    )


def test_r17_b2_pipeline_returns_503_on_llm_failure():
    """
    Quando _chat_claude levantar LLMUnavailableError, o pipeline deve
    retornar HTTPException com status_code=503 (não 200 nem 500).
    """
    from fastapi import HTTPException
    import src.chat.pipeline as pipeline

    # Precisa de LLMUnavailableError para este teste fazer sentido
    if not hasattr(pipeline, "LLMUnavailableError"):
        raise AssertionError("LLMUnavailableError deve existir em pipeline")

    mock_store = MagicMock()
    mock_store.read_last.return_value = []
    mock_store.count.return_value = 0
    mock_summ = MagicMock()
    mock_summ.get.return_value = ("", 0)

    with patch.object(pipeline, "_chat_claude",
                      side_effect=pipeline.LLMUnavailableError("timeout")):
        with patch.object(pipeline, "IngestAdapter") as mock_ia, \
             patch.object(pipeline, "build_failure_mode_chat_context") as mock_fm:

            mock_fm.return_value = MagicMock(found=False, text="", sources=[])
            adapter_instance = MagicMock()
            adapter_instance.get_document_chunks.return_value = {}
            mock_ia.return_value = adapter_instance

            try:
                pipeline.run_chat_pipeline(
                    message="minha pergunta",
                    equipment_id="equip-1",
                    org_id="org-1",
                    tenant_id="tenant-1",
                    chat_store=mock_store,
                    summary_store=mock_summ,
                )
            except HTTPException as exc:
                assert exc.status_code == 503, (
                    f"Esperado 503 para falha do LLM, mas recebeu {exc.status_code}"
                )
            except Exception as exc:
                raise AssertionError(f"Esperado HTTPException 503, mas recebeu {type(exc).__name__}: {exc}")
            else:
                raise AssertionError("Esperado HTTPException 503, mas nenhuma exceção foi levantada")
