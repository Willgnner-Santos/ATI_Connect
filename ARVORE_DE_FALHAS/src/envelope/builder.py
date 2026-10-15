"""
Orquestrador de extracao estruturada.
Classifica o documento e rota para o extrator correto.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from .classifier import classifier_service, ClassificationResult
from .envelope import DocumentType, EnvelopeDocument
from .extractors import manual_tecnico_extractor, ExtractionResult


@dataclass
class BuildContext:
    org_id: str
    tenant_id: Optional[str]
    bronze_id: str
    document_hash: str
    file_name: str


class EnvelopeBuilder:
    def __init__(self):
        self.classifier = classifier_service

    def build(self, ctx: BuildContext, raw_text: str) -> EnvelopeDocument:
        classification = self.classifier.classify(raw_text)

        if classification is None:
            logger.warning("Classificacao falhou — retornando raw_only")
            return self._raw_only_envelope(ctx, DocumentType.UNKNOWN, 0.0, raw_text)

        doc_type = classification.document_type
        confidence = classification.confidence

        if doc_type == DocumentType.UNKNOWN:
            return self._raw_only_envelope(ctx, doc_type, confidence, raw_text)

        extraction = manual_tecnico_extractor.extract(raw_text, doc_type)
        if extraction is None:
            logger.warning(f"Extracao falhou para {doc_type.value} — raw_only")
            return self._raw_only_envelope(ctx, doc_type, confidence, raw_text)

        return EnvelopeDocument(
            envelope_id=str(uuid.uuid4()),
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            bronze_id=ctx.bronze_id,
            document_type=doc_type,
            document_hash=ctx.document_hash,
            file_name=ctx.file_name,
            is_structured=True,
            confidence_score=extraction.confidence,
            extracted_data=extraction.data,
            raw_text=raw_text,
        )

    def _raw_only_envelope(
        self,
        ctx: BuildContext,
        doc_type: DocumentType,
        confidence: float,
        raw_text: str,
    ) -> EnvelopeDocument:
        return EnvelopeDocument(
            envelope_id=str(uuid.uuid4()),
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            bronze_id=ctx.bronze_id,
            document_type=doc_type,
            document_hash=ctx.document_hash,
            file_name=ctx.file_name,
            is_structured=False,
            confidence_score=confidence,
            extracted_data=None,
            raw_text=raw_text,
        )


envelope_builder = EnvelopeBuilder()
