"""Cria idempotentemente o bucket do storage S3 local."""
from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError


def build_client() -> Any:
    endpoint_url = os.environ["S3_ENDPOINT_URL"]
    region = os.getenv("S3_REGION", "sa-east-1")
    kwargs: dict[str, Any] = {
        "endpoint_url": endpoint_url,
        "region_name": region,
        "aws_access_key_id": os.environ["S3_ACCESS_KEY"],
        "aws_secret_access_key": os.environ["S3_SECRET_KEY"],
    }
    if os.getenv("S3_USE_PATH_STYLE", "true").lower() in {"1", "true", "yes", "on"}:
        kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
    return boto3.client("s3", **kwargs)


def ensure_bucket(client: Any, bucket: str) -> bool:
    """Garante que o bucket exista; retorna True somente quando ele é criado."""
    try:
        client.head_bucket(Bucket=bucket)
        return False
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if status != 404 and code not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    client.create_bucket(Bucket=bucket)
    return True


def main() -> None:
    bucket = os.getenv("S3_BUCKET", "plataforma-ati-sandbox")
    created = ensure_bucket(build_client(), bucket)
    action = "criado" if created else "já existe"
    print(f"Bucket S3 '{bucket}' {action}.")


if __name__ == "__main__":
    main()
