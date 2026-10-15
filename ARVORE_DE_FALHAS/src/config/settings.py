"""
Configuracoes centralizadas — Arvore de Falhas ATI
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # AWS Bedrock
    AWS_ACCESS_KEY_ID: str = Field(..., alias="AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: str = Field(..., alias="AWS_SECRET_ACCESS_KEY")
    BEDROCK_REGION: str = Field(default="sa-east-1", alias="BEDROCK_REGION")
    MODEL_ID: str = Field(default="anthropic.claude-3-haiku-20240307-v1:0", alias="MODEL_ID")
    EMBEDDING_MODEL_ID: str = Field(default="amazon.titan-embed-text-v2:0", alias="EMBEDDING_MODEL_ID")

    # PostgreSQL
    POSTGRES_URI: str = Field(default="postgresql://admin:change-me@localhost:5432/arvore_falhas", alias="POSTGRES_URI")

    # Qdrant
    QDRANT_HOST: str = Field(default="localhost", alias="QDRANT_HOST")
    QDRANT_PORT: int = Field(default=6333, alias="QDRANT_PORT")
    QDRANT_COLLECTION: str = Field(default="documents", alias="QDRANT_COLLECTION")
    QDRANT_UPSERT_BATCH_SIZE: int = Field(default=64, alias="QDRANT_UPSERT_BATCH_SIZE")
    CHUNK_PAYLOAD_MAX_CHARS: int = Field(default=8000, alias="CHUNK_PAYLOAD_MAX_CHARS")
    SEARCH_POOL_MULTIPLIER: int = Field(default=5, alias="SEARCH_POOL_MULTIPLIER")
    SEARCH_POOL_MAX: int = Field(default=80, alias="SEARCH_POOL_MAX")
    SEARCH_RERANK_MAX_CANDIDATES: int = Field(default=40, alias="SEARCH_RERANK_MAX_CANDIDATES")
    SEARCH_RERANK_WEIGHT: float = Field(default=0.7, alias="SEARCH_RERANK_WEIGHT")
    SEARCH_LEXICAL_BOOST_ENABLED: bool = Field(default=True, alias="SEARCH_LEXICAL_BOOST_ENABLED")

    # S3
    S3_BUCKET: str = Field(default="plataforma-ati-sandbox", alias="S3_BUCKET")
    S3_REGION: str = Field(default="sa-east-1", alias="S3_REGION")
    S3_ENDPOINT_URL: Optional[str] = Field(default=None, alias="S3_ENDPOINT_URL")
    S3_PUBLIC_ENDPOINT_URL: Optional[str] = Field(default=None, alias="S3_PUBLIC_ENDPOINT_URL")
    S3_USE_PATH_STYLE: bool = Field(default=True, alias="S3_USE_PATH_STYLE")
    S3_ACCESS_KEY: Optional[str] = Field(default=None, alias="S3_ACCESS_KEY")
    S3_SECRET_KEY: Optional[str] = Field(default=None, alias="S3_SECRET_KEY")

    # RabbitMQ
    AMQP_URL: Optional[str] = Field(default=None, alias="AMQP_URL")
    REQUEST_QUEUE: str = Field(default="docs.ingest.requests", alias="REQUEST_QUEUE")
    RESPONSE_QUEUE: str = Field(default="docs.ingest.responses", alias="RESPONSE_QUEUE")
    RABBITMQ_EXCHANGE: str = Field(default="document-processing", alias="RABBITMQ_EXCHANGE")
    RABBITMQ_EXCHANGE_TYPE: str = Field(default="topic", alias="RABBITMQ_EXCHANGE_TYPE")
    OUTBOX_POLL_INTERVAL_SECONDS: int = Field(default=5, alias="OUTBOX_POLL_INTERVAL_SECONDS")
    OUTBOX_BATCH_SIZE: int = Field(default=50, alias="OUTBOX_BATCH_SIZE")
    OUTBOX_MAX_ATTEMPTS: int = Field(default=10, alias="OUTBOX_MAX_ATTEMPTS")
    ENABLE_OUTBOX_PUBLISHER: bool = Field(default=True, alias="ENABLE_OUTBOX_PUBLISHER")
    ENABLE_REQUEST_CONSUMER: bool = Field(default=True, alias="ENABLE_REQUEST_CONSUMER")

    # Auth (id_token do Cognito — validação local via JWKs)
    COGNITO_REGION: str = Field(default="sa-east-1", alias="COGNITO_REGION")
    COGNITO_USER_POOL_ID: str = Field(..., alias="COGNITO_USER_POOL_ID")
    COGNITO_CLIENT_ID: str = Field(..., alias="COGNITO_CLIENT_ID")
    CORS_ALLOWED_ORIGINS: str = Field(default="", alias="CORS_ALLOWED_ORIGINS")

    # RBAC — roles de organização ficam no DynamoDB (mesmo padrão do backend principal)
    # Quando não configurado, roles globais (superadmin/collaborator) recebem acesso total.
    DYNAMODB_TABLE_ROLES: Optional[str] = Field(default=None, alias="DYNAMODB_TABLE_ROLES")
    DYNAMODB_REGION: str = Field(default="sa-east-1", alias="DYNAMODB_REGION")

    # Integração service-to-service (ATI backend → arvore-de-falhas)
    INTERNAL_API_KEY: Optional[str] = Field(default=None, alias="INTERNAL_API_KEY")

    # Processamento
    MAX_WORKERS: int = Field(default=4, alias="MAX_WORKERS")
    CHUNK_SIZE: int = Field(default=1000, alias="CHUNK_SIZE")
    EMBEDDING_DIMENSION: int = Field(default=1024, alias="EMBEDDING_DIMENSION")
    MAX_UPLOAD_BYTES: int = Field(default=52_428_800, alias="MAX_UPLOAD_BYTES")
    IMAGE_CATALOG_MAX_PER_DOC: int = Field(default=80, alias="IMAGE_CATALOG_MAX_PER_DOC")
    STALE_JOB_MINUTES: int = Field(default=30, alias="STALE_JOB_MINUTES")
    CHAT_CONTEXT_MAX_CHARS: int = Field(default=24_000, alias="CHAT_CONTEXT_MAX_CHARS")
    CHAT_DEBUG: bool = Field(default=False, alias="CHAT_DEBUG")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
