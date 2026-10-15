"""
Testes estáticos de integridade do lockfile — R22.

Garante que:
- requirements.lock contém entradas --hash= (compatível com --require-hashes).
- Nenhum pacote Windows-only (pywin32, pywinpty) está no lock sem marker de plataforma.
- Todas as dependências diretas de requirements.in estão presentes no lock.
- Versões de segurança mínimas (set/2026) estão satisfeitas.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.version import Version

_ROOT = Path(__file__).parent.parent
_LOCK = _ROOT / "requirements.lock"
_IN = _ROOT / "requirements.in"


def _lock_content() -> str:
    if not _LOCK.exists():
        pytest.skip("requirements.lock não encontrado")
    return _LOCK.read_text(encoding="utf-8")


def _in_direct_deps() -> list[str]:
    if not _IN.exists():
        pytest.skip("requirements.in não encontrado")
    deps = []
    for line in _IN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # normalizar: pegar nome antes de >= == etc. e lower-case com underscores → hifens
        name = re.split(r"[>=<!;\[]", line)[0].strip().lower().replace("_", "-")
        if name:
            deps.append(name)
    return deps


def test_lockfile_has_hashes():
    """R22 — requirements.lock deve conter hashes (compatível com --require-hashes)."""
    content = _lock_content()
    hash_count = content.count("--hash=sha256:")
    assert hash_count > 0, (
        "R22 — requirements.lock não contém hashes. "
        "Regenerar com: pip-compile --generate-hashes requirements.in"
    )


def test_lockfile_no_windows_only_packages():
    """R22 — requirements.lock não deve conter pacotes Windows-only sem marker de plataforma."""
    content = _lock_content()
    windows_packages = ["pywin32", "pywinpty", "pyreadline", "colorama"]
    for pkg in windows_packages:
        # Aceitar se houver marcador de plataforma (ex: ; sys_platform == "win32")
        pattern = re.compile(rf"^{pkg}==", re.MULTILINE | re.IGNORECASE)
        if pattern.search(content):
            # Verificar se linha seguinte (comentário) menciona marker
            assert f'sys_platform == "win32"' in content or f"sys_platform == 'win32'" in content, (
                f"R22 — '{pkg}' encontrado no lock sem marker de plataforma. "
                "O lock foi gerado no ambiente de desenvolvimento Windows, não em Python 3.12/Linux."
            )


def test_lockfile_contains_all_direct_deps():
    """R22 — todas as dependências diretas de requirements.in devem estar no lock."""
    content = _lock_content().lower()
    missing = []
    for dep in _in_direct_deps():
        # Nome normalizado no lock pode ter variações (ex: PyMuPDF → pymupdf)
        normalized = dep.replace("-", "[-_]")
        if not re.search(rf"^{normalized}[=\[]", content, re.MULTILINE):
            missing.append(dep)
    assert not missing, (
        f"R22 — dependências diretas ausentes do lock: {missing}. "
        "Regenerar o lock com pip-compile."
    )


def test_lockfile_generated_for_python312():
    """R22 — requirements.lock deve ter sido gerado para Python 3.12."""
    content = _lock_content()
    assert "Python 3.12" in content, (
        "R22 — requirements.lock não contém 'Python 3.12' no cabeçalho. "
        "Regenerar com: docker run --rm -v $(pwd):/src python:3.12-slim "
        "sh -c 'pip install pip-tools && pip-compile --generate-hashes /src/requirements.in -o /src/requirements.lock'"
    )


def test_security_versions_in_lock():
    """R22 — versões de segurança mínimas devem estar satisfeitas no lock."""
    content = _lock_content()

    checks = [
        ("python-multipart", r"^python-multipart==(\S+)", Version("0.0.31")),
        ("starlette", r"^starlette==(\S+)", Version("1.3.1")),
        ("fastapi", r"^fastapi==(\S+)", Version("0.133.0")),
        ("pydantic", r"^pydantic==(\S+)", Version("2.7.0")),
    ]

    for pkg, pattern, minimum in checks:
        m = re.search(pattern, content, re.MULTILINE)
        if not m:
            pytest.fail(f"R22 — '{pkg}' não encontrado no lock.")
        installed = Version(m.group(1).rstrip("\\").strip())
        assert installed >= minimum, (
            f"R22 — {pkg} {installed} no lock está abaixo do mínimo seguro {minimum}."
        )
