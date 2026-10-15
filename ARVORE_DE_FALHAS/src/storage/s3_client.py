"""
Cliente S3 — upload, download, list e delete de objetos.
Usa boto3 nativo com AWS S3 ou endpoints S3 compativeis.
"""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings


def resolve_bucket_and_key(object_path: str) -> tuple[str, str]:
    """Resolve paths legados (key) e atuais (bucket/key) para o S3 configurado."""
    path = str(object_path or "").strip().lstrip("/")
    if not path:
        return settings.S3_BUCKET, ""
    bucket, separator, key = path.partition("/")
    if separator and bucket == settings.S3_BUCKET:
        return bucket, key
    return settings.S3_BUCKET, path

_RETRYABLE = (ClientError, ConnectionError, TimeoutError)


class S3Client:
    """Cliente S3 com retry e interface simplificada."""

    def __init__(self) -> None:
        self._client = None
        self._presign_client = None

    def _get_client(self):
        if self._client is None:
            kwargs = {
                "region_name": settings.S3_REGION,
                "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
                "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
            }
            if settings.S3_ENDPOINT_URL:
                kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
                # Em modo local, usar credenciais do endpoint se fornecidas.
                if settings.S3_ACCESS_KEY:
                    kwargs["aws_access_key_id"] = settings.S3_ACCESS_KEY
                if settings.S3_SECRET_KEY:
                    kwargs["aws_secret_access_key"] = settings.S3_SECRET_KEY
            if settings.S3_USE_PATH_STYLE:
                kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
            self._client = boto3.client("s3", **kwargs)
        return self._client

    # ── Upload ────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def upload_file(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> bool:
        try:
            extra: Dict[str, Any] = {}
            if metadata:
                extra["ExtraArgs"] = {
                    "Metadata": {k: str(v) for k, v in metadata.items()}
                }
            self._get_client().upload_file(file_path, bucket_name, object_name, **extra)
            return True
        except Exception as e:
            logger.error(f"Erro ao fazer upload para S3: {e}")
            return False

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def upload_bytes(
        self,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: Optional[Dict[str, str]] = None,
    ) -> bool:
        try:
            extra: Dict[str, Any] = {"ContentType": content_type}
            if metadata:
                extra["Metadata"] = {k: str(v) for k, v in metadata.items()}
            self._get_client().put_object(
                Bucket=bucket_name,
                Key=object_name,
                Body=data,
                **extra,
            )
            return True
        except Exception as e:
            logger.error(f"Erro ao fazer upload de bytes para S3: {e}")
            return False

    def upload_json(
        self,
        bucket_name: str,
        object_name: str,
        data: Any,
        metadata: Optional[Dict[str, str]] = None,
    ) -> bool:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        return self.upload_bytes(
            bucket_name, object_name, payload,
            content_type="application/json", metadata=metadata,
        )

    # ── Download ──────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def download_file(
        self, bucket_name: str, object_name: str, file_path: str
    ) -> bool:
        try:
            self._get_client().download_file(bucket_name, object_name, file_path)
            return True
        except Exception as e:
            logger.error(f"Erro ao baixar arquivo do S3: {e}")
            return False

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def download_bytes(self, bucket_name: str, object_name: str) -> Optional[bytes]:
        try:
            response = self._get_client().get_object(
                Bucket=bucket_name, Key=object_name
            )
            data = response["Body"].read()
            return data
        except Exception as e:
            logger.error(f"Erro ao baixar bytes do S3: {e}")
            return None

    def download_json(self, bucket_name: str, object_name: str) -> Optional[Any]:
        raw = self.download_bytes(bucket_name, object_name)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.error(f"Erro ao decodificar JSON do S3: {e}")
            return None

    # ── List / Delete ─────────────────────────────────────────────

    def list_objects(
        self, bucket_name: str, prefix: Optional[str] = None
    ) -> List[str]:
        try:
            paginator = self._get_client().get_paginator("list_objects_v2")
            names: List[str] = []
            for page in paginator.paginate(
                Bucket=bucket_name, Prefix=prefix or ""
            ):
                for obj in page.get("Contents", []):
                    names.append(obj["Key"])
            return names
        except Exception as e:
            logger.error(f"Erro ao listar objetos do S3: {e}")
            return []

    def delete_object(self, bucket_name: str, object_name: str) -> bool:
        try:
            self._get_client().delete_object(Bucket=bucket_name, Key=object_name)
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar objeto do S3: {e}")
            return False

    def get_object_metadata(
        self, bucket_name: str, object_name: str
    ) -> Optional[Dict[str, Any]]:
        try:
            response = self._get_client().head_object(
                Bucket=bucket_name, Key=object_name
            )
            return {
                "size": response.get("ContentLength"),
                "content_type": response.get("ContentType"),
                "last_modified": response.get("LastModified"),
                "metadata": response.get("Metadata", {}),
            }
        except Exception as e:
            logger.error(f"Erro ao obter metadata do S3: {e}")
            return None

    def generate_download_url(
        self, bucket_name: str, object_name: str, expires_in: int = 300
    ) -> Optional[str]:
        """Gera URL temporária somente depois de o chamador validar o escopo."""
        try:
            client = self._get_client()
            if settings.S3_PUBLIC_ENDPOINT_URL:
                if self._presign_client is None:
                    kwargs: Dict[str, Any] = {
                        "region_name": settings.S3_REGION,
                        "endpoint_url": settings.S3_PUBLIC_ENDPOINT_URL,
                        "aws_access_key_id": settings.S3_ACCESS_KEY or settings.AWS_ACCESS_KEY_ID,
                        "aws_secret_access_key": settings.S3_SECRET_KEY or settings.AWS_SECRET_ACCESS_KEY,
                    }
                    if settings.S3_USE_PATH_STYLE:
                        kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
                    self._presign_client = boto3.client("s3", **kwargs)
                client = self._presign_client
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name, "Key": object_name},
                ExpiresIn=max(60, min(int(expires_in), 3600)),
            )
        except Exception as e:
            logger.error(f"Erro ao gerar URL assinada do S3: {e}")
            return None


class _LazyS3Client:
    """Proxy lazy-loading para o singleton."""

    def __getattr__(self, name: str):
        return getattr(_get_s3_client(), name)


def _get_s3_client() -> S3Client:
    return S3Client()


s3_client: S3Client = _LazyS3Client()  # type: ignore[assignment]
