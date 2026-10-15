import json
import uuid

import pytest
from pydantic import ValidationError

from src.messaging.event_schemas import (
    DocumentProcessedEvent,
    DocumentFailedEvent,
    IngestRequestedEvent,
)


def test_document_processed_routing_key():
    ev = DocumentProcessedEvent(
        event_id=str(uuid.uuid4()),
        org_id="org1",
        tenant_id="tenant1",
        bronze_id="b1",
        file_name="manual.pdf",
        file_type="pdf",
        object_path="bucket/org1/tenant1/manual.pdf",
    )
    assert ev.routing_key() == "document.processed"
    payload = json.loads(ev.to_json())
    assert payload["version"] == 3, (
        "DocumentProcessedEvent deve ser versão 3 após adição de vector_index_status (R05)."
    )
    assert payload["object_path"] == "bucket/org1/tenant1/manual.pdf"
    assert "vector_index_status" in payload, (
        "Schema v3 deve incluir vector_index_status para distinguir completo de parcial."
    )
    assert "minio_path" not in payload


def test_document_processed_rejects_legacy_storage_field():
    with pytest.raises(ValidationError):
        DocumentProcessedEvent(
            event_id=str(uuid.uuid4()),
            org_id="org1",
            tenant_id="tenant1",
            bronze_id="b1",
            file_name="manual.pdf",
            file_type="pdf",
            minio_path="bucket/manual.pdf",
        )


def test_document_failed_routing_key():
    ev = DocumentFailedEvent(
        event_id=str(uuid.uuid4()),
        org_id="org1",
        tenant_id="tenant1",
        stage="bronze",
        errors=["x"],
    )
    assert ev.routing_key() == "document.failed"


def test_ingest_requested_to_json_roundtrip():
    ev = IngestRequestedEvent(
        event_id=str(uuid.uuid4()),
        org_id="org1",
        tenant_id="tenant1",
        file_name="manual.pdf",
        content_base64="aGVsbG8=",
    )
    payload = json.loads(ev.to_json())
    assert payload["event_type"] == "ingest.requested"
    assert payload["org_id"] == "org1"
    assert payload["file_name"] == "manual.pdf"
