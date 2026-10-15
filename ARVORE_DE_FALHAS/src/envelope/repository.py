"""
Repositorio de envelhecimento — CRUD no PostgreSQL com org_id + tenant_id.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text

from src.storage.postgres_client import postgres_client


class EnvelopeRepository:
    def save_envelope(self, envelope) -> bool:
        try:
            payload = envelope.to_db_payload()
            return postgres_client.insert_envelope(payload)
        except Exception as e:
            logger.error(f"Erro ao salvar envelope: {e}")
            return False

    def get_by_id(
        self, envelope_id: str, org_id: str, tenant_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        return postgres_client.get_envelope_by_id(envelope_id, org_id, tenant_id)

    def get_by_bronze_id(
        self, bronze_id: str, org_id: str, tenant_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        try:
            with postgres_client.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT * FROM envelope_documents "
                        "WHERE bronze_id = :bronze_id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id "
                        "ORDER BY processed_at DESC LIMIT 1"
                    ),
                    {"bronze_id": bronze_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is not None:
                    data = dict(row._mapping)
                    if data.get("extracted_data"):
                        try:
                            data["extracted_data"] = json.loads(data["extracted_data"])
                        except Exception:
                            pass
                    return data
        except Exception as e:
            logger.error(f"Erro ao buscar envelope por bronze_id: {e}")
        return None

    def list_by_tenant(
        self,
        org_id: str,
        tenant_id: Optional[str],
        document_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return postgres_client.list_envelopes(org_id, tenant_id, document_type, limit)


envelope_repository = EnvelopeRepository()
