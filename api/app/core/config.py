# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Reads settings from environment. Every provider choice (auth, files, email) is config-only."""

from functools import lru_cache
from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    org_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    timezone_display: str = "Asia/Kolkata"

    database_url: str = "postgresql+asyncpg://relay_app:relay_app@localhost:5432/relay"
    migration_database_url: str = "postgresql+asyncpg://relay:relay@localhost:5432/relay"
    # Connections per API process: 50 people with a few requests each need more than the default 15.
    db_pool_size: int = 20
    db_pool_overflow: int = 20

    auth_provider: str = "dev"  # dev | cognito
    dev_jwt_secret: str = "local-dev-secret-change-me-32-bytes-min"
    cognito_region: str = "ap-south-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    allowed_google_domain: str = ""

    s3_endpoint_url: str = ""
    s3_public_endpoint_url: str = ""
    s3_bucket: str = "relay-files"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "ap-south-1"
    # Local development without S3/MinIO: keep files in this folder and serve them through the
    # API's own signed /files/local/ links (base URL below). Ignored in production.
    local_storage_dir: str = ""
    local_storage_url: str = "http://localhost:8000"

    email_backend: str = "smtp"  # smtp | ses
    smtp_host: str = ""
    smtp_port: int = 1025
    email_from: str = "relay@localhost"

    cors_origins: str = "http://localhost:5173"

    usage_api_url: str = ""  # e.g. http://usage-api:8001 ; empty disables the hourly pull
    usage_api_secret: str = "dev-secret-change-me"

    # Phase 8 side track: Google Calendar/Gmail (separate, per-user opt-in OAuth grant --
    # not the same as Cognito's "Sign in with Google"). Empty client id/secret means the real
    # Google Cloud project isn't provisioned yet (confirmed with Samar); GoogleClient then runs
    # its local dev stand-in instead of calling Google, same pattern as capture's captcha hook.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:5173/settings/google/callback"
    # KMS key ARN for envelope-encrypting refresh tokens at rest. Empty = local dev Fernet key
    # below (never used in production; validate_for_start refuses it there).
    google_kms_key_id: str = ""
    google_token_encryption_key: str = "local-dev-only-32-byte-key-change-me!!"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def validate_for_start(settings: Settings) -> None:
    """Refuse to start with insecure development settings in production (BUG-P1-20)."""
    if not settings.is_production:
        return
    if settings.auth_provider != "cognito":
        raise RuntimeError("ENV=production requires AUTH_PROVIDER=cognito (dev provider refused)")
    if not settings.cognito_user_pool_id or not settings.cognito_client_id:
        raise RuntimeError("ENV=production requires COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID")
    if settings.google_client_id and not settings.google_kms_key_id:
        raise RuntimeError(
            "ENV=production with Google connected requires GOOGLE_KMS_KEY_ID "
            "(the local Fernet fallback key must never hold a real refresh token)"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
