"""
Testes de regressão para R21 — extract_pages faz decisão a nível de documento,
perdendo páginas escaneadas em PDFs híbridos.

Bug: se total_chars >= threshold, todas as páginas são retornadas via PyPDF2/pdfplumber.
     Páginas escaneadas (sem camada de texto) ficam com texto vazio.

Correto: após extração da camada de texto, aplicar OCR por página para páginas
         com menos de _THRESHOLD_OCR caracteres (prováveis páginas escaneadas).

B1 — PDF híbrido: páginas com texto mantêm texto, páginas esparsas recebem OCR.
B2 — PDF puramente textual: nenhum OCR é aplicado, método permanece "pypdf2".
B3 — _apply_per_page_ocr_fallback com todas páginas acima do threshold → unchanged.
B4 — _apply_per_page_ocr_fallback com página esparsa → OCR aplicado nessa página.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── B3/B4: _apply_per_page_ocr_fallback unit tests ───────────────────────────

def test_r21_b3_no_sparse_pages_returns_unchanged():
    """Todas as páginas com texto suficiente → nenhum OCR, retorna original."""
    from src.processors.pdf_processor import PDFProcessor

    proc = PDFProcessor()
    pages = [(1, "A" * 200), (2, "B" * 300), (3, "C" * 150)]

    result_pages, method = proc._apply_per_page_ocr_fallback("dummy.pdf", pages)

    assert result_pages == pages
    assert method == "pypdf2"


def test_r21_b4_sparse_page_gets_ocr():
    """Página esparsa (< _THRESHOLD_OCR chars) deve receber texto do OCR."""
    import sys
    from src.processors.pdf_processor import PDFProcessor

    proc = PDFProcessor()
    pages = [(1, "A" * 200), (2, ""), (3, "C" * 150)]  # página 2 esparsa

    fake_pil = [MagicMock(), MagicMock(), MagicMock()]
    ocr_text_for_page2 = "Texto extraído via OCR da página escaneada com conteúdo suficiente para validar"

    mock_pytesseract = MagicMock()
    mock_pytesseract.image_to_string.side_effect = (
        lambda img, lang, config: ocr_text_for_page2 if img is fake_pil[1] else "texto normal"
    )

    with patch("shutil.which", return_value="/usr/bin/tesseract"), \
         patch.object(proc, "_render_pages_as_pil", return_value=fake_pil), \
         patch.object(proc, "_preprocess_image", side_effect=lambda img: img), \
         patch.dict(sys.modules, {"pytesseract": mock_pytesseract}):

        result_pages, method = proc._apply_per_page_ocr_fallback("dummy.pdf", pages)

    assert result_pages[0] == (1, "A" * 200), "Página 1 não deve ser alterada"
    assert result_pages[2] == (3, "C" * 150), "Página 3 não deve ser alterada"
    assert result_pages[1][0] == 2, "Número da página 2 deve ser preservado"
    assert ocr_text_for_page2 in result_pages[1][1], (
        f"Página esparsa deve receber texto OCR. Recebeu: {result_pages[1][1]!r}"
    )
    assert method == "hybrid_ocr", (
        f"Método deve ser 'hybrid_ocr' quando OCR foi aplicado. Recebeu: {method!r}"
    )


def test_r21_b4_tesseract_unavailable_returns_unchanged():
    """Se Tesseract não disponível, retornar páginas sem OCR."""
    from src.processors.pdf_processor import PDFProcessor

    proc = PDFProcessor()
    pages = [(1, "A" * 200), (2, ""), (3, "C" * 150)]

    with patch("shutil.which", return_value=None):
        result_pages, method = proc._apply_per_page_ocr_fallback("dummy.pdf", pages)

    assert result_pages == pages
    assert method == "pypdf2"


# ── B1: extract_pages com PDF híbrido ────────────────────────────────────────

def test_r21_b1_hybrid_pdf_scanned_pages_get_ocr():
    """
    PDF híbrido: PyPDF2 extrai texto para páginas com camada de texto,
    mas páginas escaneadas ficam com string vazia.
    extract_pages deve retornar OCR para as páginas esparsas.
    """
    from src.processors.pdf_processor import PDFProcessor

    proc = PDFProcessor()

    # Simular PyPDF2: 3 páginas, página 2 é scaneada (vazia)
    pypdf2_pages = [(1, "Conteúdo técnico longo com 500 chars " * 14),
                    (2, ""),       # página escaneada
                    (3, "Outro conteúdo longo " * 25)]

    ocr_result = "Texto OCR da página escaneada com conteúdo técnico longo suficiente para passar o threshold"
    fake_pil = [MagicMock(), MagicMock(), MagicMock()]

    def fake_ocr(img, lang, config):
        return ocr_result if img is fake_pil[1] else ""

    import sys
    mock_pytesseract = MagicMock()
    mock_pytesseract.image_to_string.side_effect = fake_ocr

    with patch.object(proc, "_extract_pages_with_pypdf2", return_value=pypdf2_pages), \
         patch.object(proc, "_is_glyph_encoded", return_value=False), \
         patch.object(proc, "_fix_spaced_text", side_effect=lambda t: t), \
         patch("shutil.which", return_value="/usr/bin/tesseract"), \
         patch.object(proc, "_render_pages_as_pil", return_value=fake_pil), \
         patch.object(proc, "_preprocess_image", side_effect=lambda img: img), \
         patch.dict(sys.modules, {"pytesseract": mock_pytesseract}):

        result_pages, method = proc.extract_pages("dummy.pdf")

    assert len(result_pages) == 3
    # Páginas com texto original preservado
    assert "Conteúdo técnico" in result_pages[0][1]
    assert "Outro conteúdo" in result_pages[2][1]
    # Página escaneada agora tem texto OCR
    assert ocr_result in result_pages[1][1], (
        f"Página escaneada deve ter texto OCR. Recebeu: {result_pages[1][1]!r}"
    )
    assert method in ("hybrid_ocr", "pypdf2"), f"Método inesperado: {method}"


# ── B2: PDF puramente textual — sem OCR ──────────────────────────────────────

def test_r21_b2_text_only_pdf_no_ocr():
    """
    PDF com todas as páginas tendo texto suficiente → OCR não deve ser chamado.
    """
    from src.processors.pdf_processor import PDFProcessor

    proc = PDFProcessor()

    pypdf2_pages = [(1, "Texto longo " * 50),
                    (2, "Mais texto " * 60),
                    (3, "Ainda mais " * 40)]

    with patch.object(proc, "_extract_pages_with_pypdf2", return_value=pypdf2_pages), \
         patch.object(proc, "_is_glyph_encoded", return_value=False), \
         patch.object(proc, "_fix_spaced_text", side_effect=lambda t: t), \
         patch("shutil.which", return_value="/usr/bin/tesseract") as mock_which, \
         patch.object(proc, "_render_pages_as_pil") as mock_render:

        result_pages, method = proc.extract_pages("dummy.pdf")

    # Se nenhuma página é esparsa, _render_pages_as_pil não deve ser chamado
    mock_render.assert_not_called()
    assert method in ("pypdf2", "hybrid_ocr")
    assert len(result_pages) == 3
