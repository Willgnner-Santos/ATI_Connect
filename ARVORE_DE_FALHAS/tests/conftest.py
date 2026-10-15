"""
Testes para o pipeline de arvore de falhas.
Nao requerem servicos externos (DB, S3, Bedrock, RabbitMQ).
"""
from __future__ import annotations

import os

# Defaults mínimos para que Settings() seja instanciável sem .env real.
# Valores reais no .env local sobrescrevem via pydantic-settings (env tem precedência).
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("COGNITO_REGION", "sa-east-1")
os.environ.setdefault("COGNITO_USER_POOL_ID", "test-pool-id")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client-id")