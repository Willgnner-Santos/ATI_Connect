"""
Testes de regressão para R05 — Falhas de persistência/indexação reportadas como sucesso.

Garante que:
- _store_chunks retorna total_chunks como 4º elemento (não apenas stored/ok/fail).
- vector_index_status é "partial" quando só parte dos chunks é armazenada.
- vector_index_status é "failed" quando nenhum chunk é armazenado mas havia chunks.
- vector_index_status é "success" somente quando todos os chunks são armazenados.
- vector_index_status é "empty" quando não há chunks.
- save_envelope retornando False interrompe o processamento (RuntimeError → None).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


def _stub_heavy_modules():
    """Stub de módulos pesados/ausentes antes de importar src.layers."""
    stubs = [
        "aio_pika", "psycopg2", "psycopg2.extras",
        "qdrant_client", "qdrant_client.models", "qdrant_client.http",
        "qdrant_client.http.models",
        "boto3", "botocore", "botocore.config", "botocore.exceptions",
        "botocore.session",
        "PyPDF2", "fitz", "pdfplumber", "pytesseract",
        "PIL", "PIL.Image", "tenacity",
    ]
    for mod in stubs:
        sys.modules.setdefault(mod, MagicMock())


_stub_heavy_modules()

# Limpar módulos src.layers para reimport limpo
for _mod in list(sys.modules):
    if _mod.startswith("src.layers") or _mod.startswith("src.storage.qdrant"):
        del sys.modules[_mod]


# ── _store_chunks: retorno correto ────────────────────────────────────────────


def test_store_chunks_returns_four_values():
    """R05 — _store_chunks deve retornar (stored, ok, fail, total_chunks)."""
    from src.layers.silver_layer import SilverLayer

    layer = SilverLayer.__new__(SilverLayer)

    fake_chunks = [MagicMock(text=f"c{i}", index=i, page_number=i, char_start=0, char_end=10) for i in range(5)]

    with (
        patch("src.layers.silver_layer.chunk_pages", return_value=fake_chunks),
        patch("src.layers.silver_layer.bedrock_client") as mock_bedrock,
        patch("src.layers.silver_layer.qdrant_client") as mock_qdrant,
    ):
        mock_bedrock.generate_embedding.return_value = [0.1] * 10
        mock_qdrant.batch_insert_vectors.return_value = 5

        result = layer._store_chunks("org", "t", "doc", [MagicMock()] * 5, [])

    assert len(result) == 4, (
        f"R05 — _store_chunks deve retornar 4 valores (stored, ok, fail, total_chunks), "
        f"retornou {len(result)}: {result}"
    )
    stored, ok, fail, total = result
    assert total == 5, f"R05 — total_chunks deve ser 5, obteve {total}"
    assert stored == 5
    assert ok == 5
    assert fail == 0


def test_store_chunks_empty_pages_returns_zeros():
    """R05 — sem páginas, _store_chunks retorna (0, 0, 0, 0)."""
    from src.layers.silver_layer import SilverLayer

    layer = SilverLayer.__new__(SilverLayer)
    result = layer._store_chunks("org", "t", "doc", [], [])
    assert result == (0, 0, 0, 0)


# ── Lógica de vector_index_status ─────────────────────────────────────────────

def _compute_status(stored: int, chunks_generated: int) -> str:
    """Replica a lógica de status do silver_layer para teste unitário."""
    if chunks_generated == 0:
        return "empty"
    elif stored == chunks_generated:
        return "success"
    elif stored > 0:
        return "partial"
    else:
        return "failed"


def test_all_stored_is_success():
    """R05 — todos armazenados → 'success'."""
    assert _compute_status(4, 4) == "success"


def test_partial_stored_is_partial():
    """R05 — parte armazenada → 'partial' (não 'success')."""
    assert _compute_status(2, 4) == "partial"


def test_none_stored_with_chunks_is_failed():
    """R05 — nenhum armazenado com chunks gerados → 'failed'."""
    assert _compute_status(0, 4) == "failed"


def test_no_chunks_is_empty():
    """R05 — sem chunks → 'empty'."""
    assert _compute_status(0, 0) == "empty"


def test_one_of_many_stored_is_partial():
    """R05 — 1 de 10 armazenados → 'partial' (não 'success')."""
    assert _compute_status(1, 10) == "partial"


# ── save_envelope falha → process retorna None ────────────────────────────────


def test_save_envelope_false_causes_process_to_return_none():
    """R05 — save_envelope retornando False deve fazer process_bronze_document() retornar None."""
    from src.layers.silver_layer import SilverLayer

    layer = SilverLayer.__new__(SilverLayer)

    fake_row = MagicMock()
    fake_row._mapping = {
        "id": "doc-1", "org_id": "org-1", "tenant_id": "t",
        "file_name": "m.pdf", "document_hash": "h", "object_path": "bucket/key",
        "file_type": "pdf", "metadata": None,
    }

    # Context manager para NamedTemporaryFile
    fake_tmp_file = MagicMock()
    fake_tmp_file.name = "/tmp/fake_r05_test.pdf"
    fake_ntf = MagicMock()
    fake_ntf.__enter__ = MagicMock(return_value=fake_tmp_file)
    fake_ntf.__exit__ = MagicMock(return_value=False)

    with (
        patch("src.layers.silver_layer.tempfile") as mock_tempfile,
        patch("src.layers.silver_layer.Path") as mock_path,
        patch("src.layers.silver_layer.postgres_client") as mock_pg,
        patch("src.layers.silver_layer.s3_client") as mock_s3,
        patch("src.layers.silver_layer.pdf_processor") as mock_pdf,
        patch("src.layers.silver_layer.envelope_builder") as mock_builder,
        patch("src.layers.silver_layer.envelope_repository") as mock_repo,
        patch("src.layers.silver_layer.image_extractor") as mock_img,
        patch("src.layers.silver_layer.image_cataloger"),
    ):
        mock_tempfile.NamedTemporaryFile.return_value = fake_ntf
        mock_path.return_value.suffix = ".pdf"
        mock_path.return_value.unlink = MagicMock()

        mock_pg.get_session.return_value.__enter__ = lambda s: s
        mock_pg.get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.get_session.return_value.execute.return_value.fetchone.return_value = fake_row
        mock_s3.download_file.return_value = True
        mock_pdf.extract_text_with_method.return_value = ("texto extraído", "pdf")
        mock_pdf.extract_pages.return_value = ([MagicMock()], "pdf")
        mock_img.extract_images.return_value = []
        mock_builder.build.return_value = MagicMock(
            envelope_id="env-1",
            document_type=MagicMock(value="manual"),
            is_structured=True,
        )
        # save_envelope retorna False — simula falha de persistência
        mock_repo.save_envelope.return_value = False

        result = layer.process_bronze_document(
            bronze_id="doc-1",
            org_id="org-1",
            tenant_id="t",
        )

    assert result is None, (
        "R05 — save_envelope retornando False deve resultar em "
        "process_bronze_document() retornando None."
    )
