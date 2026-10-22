# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Token checks behind an AuthProvider interface (Cognito in production, Dev locally).

Users must exist in the CRM first; the provider only proves who the caller is (an email).
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import jwt

from app.core.config import Settings, get_settings
from app.core.errors import Unauthenticated


@dataclass(frozen=True)
class Identity:
    email: str
    sub: str


class AuthProvider(Protocol):
    async def verify(self, token: str) -> Identity: ...
    async def start_password_reset(self, email: str) -> None: ...
    async def admin_reset_password(self, email: str) -> None: ...


class DevAuthProvider:
    """Local only: issues and checks HS256 tokens signed with DEV_JWT_SECRET."""

    def __init__(self, settings: Settings) -> None:
        if settings.is_production:
            raise RuntimeError("DevAuthProvider must never run with ENV=production")
        self._secret = settings.dev_jwt_secret
        self.sent_resets: list[tuple[str, str]] = []

    def issue(self, email: str, *, hours: int = 12) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": f"dev:{email.lower()}",
            "email": email.lower(),
            "iat": now,
            "exp": now + timedelta(hours=hours),
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")

    async def verify(self, token: str) -> Identity:
        try:
            # a token that never expires is refused, the same as the Cognito path
            claims = jwt.decode(
                token, self._secret, algorithms=["HS256"], options={"require": ["exp", "iat"]}
            )
        except jwt.PyJWTError as exc:
            raise Unauthenticated("Invalid or expired token") from exc
        if not isinstance(claims.get("email"), str) or not isinstance(claims.get("sub"), str):
            raise Unauthenticated("Invalid or expired token")
        return Identity(email=claims["email"], sub=claims["sub"])

    async def start_password_reset(self, email: str) -> None:
        self.sent_resets.append(("self", email))

    async def admin_reset_password(self, email: str) -> None:
        self.sent_resets.append(("admin", email))


class CognitoProvider:
    """Verifies Cognito JWTs against the user pool JWKS (cached by PyJWKClient)."""

    def __init__(self, settings: Settings) -> None:
        if not (settings.cognito_user_pool_id and settings.cognito_client_id):
            raise RuntimeError("COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID are required")
        self._s = settings
        self._issuer = (
            f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
            f"{settings.cognito_user_pool_id}"
        )
        self._jwks = jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json", cache_keys=True)

    def _decode(self, token: str) -> dict[str, object]:
        key = self._jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=self._issuer,
            audience=self._s.cognito_client_id,
            options={"require": ["exp", "iat", "sub"]},
        )

    async def verify(self, token: str) -> Identity:
        try:
            claims = await asyncio.to_thread(self._decode, token)
        except jwt.PyJWTError as exc:
            raise Unauthenticated("Invalid or expired token") from exc
        if claims.get("token_use") != "id":
            raise Unauthenticated("Send the Cognito ID token")
        email = str(claims.get("email", "")).lower()
        if not email or claims.get("email_verified") is False:
            raise Unauthenticated("Email not verified")
        domain = self._s.allowed_google_domain.lower()
        if domain and not email.endswith("@" + domain):
            raise Unauthenticated("Sign in with your company Google account")
        return Identity(email=email, sub=str(claims["sub"]))

    def _client(self) -> Any:
        import boto3

        return boto3.client("cognito-idp", region_name=self._s.cognito_region)

    async def start_password_reset(self, email: str) -> None:
        await asyncio.to_thread(
            self._client().forgot_password, ClientId=self._s.cognito_client_id, Username=email
        )

    async def admin_reset_password(self, email: str) -> None:
        # Cognito emails the user a reset code; no password is ever shown to an admin.
        await asyncio.to_thread(
            self._client().admin_reset_user_password,
            UserPoolId=self._s.cognito_user_pool_id,
            Username=email,
        )


@lru_cache
def get_auth_provider() -> AuthProvider:
    settings = get_settings()
    if settings.auth_provider == "cognito":
        return CognitoProvider(settings)
    return DevAuthProvider(settings)
