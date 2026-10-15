import os
import tempfile

from src.layers.bronze_layer import compute_sha256, file_size_bytes


def test_compute_sha256(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("conteudo de teste")
    h1 = compute_sha256(str(f))
    h2 = compute_sha256(str(f))
    assert h1 == h2
    assert len(h1) == 64


def test_file_size_bytes(tmp_path):
    f = tmp_path / "doc.pdf"
    data = b"\x25PDF-1.4" + b"\x00" * 100
    f.write_bytes(data)
    assert file_size_bytes(str(f)) == len(data)