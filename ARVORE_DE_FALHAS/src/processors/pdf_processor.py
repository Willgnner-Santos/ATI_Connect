"""
Processador de arquivos PDF com extração em cascata:
  S1: PyPDF2
  S2: pdfplumber
  S3: Tesseract OCR
  S4: Claude Vision
"""
import shutil
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import PyPDF2
from loguru import logger

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


class PDFProcessor:
    _THRESHOLD_TEXT = 100
    _THRESHOLD_OCR = 50
    _RENDER_DPI = 150
    _MAX_VISION_PAGES = 20

    def extract_text(self, file_path: str) -> Optional[str]:
        text, _ = self.extract_text_with_method(file_path)
        return text

    def extract_text_with_method(self, file_path: str) -> Tuple[Optional[str], str]:
        text = self._extract_with_pypdf2(file_path)
        if text and len(text.strip()) >= self._THRESHOLD_TEXT:
            if self._is_glyph_encoded(text):
                logger.warning(
                    f"S1/PyPDF2 retornou glifos codificados — pulando para Vision: {Path(file_path).name}"
                )
            else:
                text = self._fix_spaced_text(text)
                logger.info(f"S1/PyPDF2: {len(text)} chars — {Path(file_path).name}")
                return text, "pypdf2"

        text = self._extract_with_pdfplumber(file_path)
        if text and len(text.strip()) >= self._THRESHOLD_TEXT:
            if self._is_glyph_encoded(text):
                logger.warning(
                    f"S2/pdfplumber retornou glifos — pulando para Vision: {Path(file_path).name}"
                )
            else:
                text = self._fix_spaced_text(text)
                logger.info(f"S2/pdfplumber: {len(text)} chars — {Path(file_path).name}")
                return text, "pdfplumber"

        text = self._extract_with_tesseract(file_path)
        if text and len(text.strip()) >= self._THRESHOLD_OCR:
            logger.info(f"S3/Tesseract: {len(text)} chars — {Path(file_path).name}")
            return text, "ocr_tesseract"

        text = self._extract_with_claude_vision(file_path)
        if text and len(text.strip()) >= self._THRESHOLD_OCR:
            logger.info(f"S4/Vision: {len(text)} chars — {Path(file_path).name}")
            return text, "claude_vision"

        logger.warning(f"Todas as estratégias falharam: {Path(file_path).name}")
        return text or None, "failed"

    def _apply_per_page_ocr_fallback(
        self, file_path: str, pages: List[Tuple[int, str]]
    ) -> Tuple[List[Tuple[int, str]], str]:
        """For pages below _THRESHOLD_OCR, attempt Tesseract OCR (hybrid PDF support)."""
        if shutil.which("tesseract") is None:
            return pages, "pypdf2"
        sparse = [i for i, (_, t) in enumerate(pages) if len(t.strip()) < self._THRESHOLD_OCR]
        if not sparse:
            return pages, "pypdf2"
        try:
            pil_pages = self._render_pages_as_pil(file_path)
        except Exception as e:
            logger.debug(f"Renderização para OCR por página falhou: {e}")
            return pages, "pypdf2"
        try:
            import pytesseract
        except ImportError:
            return pages, "pypdf2"
        result = list(pages)
        ocr_applied = False
        for i in sparse:
            if i >= len(pil_pages):
                continue
            try:
                preprocessed = self._preprocess_image(pil_pages[i])
                ocr_text = pytesseract.image_to_string(
                    preprocessed, lang="por", config="--oem 3 --psm 1"
                ) or ""
                if len(ocr_text.strip()) >= self._THRESHOLD_OCR:
                    result[i] = (result[i][0], ocr_text)
                    ocr_applied = True
            except Exception as e:
                logger.debug(f"OCR por página {i + 1} falhou: {e}")
        return result, "hybrid_ocr" if ocr_applied else "pypdf2"

    def extract_pages(self, file_path: str) -> Tuple[List[Tuple[int, str]], str]:
        pages = self._extract_pages_with_pypdf2(file_path)
        total_chars = sum(len(t) for _, t in pages)
        if total_chars >= self._THRESHOLD_TEXT:
            sample = " ".join(t for _, t in pages[:3])
            if self._is_glyph_encoded(sample):
                logger.warning("extract_pages S1/PyPDF2 retornou glifos — pulando")
            else:
                pages = [(n, self._fix_spaced_text(t)) for n, t in pages]
                pages, method = self._apply_per_page_ocr_fallback(file_path, pages)
                logger.info(f"extract_pages S1/{method}: {len(pages)} páginas")
                return pages, method

        pages = self._extract_pages_with_pdfplumber(file_path)
        total_chars = sum(len(t) for _, t in pages)
        if total_chars >= self._THRESHOLD_TEXT:
            sample = " ".join(t for _, t in pages[:3])
            if self._is_glyph_encoded(sample):
                logger.warning("extract_pages S2/pdfplumber retornou glifos — pulando")
            else:
                pages = [(n, self._fix_spaced_text(t)) for n, t in pages]
                pages, method = self._apply_per_page_ocr_fallback(file_path, pages)
                logger.info(f"extract_pages S2/{method}: {len(pages)} páginas")
                return pages, method

        pages = self._extract_pages_with_tesseract(file_path)
        total_chars = sum(len(t) for _, t in pages)
        if total_chars >= self._THRESHOLD_OCR:
            logger.info(f"extract_pages S3/Tesseract: {len(pages)} páginas, {total_chars} chars")
            return pages, "ocr_tesseract"

        vision_text = self._extract_with_claude_vision(file_path)
        if vision_text and len(vision_text.strip()) >= self._THRESHOLD_OCR:
            logger.info(f"extract_pages S4/Vision (sem número de página): {len(vision_text)} chars")
            return [(1, vision_text)], "claude_vision"

        logger.warning(f"extract_pages: todas as estratégias falharam — {Path(file_path).name}")
        return [], "failed"

    def _extract_with_pypdf2(self, file_path: str) -> Optional[str]:
        try:
            parts = []
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            return "\n\n".join(parts) if parts else None
        except Exception as e:
            logger.debug(f"PyPDF2 falhou ({Path(file_path).name}): {e}")
            return None

    def _extract_pages_with_pypdf2(self, file_path: str) -> List[Tuple[int, str]]:
        try:
            pages: List[Tuple[int, str]] = []
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for idx, page in enumerate(reader.pages, start=1):
                    t = page.extract_text() or ""
                    pages.append((idx, t))
            return pages
        except Exception as e:
            logger.debug(f"PyPDF2 por página falhou ({Path(file_path).name}): {e}")
            return []

    def _extract_with_pdfplumber(self, file_path: str) -> Optional[str]:
        try:
            import pdfplumber
            parts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            return "\n\n".join(parts) if parts else None
        except Exception as e:
            logger.debug(f"pdfplumber falhou ({Path(file_path).name}): {e}")
            return None

    def _extract_pages_with_pdfplumber(self, file_path: str) -> List[Tuple[int, str]]:
        try:
            import pdfplumber
            pages: List[Tuple[int, str]] = []
            with pdfplumber.open(file_path) as pdf:
                for idx, page in enumerate(pdf.pages, start=1):
                    t = page.extract_text() or ""
                    pages.append((idx, t))
            return pages
        except Exception as e:
            logger.debug(f"pdfplumber por página falhou ({Path(file_path).name}): {e}")
            return []

    def _extract_with_tesseract(self, file_path: str) -> Optional[str]:
        if shutil.which("tesseract") is None:
            logger.debug("Tesseract não encontrado — S3 pulado")
            return None
        try:
            import pytesseract
            pil_pages = self._render_pages_as_pil(file_path)
            if not pil_pages:
                return None
            parts = []
            for img in pil_pages:
                preprocessed = self._preprocess_image(img)
                t = pytesseract.image_to_string(preprocessed, lang="por", config="--oem 3 --psm 1")
                if t:
                    parts.append(t)
            return "\n\n".join(parts) if parts else None
        except Exception as e:
            logger.debug(f"Tesseract falhou ({Path(file_path).name}): {e}")
            return None

    def _extract_pages_with_tesseract(self, file_path: str) -> List[Tuple[int, str]]:
        if shutil.which("tesseract") is None:
            return []
        try:
            import pytesseract
            pil_pages = self._render_pages_as_pil(file_path)
            pages: List[Tuple[int, str]] = []
            for idx, img in enumerate(pil_pages, start=1):
                preprocessed = self._preprocess_image(img)
                t = pytesseract.image_to_string(preprocessed, lang="por", config="--oem 3 --psm 1") or ""
                pages.append((idx, t))
            return pages
        except Exception as e:
            logger.debug(f"Tesseract por página falhou ({Path(file_path).name}): {e}")
            return []

    def _extract_with_claude_vision(self, file_path: str) -> Optional[str]:
        try:
            from src.bedrock.bedrock_client import bedrock_client
            image_pages = self._render_pages_as_png_bytes(file_path)
            if not image_pages:
                return None
            result = bedrock_client.extract_text_from_image_pages(image_pages)
            if result and isinstance(result, dict):
                return result.get("texto_extraido")
            return None
        except Exception as e:
            logger.error(f"Claude Vision falhou ({Path(file_path).name}): {e}")
            return None

    def _render_pages_as_pil(self, file_path: str) -> List["Image.Image"]:
        if not _PIL_AVAILABLE:
            return []
        try:
            import pdfplumber
            images = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    pil_img = page.to_image(resolution=self._RENDER_DPI).original.convert("RGB")
                    images.append(pil_img)
            return images
        except Exception as e:
            logger.debug(f"Renderização PIL falhou ({Path(file_path).name}): {e}")
            return []

    def _render_pages_as_png_bytes(self, file_path: str) -> List[Dict[str, Any]]:
        pil_pages = self._render_pages_as_pil(file_path)
        result = []
        for idx, img in enumerate(pil_pages[: self._MAX_VISION_PAGES], start=1):
            processed = self._preprocess_image(img)
            buf = BytesIO()
            processed.save(buf, format="PNG")
            result.append({"page_number": idx, "image_bytes": buf.getvalue()})
        return result

    @staticmethod
    def _is_glyph_encoded(text: str) -> bool:
        import re
        tokens = text.split()
        if not tokens:
            return False
        glyph_count = sum(1 for t in tokens if re.match(r"^/G[0-9A-Fa-f]{2}", t))
        return (glyph_count / len(tokens)) > 0.20

    @staticmethod
    def _fix_spaced_text(text: str) -> str:
        import re
        if not text:
            return text
        lines = text.splitlines()
        fixed_lines = []
        for line in lines:
            stripped = line.strip()
            tokens = stripped.split(" ")
            single_chars = sum(1 for t in tokens if len(t) == 1)
            if len(tokens) > 4 and (single_chars / len(tokens)) > 0.6:
                collapsed = re.sub(r"(?<!\s)( )(?!\s)", "", stripped)
                fixed_lines.append(collapsed)
            else:
                fixed_lines.append(line)
        return "\n".join(fixed_lines)

    def _preprocess_image(self, pil_image: "Image.Image") -> "Image.Image":
        if not _PIL_AVAILABLE:
            return pil_image
        img = pil_image.convert("RGB")
        gray = img.convert("L")
        enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
        sharpened = enhanced.filter(ImageFilter.SHARPEN)
        final = ImageOps.autocontrast(sharpened, cutoff=1)
        return final.convert("RGB")

    def get_metadata(self, file_path: str) -> Dict[str, Any]:
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                metadata: Dict[str, Any] = {
                    "num_pages": len(reader.pages),
                    "file_size": Path(file_path).stat().st_size,
                    "file_name": Path(file_path).name,
                }
                if reader.metadata:
                    m = reader.metadata
                    if m.title:
                        metadata["title"] = m.title
                    if m.author:
                        metadata["author"] = m.author
                return metadata
        except Exception as e:
            logger.error(f"Erro ao extrair metadados do PDF: {e}")
            return {"file_name": Path(file_path).name, "error": str(e)}

    def is_valid_pdf(self, file_path: str) -> bool:
        try:
            with open(file_path, "rb") as f:
                PyPDF2.PdfReader(f)
            return True
        except Exception:
            return False


pdf_processor = PDFProcessor()
