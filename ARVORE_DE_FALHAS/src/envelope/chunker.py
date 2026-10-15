"""
Chunker de texto para RAG — divide paginas em chunks de tamanho fixo com overlap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Chunk:
    index: int
    page_number: Optional[int]
    text: str
    char_start: int
    char_end: int
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    section_path: Optional[str] = None


_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)(?:[.\-:)]\s*|\s+)(.+?)\s*$")
_KEYWORD_HEADING = re.compile(
    r"^\s*(?:cap[ií]tulo|chapter|se[cç][aã]o|section|sezione)\s+(.+?)\s*$",
    re.IGNORECASE,
)


@dataclass
class _Heading:
    offset: int
    level: int
    number: Optional[str]
    title: str


def _scan_headings(text: str) -> List[_Heading]:
    """Localiza titulos comuns de manuais, inclusive em documentos multilíngues."""
    headings: List[_Heading] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        match = _NUMBERED_HEADING.match(stripped)
        keyword_match = _KEYWORD_HEADING.match(stripped)
        is_caps = (
            len(stripped) >= 4
            and len(stripped) <= 140
            and any(char.isalpha() for char in stripped)
            and stripped == stripped.upper()
        )
        if match:
            number, title = match.groups()
            headings.append(_Heading(offset, number.count(".") + 1, number, title))
        elif keyword_match:
            title = keyword_match.group(1)
            headings.append(_Heading(offset, 1, None, title))
        elif is_caps:
            headings.append(_Heading(offset, 1, None, stripped))
        offset += len(line)
    return headings


def _section_for_offset(headings: List[_Heading], offset: int) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    stack: List[_Heading] = []
    for heading in headings:
        if heading.offset > offset:
            break
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        stack.append(heading)
    if not stack:
        return None, None, None
    current = stack[-1]
    return current.number, current.title, " > ".join(item.title for item in stack)


def _safe_chunk_end(text: str, start: int, maximum_end: int) -> int:
    """Prefere fronteiras legíveis sem ultrapassar o tamanho solicitado."""
    if maximum_end >= len(text):
        return len(text)
    window = text[start:maximum_end]
    for separator in ("\n\n", "\n", ". "):
        split_at = window.rfind(separator)
        if split_at > max(0, len(window) // 2):
            return start + split_at + len(separator)
    return maximum_end


def chunk_pages(
    pages: List[Tuple[int, str]],
    chunk_size: int = 1000,
    overlap: int = 100,
    min_chars: int = 20,
) -> List[Chunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size deve ser maior que zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap deve ser maior ou igual a zero e menor que chunk_size")
    if not pages:
        return []

    full_text_parts: List[Tuple[int, str, int]] = []
    offset = 0
    for page_num, text in pages:
        full_text_parts.append((page_num, text, offset))
        offset += len(text) + 2

    full_text = "\n\n".join(t for _, t, _ in full_text_parts)
    headings = _scan_headings(full_text)
    chunks: List[Chunk] = []
    stride = chunk_size - overlap
    global_idx = 0

    for start in range(0, len(full_text), stride):
        end = _safe_chunk_end(full_text, start, min(start + chunk_size, len(full_text)))
        text_chunk = full_text[start:end].strip()
        if len(text_chunk) < min_chars:
            if end >= len(full_text):
                break
            continue

        page_num = _resolve_page(full_text_parts, start)
        section_number, section_title, section_path = _section_for_offset(headings, start)
        chunks.append(Chunk(
            index=global_idx,
            page_number=page_num,
            text=text_chunk,
            char_start=start,
            char_end=end,
            section_number=section_number,
            section_title=section_title,
            section_path=section_path,
        ))
        global_idx += 1

        if end >= len(full_text):
            break

    return chunks


def chunk_plain_text(text: str, chunk_size: int = 1000, overlap: int = 100, min_chars: int = 20) -> List[Chunk]:
    return chunk_pages([(None, text)], chunk_size=chunk_size, overlap=overlap, min_chars=min_chars)


def _resolve_page(parts: List[Tuple[int, str, int]], pos: int) -> Optional[int]:
    page_num = None
    for pn, _, off in parts:
        if off <= pos:
            page_num = pn
        else:
            break
    return page_num
