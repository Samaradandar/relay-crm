# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Object storage behind an interface: S3 on AWS, MinIO locally, or a plain folder for local
development without Docker. Buckets are always private; every link is signed and short-lived."""

import asyncio
import hashlib
import hmac
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from app.core.config import get_settings
from app.core.errors import StorageUnavailable

PUT_EXPIRES_SECONDS = 600  # upload links live 10 minutes
GET_EXPIRES_SECONDS = 300  # download links live 5 minutes


class Storage(Protocol):
    def presign_put(self, key: str, content_type: str) -> str: ...
    def presign_get(self, key: str, filename: str) -> str: ...
    async def size_of(self, key: str) -> int | None: ...
    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...


NO_STORAGE = "File storage (S3/MinIO) is not set up here, so files cannot be uploaded"


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        # Outside production there is no IAM role to fall back on: with no endpoint and no keys,
        # boto would probe the EC2 metadata service for seconds and then fail. Say so at once.
        self._configured = s.is_production or bool(s.s3_endpoint_url or s.s3_access_key)
        endpoint = s.s3_public_endpoint_url or s.s3_endpoint_url or None
        kwargs: dict[str, Any] = {
            "region_name": s.s3_region,
            "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        }
        if s.s3_access_key:
            kwargs["aws_access_key_id"] = s.s3_access_key
            kwargs["aws_secret_access_key"] = s.s3_secret_key
        self._signer = boto3.client("s3", endpoint_url=endpoint, **kwargs)
        self._internal = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url or None,
            **kwargs,
        )
        self._bucket = s.s3_bucket

    def _presign(self, op: str, params: dict[str, Any], expires: int) -> str:
        from botocore.exceptions import NoCredentialsError

        if not self._configured:
            raise StorageUnavailable(NO_STORAGE)
        try:
            return str(self._signer.generate_presigned_url(op, Params=params, ExpiresIn=expires))
        except NoCredentialsError as e:
            raise StorageUnavailable(NO_STORAGE) from e

    def presign_put(self, key: str, content_type: str) -> str:
        return self._presign(
            "put_object",
            {"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            PUT_EXPIRES_SECONDS,
        )

    def presign_get(self, key: str, filename: str) -> str:
        safe = filename.replace('"', "")
        return self._presign(
            "get_object",
            {
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe}"',
            },
            GET_EXPIRES_SECONDS,
        )

    async def size_of(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        def _head() -> int | None:
            try:
                return int(
                    self._internal.head_object(Bucket=self._bucket, Key=key)["ContentLength"]
                )
            except ClientError:
                return None

        return await asyncio.to_thread(_head)

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        """Write an object from the server itself (generated exports)."""

        def _put() -> None:
            self._internal.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        await asyncio.to_thread(_put)


class LocalDiskStorage:
    """Files in a folder on this machine (run_local.py without MinIO). The signed links point at
    the API's own /files/local/{put,get} routes, which check the signature and expiry, so the
    browser flow is the same as with S3. Never used in production."""

    def __init__(self, root: str, base_url: str, secret: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._base = base_url.rstrip("/")
        self._secret = secret.encode()

    def _sig(self, op: str, key: str, exp: int) -> str:
        msg = f"{op}|{exp}|{key}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def _link(self, op: str, key: str, expires: int, **extra: str) -> str:
        exp = int(time.time()) + expires
        query = {"key": key, "exp": str(exp), "sig": self._sig(op, key, exp), **extra}
        return f"{self._base}/files/local/{op}?{urlencode(query)}"

    def check(self, op: str, key: str, exp: int, sig: str) -> bool:
        return exp >= time.time() and hmac.compare_digest(sig, self._sig(op, key, exp))

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("bad key")
        return path

    def presign_put(self, key: str, content_type: str) -> str:
        return self._link("put", key, PUT_EXPIRES_SECONDS)

    def presign_get(self, key: str, filename: str) -> str:
        return self._link("get", key, GET_EXPIRES_SECONDS, name=filename)

    async def size_of(self, key: str) -> int | None:
        path = self.path_for(key)
        return path.stat().st_size if path.is_file() else None

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)


_override: Storage | None = None
_default: Storage | None = None


def set_storage(storage: Storage | None) -> None:
    global _override
    _override = storage


def get_storage() -> Storage:
    global _default
    if _override is not None:
        return _override
    if _default is None:
        s = get_settings()
        if s.local_storage_dir and not s.is_production:
            _default = LocalDiskStorage(s.local_storage_dir, s.local_storage_url, s.dev_jwt_secret)
        else:
            _default = S3Storage()
    return _default
