"""
Script de uso único para gerar tests/fixtures/manual_test_equipment.pdf.
Requer apenas a stdlib Python — sem dependências externas.

Execute: python tests/fixtures/create_test_pdf.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _pdf_string(s: str) -> bytes:
    return s.encode("latin-1", errors="replace")


def make_minimal_pdf(content_pages: list[str], output_path: str) -> None:
    """Gera um PDF mínimo (sem imagens) com o texto fornecido por página."""
    objects: list[bytes] = []
    offsets: list[int] = []

    def add_obj(body: bytes) -> int:
        idx = len(objects) + 1
        objects.append(body)
        return idx

    # Objetos 1 e 2 precisam ser Catalog/Pages: o Catalog referencia 2 0 R.
    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages_idx = add_obj(b"")  # atualizado após criar os objetos Page

    # Objetos 3+ — streams de página
    page_content_refs: list[int] = []
    for page_text in content_pages:
        stream_bytes = _pdf_string(page_text)
        compressed = zlib.compress(stream_bytes)
        stream_obj = (
            b"<< /Filter /FlateDecode /Length "
            + str(len(compressed)).encode()
            + b" >>\nstream\n"
            + compressed
            + b"\nendstream"
        )
        page_content_refs.append(add_obj(stream_obj))

    font_idx = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_refs: list[int] = []
    for content_ref in page_content_refs:
        page_body = (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 "
            + str(font_idx).encode()
            + b" 0 R >> >> "
            b"/Contents "
            + str(content_ref).encode()
            + b" 0 R >>"
        )
        page_refs.append(add_obj(page_body))

    kids = b" ".join(str(r).encode() + b" 0 R" for r in page_refs)
    pages_body = (
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(len(page_refs)).encode()
        + b" >>"
    )
    objects[pages_idx - 1] = pages_body

    # Build PDF bytes
    buf = bytearray()
    buf += b"%PDF-1.4\n"
    for i, obj_body in enumerate(objects):
        offsets.append(len(buf))
        buf += str(i + 1).encode() + b" 0 obj\n" + obj_body + b"\nendobj\n"

    xref_offset = len(buf)
    buf += b"xref\n"
    buf += b"0 " + str(len(objects) + 1).encode() + b"\n"
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()

    buf += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    buf += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"

    Path(output_path).write_bytes(bytes(buf))
    print(f"PDF gerado: {output_path} ({len(buf)} bytes)")


def _make_page_stream(page_num: int, text_lines: list[str]) -> str:
    """Gera o stream PDF para uma página com as linhas fornecidas."""
    cmds = [f"BT /F1 10 Tf 50 {750 - 15 * i} Td ({line}) Tj ET"
            for i, line in enumerate(text_lines)]
    return "\n".join(cmds)


if __name__ == "__main__":
    # Conteúdo controlado com modos de falha reconhecíveis para o gerador
    pages = [
        _make_page_stream(1, [
            "MANUAL TECNICO - EQUIPAMENTO DE TESTE ATI",
            "Bomba Centrifuga Industrial - Modelo ATI-TEST-01",
            "Fabricante: ATI Test Equipment",
            "[Pagina 1]",
        ]),
        _make_page_stream(2, [
            "[Pagina 2] SECAO 3 - MODOS DE FALHA E DIAGNOSTICO",
            "3.1 Falha no rolamento (bearing failure)",
            "Descricao: Desgaste ou trinca nos rolamentos do eixo principal.",
            "Causas: Lubrificacao insuficiente, contaminacao por particulas.",
            "Efeitos: Vibracao excessiva, ruido metalico, aumento de temperatura.",
            "Acoes: Substituir rolamento, verificar alinhamento do eixo.",
            "Severidade: alta",
        ]),
        _make_page_stream(3, [
            "[Pagina 3] 3.2 Superaquecimento do motor (motor overheating)",
            "Descricao: Temperatura do motor acima do limite operacional.",
            "Causas: Sobrecarga eletrica, falha no sistema de refrigeracao.",
            "Efeitos: Desligamento por protecao termica, dano ao enrolamento.",
            "Acoes: Verificar corrente, limpar filtros de ar, checar ventilador.",
            "Alarme: TEMP-HIGH-01 - Temperatura elevada do motor",
            "Severidade: alta",
        ]),
        _make_page_stream(4, [
            "[Pagina 4] 3.3 Falha de lubrificacao (lubrication failure)",
            "Descricao: Nivel de oleo insuficiente ou oleo contaminado.",
            "Causas: Vazamento de oleo, intervalo de troca excedido.",
            "Efeitos: Desgaste prematuro de componentes moveis, travamento.",
            "Acoes: Verificar nivel de oleo, inspecionar vedacoes, trocar oleo.",
            "Alarme: LUB-LOW-02 - Pressao de lubrificacao baixa",
            "Severidade: alta",
        ]),
        _make_page_stream(5, [
            "[Pagina 5] 3.4 Sobrecarga do motor (motor overload)",
            "Descricao: Corrente de operacao acima do valor nominal.",
            "Causas: Bloqueio mecanico, fluido muito viscoso, falha eletrica.",
            "Efeitos: Atuacao do relé de sobrecarga, parada do equipamento.",
            "Acoes: Verificar mecanismo de acionamento, checar corrente nominal.",
            "Alarme: OVL-01 - Sobrecarga detectada",
            "Severidade: critica",
            "3.5 Vazamento de fluido (fluid leak)",
            "Descricao: Perda de fluido pelo selo mecanico ou junta.",
            "Causas: Desgaste do selo, pressao acima do limite, vibracao.",
            "Efeitos: Reducao de vazao, contaminacao ambiental.",
            "Acoes: Substituir selo mecanico, verificar pressao de operacao.",
            "Severidade: media",
        ]),
    ]

    output = Path(__file__).parent / "manual_test_equipment.pdf"
    make_minimal_pdf(pages, str(output))
