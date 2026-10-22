# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""ST-01: connect/disconnect/reconnect. The OAuth `state` carries the PKCE `code_verifier` and
the connecting user's id itself (HMAC-signed, short-lived JWT) instead of server-side session
storage -- there's no cache/session infra in this app to put it in, and a signed, expiring token
is exactly as safe against CSRF/replay as a server-side nonce would be.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import PermissionDenied, ValidationFailed
from app.core.time import utc_now
from app.modules.google import events
from app.modules.google.client import GoogleError, get_client
from app.modules.google.crypto import decrypt_refresh_token, encrypt_refresh_token
from app.modules.google.models import GoogleAccount
from app.platform.context import Actor, RequestInfo
from app.platform.notifications import service as notifications
from app.platform.uow import UnitOfWork

CONNECT_KEY = "google.connect"  # own-settings action; no reason needed (PRD)
STATE_TTL_MINUTES = 10


def _state_secret() -> str:
    s = get_settings()
    return s.google_client_secret or s.google_token_encryption_key


def start_connect(user_id: uuid.UUID) -> tuple[str, str]:
    """Returns (authorize_url, code_verifier_cookie_value) -- the verifier also round-trips in
    the signed state, so the caller only needs to keep the URL; the second value exists for
    callers that prefer a cookie instead (none do yet)."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        hashlib.sha256(verifier.encode()).digest().hex()
    )  # Google wants base64url of the raw digest, built next line
    import base64

    challenge_b64 = base64.urlsafe_b64encode(bytes.fromhex(challenge)).decode().rstrip("=")
    expires_at = utc_now() + timedelta(minutes=STATE_TTL_MINUTES)
    state = jwt.encode(
        {"uid": str(user_id), "v": verifier, "exp": expires_at}, _state_secret(), algorithm="HS256"
    )
    url = get_client().build_auth_url(state, challenge_b64)
    return url, verifier


def _decode_state(state: str) -> tuple[uuid.UUID, str]:
    try:
        body = jwt.decode(state, _state_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise ValidationFailed("This Google sign-in link has expired. Try connecting again.") from e
    return uuid.UUID(body["uid"]), body["v"]


async def handle_callback(
    actor: Actor, request: RequestInfo, code: str, state: str
) -> GoogleAccount:
    """Opens its own UnitOfWork (not a session passed in) since it's the only write here."""
    uid, verifier = _decode_state(state)
    if uid != actor.id:
        raise PermissionDenied("This sign-in link was issued to a different person")
    client = get_client()
    try:
        tokens = await client.exchange_code(code, verifier)
        info = await client.get_userinfo(tokens.access_token)
    except GoogleError as e:
        raise ValidationFailed(f"Google sign-in failed: {e}") from e

    settings = get_settings()
    allowed_domain = settings.allowed_google_domain
    if allowed_domain and (info.hd or "").lower() != allowed_domain.lower():
        raise ValidationFailed(
            f"Connect your {allowed_domain} Google account, not a personal one.",
            {"field": "email"},
        )

    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(CONNECT_KEY)
        existing = (
            await s.execute(select(GoogleAccount).where(GoogleAccount.user_id == actor.id))
        ).scalar_one_or_none()
        encrypted = encrypt_refresh_token(tokens.refresh_token) if tokens.refresh_token else None
        if existing is None:
            account = GoogleAccount(
                org_id=actor.org_id, user_id=actor.id, google_email=info.email,
                granted_scopes=tokens.scopes, encrypted_refresh_token=encrypted,
                status="connected", connected_at=utc_now(), last_success_at=utc_now(),
            )  # fmt: skip
            s.add(account)
        else:
            account = existing
            account.google_email = info.email
            account.granted_scopes = tokens.scopes
            if encrypted:
                account.encrypted_refresh_token = encrypted
            account.status, account.connected_at = "connected", utc_now()
            account.last_success_at, account.last_error = utc_now(), None
            account.disconnected_at = None
        await s.flush()
        await uow.audit(CONNECT_KEY, "google_account", account.id, new={"google_email": info.email})
        await uow.emit(events.GOOGLE_CONNECTED, {"user_id": str(actor.id)})
        return account


async def status(session: AsyncSession, actor: Actor) -> dict[str, Any] | None:
    account = (
        await session.execute(select(GoogleAccount).where(GoogleAccount.user_id == actor.id))
    ).scalar_one_or_none()
    if account is None:
        return None
    return {
        "google_email": account.google_email, "status": account.status,
        "connected_at": account.connected_at, "last_success_at": account.last_success_at,
        "last_error": account.last_error,
    }  # fmt: skip


async def disconnect(actor: Actor, request: RequestInfo) -> None:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        account = (
            await s.execute(select(GoogleAccount).where(GoogleAccount.user_id == actor.id))
        ).scalar_one_or_none()
        if account is None or account.status == "disconnected":
            return
        await uow.authorize(CONNECT_KEY)
        assert actor.id is not None
        from app.modules.google.incremental_sync import stop_channel_for_user

        await stop_channel_for_user(s, actor.id)  # before revoking -- it still needs a token
        if account.encrypted_refresh_token:
            try:
                await get_client().revoke(decrypt_refresh_token(account.encrypted_refresh_token))
            except GoogleError:
                pass  # already revoked at Google, or Google is unreachable -- delete locally anyway
        account.status, account.disconnected_at = "disconnected", utc_now()
        account.encrypted_refresh_token = (
            None  # history (connected_at etc) is kept; the secret is not
        )
        await s.flush()
        await uow.audit(CONNECT_KEY, "google_account", account.id, new={"status": "disconnected"})
        await uow.emit(events.GOOGLE_DISCONNECTED, {"user_id": str(actor.id)})


async def get_valid_access_token(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    """None when the user isn't connected or needs to reconnect -- callers (calendar/gmail sync)
    must treat that as "nothing to do", never as an error to retry forever."""
    account = (
        await session.execute(select(GoogleAccount).where(GoogleAccount.user_id == user_id))
    ).scalar_one_or_none()
    if account is None or account.status != "connected" or not account.encrypted_refresh_token:
        return None
    try:
        tokens = await get_client().refresh_access_token(
            decrypt_refresh_token(account.encrypted_refresh_token)
        )
    except GoogleError as e:
        if e.invalid_grant or e.status == 401:
            await mark_needs_reconnect(session, account, str(e))
        return None
    account.last_success_at = utc_now()
    await session.flush()
    return tokens.access_token


async def mark_needs_reconnect(session: AsyncSession, account: GoogleAccount, error: str) -> None:
    if account.status == "needs_reconnect":
        account.last_error = error
        await session.flush()
        return  # already flagged -- the one-notification rule below only fires on the transition
    account.status, account.last_error = "needs_reconnect", error
    await session.flush()
    await notifications.notify(
        session, account.org_id, [account.user_id], events.GOOGLE_NEEDS_RECONNECT,
        "Reconnect Google",
        "Your Google connection needs to be renewed before meetings keep syncing.",
        "/profile",
        dedupe_key=f"google.needs_reconnect:{account.id}",
    )  # fmt: skip
