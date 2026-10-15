"""Exporta a especificação OpenAPI diretamente das rotas FastAPI atuais.

Uso (com dependências runtime instaladas):
    python scripts/export_openapi.py
    python scripts/export_openapi.py --output /tmp/openapi.json

A exportação não inicializa o lifespan nem acessa a infraestrutura; configura
somente placeholders para variáveis obrigatórias ausentes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Obrigatórias no modelo Settings, mas não necessárias para introspecção de rotas.
for env_key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID"):
    os.environ.setdefault(env_key, "openapi-export-placeholder")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "openapi.json")
    args = parser.parse_args()

    from src.api import app  # Só importa após preencher variáveis mínimas.

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"OpenAPI exportado: {output_path} ({len(app.openapi()['paths'])} caminhos)")


if __name__ == "__main__":
    main()
