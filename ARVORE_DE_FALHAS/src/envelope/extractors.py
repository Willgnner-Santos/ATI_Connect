"""
Extrator de dados estruturados — apenas ManualTecnicoExtractor.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from dataclasses import dataclass

from loguru import logger

from src.bedrock.bedrock_client import bedrock_client
from .envelope import DocumentType
from .manual_tecnico import ManualTecnicoData

MAX_PROMPT_CHARS = 12000


@dataclass
class ExtractionResult:
    data: Dict[str, Any]
    confidence: float


def _build_manual_tecnico_tool() -> Dict[str, Any]:
    return {
        "name": "extract_manual_tecnico",
        "description": "Extrai dados estruturados de um manual tecnico industrial",
        "input_schema": {
            "type": "object",
            "properties": ManualTecnicoData.tool_schema_properties(),
            "required": ["tipo_equipamento"],
        },
    }


class ManualTecnicoExtractor:
    def extract(
        self, raw_text: str, document_type: Optional[DocumentType] = None
    ) -> Optional[ExtractionResult]:
        try:
            prompt = (
                "Analise o texto abaixo de um manual tecnico industrial e extraia "
                "as informacoes estruturadas solicitadas.\n\n"
                f"TEXTO:\n{raw_text[:MAX_PROMPT_CHARS]}"
            )
            tool = _build_manual_tecnico_tool()
            response = bedrock_client.extract_manual_tecnico(raw_text, tool)
            if not response:
                return None
            confidence = float(response.pop("confidence", 0.5))
            cleaned = {k: v for k, v in response.items() if v is not None and v != ""}
            validated = ManualTecnicoData(**cleaned)
            return ExtractionResult(data=validated.model_dump(), confidence=confidence)
        except Exception as e:
            logger.error(f"Erro ao extrair manual tecnico: {e}")
            return None


manual_tecnico_extractor = ManualTecnicoExtractor()
