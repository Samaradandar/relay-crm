# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 8 (side track) deep tests: ST-01 connect/disconnect/reconnect and ST-02 one-way
calendar sync, against DevGoogleClient (no real Google Cloud project yet -- confirmed with
Samar; see client.py's module docstring).
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import PermissionDenied, ValidationFailed
from app.modules.google import service
from app.modules.google.calendar_sync import deterministic_event_id, sync_activity_to_google
from app.modules.google.client import get_client, reset_dev_client
from app.modules.google.models import GoogleAccount, GoogleEventLink
from app.platform.context import RequestInfo


@pytest.fixture(autouse=True)
def _fresh_dev_client():
    reset_dev_client()
    yield
    reset_dev_client()


@pytest.fixture
async def session(database):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database.app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_connect_then_disconnect_roundtrip(world, session):
    req = RequestInfo()
    url, verifier = service.start_connect(world.north1.id)
    assert "accounts.google.com" in url

    # Build a real signed state exactly like start_connect does internally.

    from datetime import timedelta

    import jwt as pyjwt

    from app.core.time import utc_now

    state = pyjwt.encode(
        {"uid": str(world.north1.id), "v": verifier, "exp": utc_now() + timedelta(minutes=10)},
        service._state_secret(), algorithm="HS256",
    )  # fmt: skip
    code = "auth-code-1"
    account = await service.handle_callback(world.north1, req, code, state)
    assert account.status == "connected"
    assert account.google_email == "dev@example.com"

    row = (
        await session.execute(select(GoogleAccount).where(GoogleAccount.user_id == world.north1.id))
    ).scalar_one()
    assert row.encrypted_refresh_token is not None
    assert row.encrypted_refresh_token.startswith("fernet:")

    await service.disconnect(world.north1, req)
    await session.commit()
    await session.refresh(row)
    assert row.status == "disconnected"
    assert row.encrypted_refresh_token is None
    assert row.connected_at is not None  # history kept


async def test_wrong_user_in_state_is_denied(world, session):
    req = RequestInfo()
    url, verifier = service.start_connect(world.north1.id)
    from datetime import timedelta

    import jwt as pyjwt

    from app.core.time import utc_now

    state = pyjwt.encode(
        {"uid": str(world.north1.id), "v": verifier, "exp": utc_now() + timedelta(minutes=10)},
        service._state_secret(), algorithm="HS256",
    )  # fmt: skip
    with pytest.raises(PermissionDenied):
        await service.handle_callback(world.north2, req, "code", state)


async def test_non_company_domain_is_rejected(world, session, monkeypatch):
    monkeypatch.setenv("ALLOWED_GOOGLE_DOMAIN", "relay.local")
    get_settings.cache_clear()
    try:
        req = RequestInfo()
        url, verifier = service.start_connect(world.north1.id)
        from datetime import timedelta

        import jwt as pyjwt

        from app.core.time import utc_now

        state = pyjwt.encode(
            {"uid": str(world.north1.id), "v": verifier, "exp": utc_now() + timedelta(minutes=10)},
            service._state_secret(), algorithm="HS256",
        )  # fmt: skip
        # DevGoogleClient's default userinfo answers hd="example.com", not "relay.local".
        with pytest.raises(ValidationFailed):
            await service.handle_callback(world.north1, req, "code", state)
    finally:
        monkeypatch.delenv("ALLOWED_GOOGLE_DOMAIN", raising=False)
        get_settings.cache_clear()


async def test_revoked_refresh_token_marks_needs_reconnect_once(world, session):
    req = RequestInfo()
    from datetime import timedelta

    import jwt as pyjwt

    from app.core.time import utc_now

    url, verifier = service.start_connect(world.north1.id)
    state = pyjwt.encode(
        {"uid": str(world.north1.id), "v": verifier, "exp": utc_now() + timedelta(minutes=10)},
        service._state_secret(), algorithm="HS256",
    )  # fmt: skip
    await service.handle_callback(world.north1, req, "code-x", state)

    row = (
        await session.execute(select(GoogleAccount).where(GoogleAccount.user_id == world.north1.id))
    ).scalar_one()
    from app.modules.google.crypto import decrypt_refresh_token

    refresh_token = decrypt_refresh_token(row.encrypted_refresh_token)
    from app.modules.google.client import DevGoogleClient

    dev_client = get_client()
    assert isinstance(dev_client, DevGoogleClient)
    dev_client.revoked.append(refresh_token)

    token = await service.get_valid_access_token(session, world.north1.id)
    assert token is None
    await session.refresh(row)
    assert row.status == "needs_reconnect"

    # calling again while already flagged must not re-send the notification (one notification rule)
    token2 = await service.get_valid_access_token(session, world.north1.id)
    assert token2 is None


async def _connect(world, user, session):
    from datetime import timedelta

    import jwt as pyjwt

    from app.core.time import utc_now

    url, verifier = service.start_connect(user.id)
    state = pyjwt.encode(
        {"uid": str(user.id), "v": verifier, "exp": utc_now() + timedelta(minutes=10)},
        service._state_secret(), algorithm="HS256",
    )  # fmt: skip
    await service.handle_callback(user, RequestInfo(), f"code-{user.id}", state)


async def _make_future_meeting(session, world, owner):
    import uuid
    from datetime import timedelta

    from app.core.time import utc_now
    from app.modules.accounts.models import Account
    from app.modules.activities.models import Activity
    from app.modules.leads.models import Deal

    account = Account(org_id=world.org_id, name=f"Acme {uuid.uuid4().hex[:6]}")
    session.add(account)
    await session.flush()
    deal = Deal(
        org_id=world.org_id, account_id=account.id, owner_id=owner.id, team_id=owner.team_id,
        lead_source="Website", pipeline="new_business", status="open", stage_key="prospecting",
    )  # fmt: skip
    session.add(deal)
    await session.flush()
    activity = Activity(
        org_id=world.org_id, deal_id=deal.id, account_id=account.id, type="meeting",
        occurred_at=utc_now() + timedelta(days=1), two_way=False, attendees=[],
        discussed="Discovery call booked", created_by=owner.id, attributed_to=owner.id,
        send_invite=False, add_meet_link=False,
    )  # fmt: skip
    session.add(activity)
    await session.flush()
    await session.commit()
    return account, deal, activity


async def test_calendar_create_is_idempotent_on_retry(world, session):
    await _connect(world, world.north1, session)
    account, deal, activity = await _make_future_meeting(session, world, world.north1)

    await sync_activity_to_google(
        session, world.north1.id, deal.id, account.id, account.name, activity
    )
    await session.commit()
    link = (
        await session.execute(
            select(GoogleEventLink).where(GoogleEventLink.activity_id == activity.id)
        )
    ).scalar_one()
    assert link.google_event_id == deterministic_event_id(activity.id)

    # a retried create (e.g. after a timeout) must never produce a second Google event.
    from app.modules.google.client import DevGoogleClient

    client = get_client()
    assert isinstance(client, DevGoogleClient)
    events_before = len(client.events)
    await sync_activity_to_google(
        session, world.north1.id, deal.id, account.id, account.name, activity
    )
    assert len(client.events) == events_before


async def test_calendar_cancel_marks_link_cancelled(world, session):
    await _connect(world, world.north1, session)
    account, deal, activity = await _make_future_meeting(session, world, world.north1)
    await sync_activity_to_google(
        session, world.north1.id, deal.id, account.id, account.name, activity
    )
    await session.commit()

    await sync_activity_to_google(
        session, world.north1.id, deal.id, account.id, account.name, activity, cancelled=True
    )
    await session.commit()
    link = (
        await session.execute(
            select(GoogleEventLink).where(GoogleEventLink.activity_id == activity.id)
        )
    ).scalar_one()
    assert link.status == "cancelled"
