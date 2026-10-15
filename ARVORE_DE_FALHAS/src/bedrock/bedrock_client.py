"""
Cliente AWS Bedrock — Claude (texto + visao) e Titan (embeddings).

Fornece chamadas estruturadas via tool_use para extrair informacoes
de manuais tecnicos industriais.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

import boto3
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings

_RETRYABLE = (Exception,)


class BedrockClient:
    """Wrapper para Amazon Bedrock (Claude + Titan)."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=settings.BEDROCK_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            )
        return self._client

    # ── Invocacao generica ────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _invoke_model_raw(self, body: Dict[str, Any]) -> Dict[str, Any]:
        response = self._get_client().invoke_model(
            modelId=settings.MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body).encode("utf-8"),
        )
        return json.loads(response["body"].read().decode("utf-8"))

    def invoke_model(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Invoca Claude com texto simples, expondo uma API publica para servicos."""
        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt
        result = self._invoke_model_raw(body)
        return "".join(
            block.get("text", "") for block in result.get("content", [])
            if block.get("type") == "text"
        )

    def rerank_documents(self, query: str, documents: List[Dict[str, Any]]) -> List[float]:
        """Retorna escores 0..1 alinhados à ordem dos documentos recebidos."""
        if not documents:
            return []
        tool = {
            "name": "rerank_documents",
            "description": "Avalia a relevancia de cada trecho para a consulta.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "scores": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["scores"],
            },
        }
        excerpts = [str(document.get("text_excerpt", ""))[:2000] for document in documents]
        prompt = (
            f"Consulta: {query}\n\nTrechos (na ordem):\n"
            + "\n".join(f"{index}: {excerpt}" for index, excerpt in enumerate(excerpts))
            + "\nRetorne um score de relevancia entre 0 e 1 para cada trecho, na mesma ordem."
        )
        try:
            result = self._invoke_with_tool(prompt, tool["name"], tool)
            scores = result.get("scores", []) if result else []
            if len(scores) != len(documents):
                return []
            return [max(0.0, min(1.0, float(score))) for score in scores]
        except Exception as exc:
            logger.warning(f"Falha no reranking LLM: {exc}")
            return []

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _invoke_embedding_raw(self, text: str) -> List[float]:
        body = json.dumps({
            "inputText": text[:40000],
            "dimensions": settings.EMBEDDING_DIMENSION,
            "normalize": True,
        }).encode("utf-8")
        response = self._get_client().invoke_model(
            modelId=settings.EMBEDDING_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        result = json.loads(response["body"].read().decode("utf-8"))
        embedding = result.get("embedding", [])
        if len(embedding) != settings.EMBEDDING_DIMENSION:
            logger.warning(
                f"Embedding dimensao inesperada: {len(embedding)} "
                f"(esperado {settings.EMBEDDING_DIMENSION})"
            )
        return embedding

    # ── Tool-use forcing ──────────────────────────────────────────

    def _invoke_with_tool(
        self,
        prompt: str,
        tool_name: str,
        tool_schema: Dict[str, Any],
        temperature: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": tool_name,
                    "description": tool_schema.get("description", ""),
                    "input_schema": tool_schema.get("input_schema", tool_schema),
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        result = self._invoke_model_raw(body)
        content = result.get("content", [])
        for block in content:
            if block.get("type") == "tool_use":
                return block.get("input")
        for block in content:
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except Exception:
                    pass
        return None

    def _invoke_with_tool_and_messages(
        self,
        messages: List[Dict[str, Any]],
        tool_name: str,
        tool_schema: Dict[str, Any],
        temperature: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": messages,
            "tools": [
                {
                    "name": tool_name,
                    "description": tool_schema.get("description", ""),
                    "input_schema": tool_schema.get("input_schema", tool_schema),
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        result = self._invoke_model_raw(body)
        content = result.get("content", [])
        for block in content:
            if block.get("type") == "tool_use":
                return block.get("input")
        return None

    # ── Embedding ─────────────────────────────────────────────────

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        try:
            return self._invoke_embedding_raw(text)
        except Exception as e:
            logger.error(f"Erro ao gerar embedding: {e}")
            return None

    # ── OCR via Vision ────────────────────────────────────────────

    def extract_text_from_image_pages(
        self, image_pages: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        content_blocks = []
        for page in image_pages:
            img_bytes = page.get("image_bytes")
            page_num = page.get("page_number", "?")
            if img_bytes:
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                content_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": self._detect_media_type(img_bytes), "data": b64},
                })
                content_blocks.append({
                    "type": "text",
                    "text": f"[Pagina {page_num}] Transcreva todo o texto visivel nesta pagina.",
                })
        if not content_blocks:
            return None
        tool = {
            "name": "transcribe_image_pages",
            "description": "Transcreve texto de paginas escaneadas",
            "input_schema": {
                "type": "object",
                "properties": {
                    "texto_extraido": {"type": "string"},
                    "qualidade_scan": {"type": "string"},
                    "idioma_detectado": {"type": "string"},
                },
                "required": ["texto_extraido"],
            },
        }
        messages = [{"role": "user", "content": content_blocks}]
        return self._invoke_with_tool_and_messages(
            messages, tool["name"], tool, temperature=0.0
        )

    # ── Catalogacao de imagem (VLM) ───────────────────────────────

    _TOOL_CATALOG_IMAGE = {
        "name": "catalog_image",
        "description": "Analisa uma imagem tecnica e extrai metadados estruturados",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Descricao tecnica detalhada (minimo 50 palavras)",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "desenho_tecnico", "diagrama", "esquema_eletrico",
                        "fotografia", "grafico", "tabela",
                        "esquema_hidraulico", "outro",
                    ],
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "detected_objects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "object_name": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                    },
                },
                "technical_context": {
                    "type": "object",
                    "properties": {
                        "equipment_type": {"type": "string"},
                        "maintenance_relevance": {"type": "string"},
                        "failure_modes": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "text_content": {"type": "array", "items": {"type": "string"}},
                "quality_indicators": {
                    "type": "object",
                    "properties": {
                        "image_clarity": {"type": "string"},
                    },
                },
                "confidence_score": {"type": "number"},
            },
            "required": ["description", "category", "confidence_score"],
        },
    }

    @staticmethod
    def _detect_media_type(image_bytes: bytes) -> str:
        if image_bytes[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if image_bytes[:4] == b"\x89PNG":
            return "image/png"
        if image_bytes[:4] in (b"GIF8", b"GIF9"):
            return "image/gif"
        if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            return "image/webp"
        return "image/png"

    def catalog_image(
        self, image_bytes: bytes, context: str = ""
    ) -> Optional[Dict[str, Any]]:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        media_type = self._detect_media_type(image_bytes)
        content_blocks = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {
                "type": "text",
                "text": (
                    "Analise esta imagem de um manual tecnico industrial. "
                    "Identifique o tipo de diagrama/desenho, componentes visiveis, "
                    "relevancia para manutencao e modos de falha associados. "
                    f"Contexto adicional: {context}"
                ),
            },
        ]
        messages = [{"role": "user", "content": content_blocks}]
        return self._invoke_with_tool_and_messages(
            messages,
            self._TOOL_CATALOG_IMAGE["name"],
            self._TOOL_CATALOG_IMAGE,
            temperature=0.0,
        )

    # ── Extracao estruturada de manual tecnico ────────────────────

    def extract_manual_tecnico(
        self, raw_text: str, tool_schema: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        prompt = (
            "Analise o texto abaixo de um manual tecnico industrial e extraia "
            "as informacoes estruturadas solicitadas.\n\n"
            f"TEXTO:\n{raw_text[:12000]}"
        )
        return self._invoke_with_tool(
            prompt,
            tool_schema.get("name", "extract_manual_tecnico"),
            tool_schema,
            temperature=0.0,
        )


bedrock_client = BedrockClient()
