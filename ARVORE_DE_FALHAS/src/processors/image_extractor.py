"""
Extrator de imagens embutidas em PDFs.
Suporta PyMuPDF (fitz) como estratégia principal e pdfplumber como fallback.
"""
import io
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from loguru import logger

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


@dataclass
class ExtractedImage:
    image_data: bytes
    page_number: int
    image_index: int
    width: int
    height: int
    format: str
    file_size: int
    image_hash: str
    x_position: Optional[float] = None
    y_position: Optional[float] = None
    colorspace: Optional[str] = None
    page_context: Optional[str] = None


class ImageExtractor:
    MIN_WIDTH = 100
    MIN_HEIGHT = 100
    MIN_FILE_SIZE = 2048
    MAX_IMAGES_PER_PAGE = 50
    CONTEXT_MARGIN = 120
    CONTEXT_MAX_CHARS = 1200

    def __init__(self):
        self._fitz_available = self._check_fitz()
        self._pdfplumber_available = self._check_pdfplumber()
        if not self._fitz_available and not self._pdfplumber_available:
            logger.warning("Nenhuma biblioteca de extração de imagens disponível.")

    def _check_fitz(self) -> bool:
        try:
            import fitz
            return True
        except Exception as e:
            logger.debug(f"PyMuPDF (fitz) indisponível: {e}")
            return False

    def _check_pdfplumber(self) -> bool:
        try:
            import pdfplumber
            return True
        except Exception as e:
            logger.debug(f"pdfplumber indisponível: {e}")
            return False

    def extract_images(self, file_path: str) -> List[ExtractedImage]:
        if self._fitz_available:
            return self._extract_with_fitz(file_path)
        elif self._pdfplumber_available:
            return self._extract_with_pdfplumber(file_path)
        else:
            logger.warning(f"Extração de imagens não disponível para {Path(file_path).name}")
            return []

    def _extract_with_fitz(self, file_path: str) -> List[ExtractedImage]:
        import fitz
        extracted_images: List[ExtractedImage] = []
        try:
            doc = fitz.open(file_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                try:
                    page_blocks = [
                        b for b in page.get_text("blocks")
                        if len(b) >= 5 and isinstance(b[4], str) and b[4].strip()
                        and (len(b) < 7 or b[6] == 0)
                    ]
                except Exception:
                    page_blocks = []
                page_full_text = " ".join(b[4].strip() for b in page_blocks)

                if len(image_list) > self.MAX_IMAGES_PER_PAGE:
                    logger.warning(
                        f"Página {page_num + 1} tem {len(image_list)} imagens. "
                        f"Processando apenas as primeiras {self.MAX_IMAGES_PER_PAGE}."
                    )
                    image_list = image_list[: self.MAX_IMAGES_PER_PAGE]

                for img_index, img_info in enumerate(image_list):
                    try:
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        if not base_image:
                            continue
                        image_bytes = base_image["image"]
                        width = base_image.get("width", 0)
                        height = base_image.get("height", 0)
                        ext = base_image.get("ext", "png")
                        colorspace = base_image.get("colorspace", None)

                        if width < self.MIN_WIDTH or height < self.MIN_HEIGHT:
                            continue
                        if len(image_bytes) < self.MIN_FILE_SIZE:
                            continue

                        image_hash = hashlib.sha256(image_bytes).hexdigest()
                        img_rect = None
                        for img_rect_tuple in page.get_image_rects(xref):
                            img_rect = img_rect_tuple
                            break

                        x_pos = img_rect.x0 if img_rect else None
                        y_pos = img_rect.y0 if img_rect else None
                        page_context = self._context_from_blocks(page_blocks, img_rect, page_full_text)

                        extracted_images.append(ExtractedImage(
                            image_data=image_bytes,
                            page_number=page_num + 1,
                            image_index=img_index,
                            width=width,
                            height=height,
                            format=ext,
                            file_size=len(image_bytes),
                            image_hash=image_hash,
                            x_position=x_pos,
                            y_position=y_pos,
                            colorspace=colorspace,
                            page_context=page_context,
                        ))
                    except Exception as e:
                        logger.warning(f"Erro ao extrair imagem {img_index} da página {page_num + 1}: {e}")
                        continue
            doc.close()
            logger.info(f"PyMuPDF: {len(extracted_images)} imagens extraídas de {Path(file_path).name}")
            return extracted_images
        except Exception as e:
            logger.error(f"Erro ao extrair imagens com PyMuPDF: {e}")
            return []

    def _context_from_blocks(self, page_blocks: list, img_rect, page_full_text: str) -> Optional[str]:
        if not page_blocks:
            return None
        if img_rect is None:
            return self._truncate_context(page_full_text)
        top, bottom = img_rect.y0, img_rect.y1
        near = []
        for b in page_blocks:
            bx0, by0, bx1, by1, btext = b[0], b[1], b[2], b[3], b[4]
            if by1 < top:
                dist = top - by1
            elif by0 > bottom:
                dist = by0 - bottom
            else:
                dist = 0.0
            if dist <= self.CONTEXT_MARGIN:
                near.append((by0, bx0, btext.strip()))
        if not near:
            return self._truncate_context(page_full_text)
        near.sort(key=lambda t: (t[0], t[1]))
        return self._truncate_context(" ".join(t[2] for t in near))

    def _truncate_context(self, text: str) -> Optional[str]:
        if not text:
            return None
        normalized = " ".join(text.split())
        if not normalized:
            return None
        if len(normalized) > self.CONTEXT_MAX_CHARS:
            normalized = normalized[: self.CONTEXT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
        return normalized

    def _extract_with_pdfplumber(self, file_path: str) -> List[ExtractedImage]:
        import pdfplumber
        extracted_images: List[ExtractedImage] = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    try:
                        images = page.images
                        try:
                            page_context = self._truncate_context(page.extract_text() or "")
                        except Exception:
                            page_context = None

                        if len(images) > self.MAX_IMAGES_PER_PAGE:
                            images = images[: self.MAX_IMAGES_PER_PAGE]

                        for img_index, img_info in enumerate(images):
                            try:
                                x0 = img_info.get("x0", 0)
                                y0 = img_info.get("top", 0)
                                x1 = img_info.get("x1", 0)
                                y1 = img_info.get("bottom", 0)
                                width = int(x1 - x0)
                                height = int(y1 - y0)
                                if width < self.MIN_WIDTH or height < self.MIN_HEIGHT:
                                    continue
                                bbox = (x0, y0, x1, y1)
                                cropped = page.crop(bbox).to_image(resolution=150)
                                pil_img = cropped.original
                                img_buffer = io.BytesIO()
                                pil_img.save(img_buffer, format="PNG")
                                image_bytes = img_buffer.getvalue()
                                if len(image_bytes) < self.MIN_FILE_SIZE:
                                    continue
                                image_hash = hashlib.sha256(image_bytes).hexdigest()
                                extracted_images.append(ExtractedImage(
                                    image_data=image_bytes,
                                    page_number=page_num,
                                    image_index=img_index,
                                    width=pil_img.width,
                                    height=pil_img.height,
                                    format="png",
                                    file_size=len(image_bytes),
                                    image_hash=image_hash,
                                    x_position=x0,
                                    y_position=y0,
                                    page_context=page_context,
                                ))
                            except Exception as e:
                                logger.warning(f"Erro ao processar imagem {img_index} da página {page_num}: {e}")
                                continue
                    except Exception as e:
                        logger.warning(f"Erro ao processar página {page_num}: {e}")
                        continue
            logger.info(f"pdfplumber: {len(extracted_images)} imagens extraídas de {Path(file_path).name}")
            return extracted_images
        except Exception as e:
            logger.error(f"Erro ao extrair imagens com pdfplumber: {e}")
            return []

    def deduplicate_images(self, images: List[ExtractedImage]) -> List[ExtractedImage]:
        seen_hashes = set()
        unique_images = []
        for img in images:
            if img.image_hash not in seen_hashes:
                seen_hashes.add(img.image_hash)
                unique_images.append(img)
        if len(images) != len(unique_images):
            logger.info(f"Dedupe: {len(images)} → {len(unique_images)} imagens únicas")
        return unique_images

    def get_extraction_stats(self, images: List[ExtractedImage]) -> Dict[str, Any]:
        if not images:
            return {"total_images": 0, "total_size_bytes": 0, "unique_pages": 0, "formats": {}, "avg_width": 0, "avg_height": 0}
        formats = {}
        for img in images:
            formats[img.format] = formats.get(img.format, 0) + 1
        unique_pages = len(set(img.page_number for img in images))
        total_size = sum(img.file_size for img in images)
        return {
            "total_images": len(images),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "unique_pages": unique_pages,
            "formats": formats,
            "avg_width": sum(img.width for img in images) // len(images),
            "avg_height": sum(img.height for img in images) // len(images),
        }


image_extractor = ImageExtractor()
