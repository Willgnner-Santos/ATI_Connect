"""Smoke test opt-in para um endpoint S3 real, como o RustFS do Compose."""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

if os.getenv("RUN_S3_INTEGRATION") != "1":
    pytest.skip(
        "Defina RUN_S3_INTEGRATION=1 para executar contra o RustFS.",
        allow_module_level=True,
    )

from scripts.init_s3_bucket import build_client, ensure_bucket


def test_rustfs_bucket_and_object_lifecycle():
    client = build_client()
    bucket = os.getenv("S3_BUCKET", "plataforma-ati-sandbox")
    key = f"integration/{uuid.uuid4()}.txt"
    payload = b"rustfs integration test"

    ensure_bucket(client, bucket)
    assert ensure_bucket(client, bucket) is False

    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="text/plain",
            Metadata={"source": "pytest"},
        )
        head = client.head_object(Bucket=bucket, Key=key)
        assert head["ContentLength"] == len(payload)
        assert head["Metadata"]["source"] == "pytest"

        listed = client.list_objects_v2(Bucket=bucket, Prefix=key)
        assert [item["Key"] for item in listed.get("Contents", [])] == [key]

        downloaded = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        assert downloaded == payload
    finally:
        client.delete_object(Bucket=bucket, Key=key)
