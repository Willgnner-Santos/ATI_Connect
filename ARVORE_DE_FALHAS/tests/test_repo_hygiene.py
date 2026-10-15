"""Regressões básicas de manutenção e empacotamento do repositório."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_requirements_runtime_uses_lock_as_source_of_truth():
    assert "-r requirements.lock" in read("requirements.txt")
    assert "-r requirements.in" not in read("requirements.txt")
    assert "--hash=sha256:" in read("requirements.lock")


def test_dev_requirements_do_not_mix_into_hashed_runtime_install():
    lines = [line.strip() for line in read("requirements-dev.txt").splitlines()]
    active = [line for line in lines if line and not line.startswith("#")]
    assert all(not line.startswith("-r ") for line in active)
    assert any(line.startswith("pytest==") for line in active)


def test_local_compose_creates_same_rabbitmq_credentials_it_uses():
    text = read("docker-compose.yml")
    for key in ("RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS", "RABBITMQ_USER", "RABBITMQ_PASSWORD"):
        assert key in text
    assert "${RABBITMQ_USER:-guest}" not in text


def test_sql_migration_fk_type_matches_primary_key():
    base = read("scripts/migrate_fault_tree_versions.sql")
    migration = read("scripts/migrate_fault_tree_version_supersedes.sql")
    primary = re.search(r"CREATE TABLE IF NOT EXISTS fault_tree_versions\s*\(\s*id\s+(\w+)", base, re.I)
    foreign = re.search(r"ADD COLUMN IF NOT EXISTS supersedes_version_id\s+(\w+)", migration, re.I)
    assert primary is not None and foreign is not None
    assert primary.group(1).upper() == foreign.group(1).upper()


def test_openapi_is_derived_from_app_not_committed_snapshot():
    assert not (ROOT / "docs/openapi.json").exists()
    script = read("scripts/export_openapi.py")
    assert "from src.api import app" in script
    assert "app.openapi()" in script


def test_docs_do_not_include_a_working_login_example():
    text = read("README.md")
    assert '"password"' not in text
    assert "auth/login" not in text
    assert "curl" in text and "ATI_ID_TOKEN" in text


def test_synthetic_fixture_is_parseable(tmp_path):
    import sys
    import pytest

    # test_r05_persistence_status.py stubs fitz via sys.modules at module level;
    # remove the stub so we get the real module (or skip if PyMuPDF is not installed).
    sys.modules.pop("fitz", None)
    fitz = pytest.importorskip("fitz")

    from tests.fixtures.create_test_pdf import make_minimal_pdf

    output = tmp_path / "fixture.pdf"
    make_minimal_pdf(["BT /F1 10 Tf 50 750 Td (Teste de pagina) Tj ET"], str(output))
    with fitz.open(output) as doc:
        assert len(doc) == 1
        assert "Teste de pagina" in doc[0].get_text()
