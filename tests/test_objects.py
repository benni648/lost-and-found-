from __future__ import annotations

import sys
import time
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from lostfound.objects import LocalObjectStore, S3ObjectStore, object_key_for_report


def test_local_store_upload_read_and_signed_url_lifecycle(tmp_path):
    store = LocalObjectStore(tmp_path / "private", "test-signing-secret")
    key = store.put_photo("reports/FR-0001.jpg", b"private-image")

    url = store.signed_url(key)
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    expires = int(params["expires"][0])
    signature = params["signature"][0]

    assert key == "reports/FR-0001.jpg"
    assert store.read(key) == b"private-image"
    assert store.verify_signed_url(key, expires, signature)
    assert not store.verify_signed_url(key, int(time.time()) - 1, signature)
    assert not store.verify_signed_url("reports/other.jpg", expires, signature)


@pytest.mark.parametrize("key", ["../outside.jpg", "reports/../outside.jpg", "/absolute.jpg", r"reports\photo.jpg"])
def test_local_store_rejects_unsafe_keys(tmp_path, key):
    store = LocalObjectStore(tmp_path / "private", "test-signing-secret")
    with pytest.raises(ValueError, match="invalid object key"):
        store.put_photo(key, b"image")


def test_local_store_reads_legacy_project_relative_photos(tmp_path, monkeypatch):
    import lostfound.objects as objects

    project_root = tmp_path / "project"
    image = project_root / "data" / "media" / "synthetic.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"legacy-image")
    monkeypatch.setattr(objects, "PROJECT_ROOT", project_root)
    store = LocalObjectStore(tmp_path / "private", "test-signing-secret")

    assert store.read("data/media/synthetic.png") == b"legacy-image"
    assert store.read("legacy/data/media/synthetic.png") == b"legacy-image"


def test_report_object_keys_validate_identifiers():
    assert object_key_for_report("FR-0001") == "reports/FR-0001.jpg"
    with pytest.raises(ValueError, match="invalid report id"):
        object_key_for_report("../outside")


def test_s3_store_uses_private_bucket_operations_and_presigned_get_url():
    class FakeS3Client:
        def __init__(self):
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(("put", kwargs))

        def get_object(self, **kwargs):
            self.calls.append(("get", kwargs))
            return {"Body": BytesIO(b"private-image")}

        def delete_object(self, **kwargs):
            self.calls.append(("delete", kwargs))

        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            self.calls.append(("sign", operation, Params, ExpiresIn))
            return "https://private.example/signed"

    store = object.__new__(S3ObjectStore)
    store.bucket = "private-bucket"
    store.client = FakeS3Client()

    key = store.put_photo("reports/FR-0001.jpg", b"private-image")
    assert store.read(key) == b"private-image"
    assert store.signed_url(key) == "https://private.example/signed"
    store.delete(key)

    assert [call[0] for call in store.client.calls] == ["put", "get", "sign", "delete"]
    assert store.client.calls[0][1]["Bucket"] == "private-bucket"
    assert store.client.calls[0][1]["ContentType"] == "image/jpeg"
    assert store.client.calls[2][1] == "get_object"
    assert store.client.calls[2][3] == 300


def test_s3_store_uses_default_credentials_for_blank_compose_values(monkeypatch):
    observed = {}

    def create_client(service, **kwargs):
        observed["service"] = service
        observed.update(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=create_client))

    S3ObjectStore(
        "private-bucket",
        endpoint_url="",
        access_key_id="",
        secret_access_key="",
    )

    assert observed["service"] == "s3"
    assert observed["endpoint_url"] is None
    assert observed["aws_access_key_id"] is None
    assert observed["aws_secret_access_key"] is None
