import importlib
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from scripts.init_s3_bucket import ensure_bucket
from src.config import settings

s3_client_module = importlib.import_module("src.storage.s3_client")
S3Client = s3_client_module.S3Client


def test_custom_endpoint_uses_local_credentials_and_path_style(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(s3_client_module.boto3, "client", factory)
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "http://rustfs:9000")
    monkeypatch.setattr(settings, "S3_USE_PATH_STYLE", True)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", "local-access")
    monkeypatch.setattr(settings, "S3_SECRET_KEY", "local-secret")

    S3Client()._get_client()

    _, kwargs = factory.call_args
    assert kwargs["endpoint_url"] == "http://rustfs:9000"
    assert kwargs["aws_access_key_id"] == "local-access"
    assert kwargs["aws_secret_access_key"] == "local-secret"
    assert kwargs["config"].s3["addressing_style"] == "path"


def test_aws_s3_omits_custom_endpoint(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(s3_client_module.boto3, "client", factory)
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", None)
    monkeypatch.setattr(settings, "S3_USE_PATH_STYLE", False)

    S3Client()._get_client()

    _, kwargs = factory.call_args
    assert "endpoint_url" not in kwargs
    assert "config" not in kwargs
    assert kwargs["aws_access_key_id"] == settings.AWS_ACCESS_KEY_ID
    assert kwargs["aws_secret_access_key"] == settings.AWS_SECRET_ACCESS_KEY


def test_object_operations_delegate_to_boto3(monkeypatch, tmp_path):
    boto_client = MagicMock()
    paginator = boto_client.get_paginator.return_value
    paginator.paginate.return_value = [{"Contents": [{"Key": "prefix/file.txt"}]}]
    boto_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"payload")}
    boto_client.head_object.return_value = {
        "ContentLength": 7,
        "ContentType": "text/plain",
        "LastModified": "now",
        "Metadata": {"source": "test"},
    }
    boto_client.generate_presigned_url.return_value = "https://signed.example/image"
    client = S3Client()
    monkeypatch.setattr(client, "_get_client", lambda: boto_client)
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    target = tmp_path / "target.txt"

    assert client.upload_file("bucket", "file.txt", str(source), {"source": "test"})
    assert client.upload_bytes("bucket", "raw.bin", b"payload", "text/plain")
    assert client.download_file("bucket", "file.txt", str(target))
    assert client.download_bytes("bucket", "file.txt") == b"payload"
    assert client.list_objects("bucket", "prefix/") == ["prefix/file.txt"]
    assert client.get_object_metadata("bucket", "file.txt") == {
        "size": 7,
        "content_type": "text/plain",
        "last_modified": "now",
        "metadata": {"source": "test"},
    }
    assert client.delete_object("bucket", "file.txt")
    assert client.generate_download_url("bucket", "image.png") == "https://signed.example/image"

    boto_client.upload_file.assert_called_once_with(
        str(source),
        "bucket",
        "file.txt",
        ExtraArgs={"Metadata": {"source": "test"}},
    )
    boto_client.put_object.assert_called_once_with(
        Bucket="bucket", Key="raw.bin", Body=b"payload", ContentType="text/plain"
    )
    boto_client.download_file.assert_called_once_with("bucket", "file.txt", str(target))
    boto_client.delete_object.assert_called_once_with(Bucket="bucket", Key="file.txt")
    boto_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "bucket", "Key": "image.png"},
        ExpiresIn=300,
    )


def test_bucket_initializer_is_idempotent():
    client = MagicMock()
    client.head_bucket.side_effect = [
        ClientError(
            {
                "Error": {"Code": "NoSuchBucket", "Message": "missing"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadBucket",
        ),
        None,
    ]

    assert ensure_bucket(client, "bucket") is True
    assert ensure_bucket(client, "bucket") is False
    client.create_bucket.assert_called_once_with(Bucket="bucket")
