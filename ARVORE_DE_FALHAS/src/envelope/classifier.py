"""
Classificador de tipo de documento.
Usa heuristica de keywords para manuais tecnicos (sem chamada LLM).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.bedrock.bedrock_client import bedrock_client
from .envelope import DocumentType

MIN_TEXT_LENGTH = 20

_MANUAL_KEYWORDS = [
    "manual", "service", "maintenance", "installation", "parts list",
    "operacao", "manutencao", "instalacao", "lista de pecas",
    "compressor", "motor", "pump", "gearmotor", "gear", "compressor",
    "hydraulic", "hidraulico", "pneumatic", "pneumatico",
    "troubleshooting", "diagnostico", "alarm", "alarme",
    "specification", "especificacao", "technical", "tecnico",
    "safety", "seguranca", "warning", "aviso",
    "torque", "rpm", "voltage", "tensao", "pressure", "pressao",
    "lubrication", "lubrificacao", "filter", "filtro",
    "bearing", "rolamento", "seal", "vedacao",
    "cnc", "laser", "cutting", "corte",
]


@dataclass
class ClassificationResult:
    document_type: DocumentType
    confidence: float
    reasoning: str = ""


class ClassifierService:
    def classify(self, raw_text: str) -> Optional[ClassificationResult]:
        if not raw_text or len(raw_text.strip()) < MIN_TEXT_LENGTH:
            return ClassificationResult(document_type=DocumentType.UNKNOWN, confidence=0.0, reasoning="Texto insuficiente")
        llm_result = self._classify_with_llm(raw_text)
        if llm_result is not None:
            return llm_result
        return self._classify_heuristically(raw_text, "Fallback por palavras-chave")

    def _classify_with_llm(self, raw_text: str) -> Optional[ClassificationResult]:
        tool = {
            "name": "classify_document_type",
            "description": "Classifica documentos industriais sem inventar categorias.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "document_type": {"type": "string", "enum": ["manual_tecnico", "unknown"]},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                },
                "required": ["document_type", "confidence", "reasoning"],
            },
        }
        try:
            result = bedrock_client._invoke_with_tool(
                "Classifique o texto a seguir. Um manual tecnico contem instrucoes, "
                "especificacoes, manutencao, operacao ou diagnostico de equipamento industrial.\n\n"
                f"TEXTO:\n{raw_text[:12000]}",
                tool["name"], tool,
            )
            if not result:
                return None
            document_type = DocumentType(result.get("document_type", "unknown"))
            confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
            return ClassificationResult(document_type, confidence, str(result.get("reasoning", "")))
        except Exception as exc:
            logger.warning(f"Classificacao LLM indisponivel: {exc}")
            return None

    def _classify_heuristically(self, raw_text: str, reasoning: str) -> ClassificationResult:
        text_lower = raw_text.lower()
        hits = sum(1 for kw in _MANUAL_KEYWORDS if kw.lower() in text_lower)
        if hits >= 3:
            logger.info(f"Classificacao heuristica: manual_tecnico ({hits} keywords)")
            return ClassificationResult(
                document_type=DocumentType.MANUAL_TECNICO,
                confidence=min(0.5 + hits * 0.05, 0.85),
                reasoning=f"{reasoning}: {hits} palavras-chave encontradas",
            )
        return ClassificationResult(document_type=DocumentType.UNKNOWN, confidence=0.0, reasoning=reasoning)


classifier_service = ClassifierService()
