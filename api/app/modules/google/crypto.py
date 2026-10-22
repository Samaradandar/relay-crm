# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Envelope encryption for Google refresh tokens at rest (ST-01). Real KMS in production;
a local Fernet key in dev (refused by `validate_for_start` once ENV=production and a Google
client id is configured -- see app/core/config.py). Tokens are never logged and never returned
to the browser by any endpoint in this module.
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    # KMS plan data keys are 32 bytes; the dev fallback setting is an arbitrary string, so derive
    # a valid Fernet key from it the same way either way (sha256 -> url-safe base64).
    raw = hashlib.sha256(get_settings().google_token_encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_refresh_token(token: str) -> str:
    """KMS envelope encryption in production (GOOGLE_KMS_KEY_ID set): boto3's `kms.encrypt`
    directly, since a refresh token is well under KMS's 4 KB symmetric-encrypt limit, so a
    separate data key isn't needed. Local dev: Fernet, see module docstring."""
    settings = get_settings()
    if settings.google_kms_key_id:
        import boto3

        client = boto3.client("kms", region_name=settings.s3_region)
        ciphertext = client.encrypt(KeyId=settings.google_kms_key_id, Plaintext=token.encode())[
            "CiphertextBlob"
        ]
        return "kms:" + base64.b64encode(ciphertext).decode()
    return "fernet:" + _fernet().encrypt(token.encode()).decode()


def decrypt_refresh_token(stored: str) -> str:
    scheme, _, payload = stored.partition(":")
    if scheme == "kms":
        import boto3

        settings = get_settings()
        client = boto3.client("kms", region_name=settings.s3_region)
        plaintext: bytes = client.decrypt(CiphertextBlob=base64.b64decode(payload))["Plaintext"]
        return plaintext.decode()
    if scheme == "fernet":
        return _fernet().decrypt(payload.encode()).decode()
    raise ValueError("Unrecognised token encryption scheme")
