from src.envelope.manual_tecnico import ManualTecnicoData
from src.envelope.envelope import DocumentType, EnvelopeDocument


def test_manual_tecnico_tool_schema():
    props = ManualTecnicoData.tool_schema_properties()
    assert "nome_equipamento" in props
    assert "advertencias_seguranca" in props
    assert len(props) >= 17


def test_envelope_to_db_payload():
    env = EnvelopeDocument(
        envelope_id="e1",
        org_id="org1",
        tenant_id="tenant1",
        bronze_id="b1",
        document_type=DocumentType.MANUAL_TECNICO,
        document_hash="abc",
        file_name="manual.pdf",
        is_structured=True,
        confidence_score=0.9,
        extracted_data={"fabricante": "X"},
        raw_text="some raw text",
    )
    payload = env.to_db_payload()
    assert payload["id"] == "e1"
    assert payload["org_id"] == "org1"
    assert payload["tenant_id"] == "tenant1"
    assert payload["document_type"] == "manual_tecnico"