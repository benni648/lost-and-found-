"""Private photo storage with a local development backend and S3-compatible support."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from .settings import PROJECT_ROOT, settings

LOCAL_SIGNING_SECRET = secrets.token_bytes(32)
DEFAULT_URL_TTL_SECONDS = 300


def _validated_key(key: str) -> PurePosixPath:
    if "\\" in key or any(part in {"", ".", ".."} for part in key.split("/")):
        raise ValueError("invalid object key")
    path = PurePosixPath(key)
    if not key or path.is_absolute():
        raise ValueError("invalid object key")
    return path


class LocalObjectStore:
    def __init__(self, media_dir: Path, signing_secret: str | None = None) -> None:
        self.root = media_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.signing_secret = signing_secret.encode("utf-8") if signing_secret else LOCAL_SIGNING_SECRET

    def _path(self, key: str) -> Path:
        if key.startswith("data/"):
            key = f"legacy/{key}"
        parts = _validated_key(key).parts
        if parts[0] == "legacy":
            path = PROJECT_ROOT.joinpath(*parts[1:]).resolve()
            allowed_root = PROJECT_ROOT.resolve()
        else:
            path = self.root.joinpath(*parts).resolve()
            allowed_root = self.root
        if not path.is_relative_to(allowed_root):
            raise ValueError("invalid object key")
        return path

    def put_photo(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def path(self, key: str) -> Path:
        return self._path(key)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def signed_url(self, key: str, expires_in: int = DEFAULT_URL_TTL_SECONDS) -> str:
        if not 1 <= expires_in <= DEFAULT_URL_TTL_SECONDS:
            raise ValueError(f"expires_in must be between 1 and {DEFAULT_URL_TTL_SECONDS} seconds")
        if key.startswith("data/"):
            key = f"legacy/{key}"
        _validated_key(key)
        expires = int(time.time()) + expires_in
        signature = self._signature(key, expires)
        escaped_key = quote(key, safe="/")
        return f"/media/{escaped_key}?expires={expires}&signature={signature}"

    def verify_signed_url(self, key: str, expires: int, signature: str) -> bool:
        if expires < int(time.time()) or expires > int(time.time()) + DEFAULT_URL_TTL_SECONDS + 5:
            return False
        return hmac.compare_digest(self._signature(key, expires), signature)

    def _signature(self, key: str, expires: int) -> str:
        message = f"{key}\n{expires}".encode()
        return hmac.new(self.signing_secret, message, hashlib.sha256).hexdigest()


class S3ObjectStore:
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region_name: str = "auto",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("S3 photo storage requires the optional boto3 dependency.") from exc

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region_name,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
        )

    def put_photo(self, key: str, data: bytes) -> str:
        _validated_key(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="image/jpeg")
        return key

    def read(self, key: str) -> bytes:
        _validated_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        _validated_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, expires_in: int = DEFAULT_URL_TTL_SECONDS) -> str:
        if not 1 <= expires_in <= DEFAULT_URL_TTL_SECONDS:
            raise ValueError(f"expires_in must be between 1 and {DEFAULT_URL_TTL_SECONDS} seconds")
        _validated_key(key)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


@lru_cache(maxsize=1)
def get_object_store() -> LocalObjectStore | S3ObjectStore:
    if settings.object_storage_backend == "local":
        return get_local_object_store()
    if not settings.s3_bucket:
        raise ValueError("S3_BUCKET is required when OBJECT_STORAGE_BACKEND=s3.")
    return S3ObjectStore(
        settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
    )


@lru_cache(maxsize=1)
def get_local_object_store() -> LocalObjectStore:
    return LocalObjectStore(settings.media, settings.photo_signing_secret)


def object_key_for_report(report_id: str) -> str:
    allowed_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not report_id or any(char not in allowed_characters for char in report_id):
        raise ValueError("invalid report id for photo key")
    return f"reports/{report_id}.jpg"


def media_url(image_path: str | None) -> str | None:
    if not image_path:
        return None
    key = image_path.replace("\\", "/")
    if key.startswith("data/"):
        key = f"legacy/{key}"
    if key.startswith("legacy/") and settings.object_storage_backend == "s3":
        return get_local_object_store().signed_url(key)
    return get_object_store().signed_url(key)
