# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 8 (side track) deep tests: ST-03 two-way calendar sync (loop prevention, 410 recovery,
cancel/move, suggestions) and ST-04 Gmail logging (two-way detection, idempotency, privacy).
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import PermissionDenied
from app.core.time import utc_now
from app.modules.google import gmail
from app.modules.google.client import (
    DevGoogleClient,
    GmailMessage,
    GmailThread,
    get_client,
    reset_dev_client,
)
from app.modules.google.incremental_sync import run_incremental_sync
from app.modules.google.models import CalendarSuggestion, CalendarSyncState, GoogleEventLink
from app.platform.context import RequestInfo

from .test_01_google import _connect, _make_future_meeting  # reuse the Phase8 fixtures/helpers


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


def _dev() -> DevGoogleClient:
    c = get_client()
    assert isinstance(c, DevGoogleClient)
    return c


async def test_time_change_in_google_updates_activity_and_notes_timeline(world, session):
    from app.modules.google.calendar_sync import sync_activity_to_google

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
    # Outside the 2-minute loop-prevention window -- a real external change, not our own echo.
    link.last_synced_at = utc_now() - timedelta(minutes=5)
    await session.commit()
    new_start = (utc_now() + timedelta(days=2)).isoformat()
    _dev().incoming_changes.append(
        {
            "id": link.google_event_id,
            "etag": "new-etag-from-google",
            "status": "confirmed",
            "start": {"dateTime": new_start},
            "extendedProperties": {
                "private": {"relayActivityId": str(activity.id), "relayDealId": str(deal.id)}
            },
        }
    )
    applied = await run_incremental_sync(session, world.north1.id, world.org_id)
    await session.commit()
    assert applied == 1

    from app.modules.activities.models import Activity

    refreshed = await session.get(Activity, activity.id)
    assert refreshed.occurred_at.isoformat() == new_start

    from app.modules.leads.models import Deal

    refreshed_deal = await session.get(Deal, deal.id)
    assert refreshed_deal.flags.get("google_calendar_notes")


async def test_echoed_own_push_is_not_reapplied(world, session):
    from app.modules.google.calendar_sync import sync_activity_to_google

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
    # Google echoing back exactly the etag we just wrote -- our own push, not a real change.
    _dev().incoming_changes.append(
        {
            "id": link.google_event_id,
            "etag": link.etag,
            "status": "confirmed",
            "extendedProperties": {
                "private": {"relayActivityId": str(activity.id), "relayDealId": str(deal.id)}
            },
        }
    )
    applied = await run_incremental_sync(session, world.north1.id, world.org_id)
    assert applied == 0


async def test_cancelled_in_google_marks_link_and_notifies(world, session):
    from app.modules.google.calendar_sync import sync_activity_to_google

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
    link.last_synced_at = utc_now() - timedelta(minutes=5)
    await session.commit()
    _dev().incoming_changes.append(
        {
            "id": link.google_event_id,
            "etag": "cancel-etag",
            "status": "cancelled",
            "extendedProperties": {
                "private": {"relayActivityId": str(activity.id), "relayDealId": str(deal.id)}
            },
        }
    )
    applied = await run_incremental_sync(session, world.north1.id, world.org_id)
    await session.commit()
    assert applied == 1
    await session.refresh(link)
    assert link.status == "cancelled"


async def test_410_gone_triggers_full_resync_without_raising(world, session):
    await _connect(world, world.north1, session)
    state = CalendarSyncState(
        org_id=world.org_id,
        user_id=world.north1.id,
        calendar_id="primary",
        sync_token="stale-token",
    )
    session.add(state)
    await session.commit()
    _dev().invalid_sync_token = "stale-token"

    applied = await run_incremental_sync(session, world.north1.id, world.org_id)
    assert applied == 0  # no changes queued, but no exception either
    await session.refresh(state)
    assert state.last_full_sync_at is not None
    assert state.sync_token is not None  # a fresh token was stored


async def test_unlinked_event_with_deal_contact_becomes_a_suggestion_not_a_link(world, session):
    await _connect(world, world.north1, session)
    account, deal, _activity = await _make_future_meeting(session, world, world.north1)
    from app.modules.accounts.models import Contact

    contact_email = "meera@example.com"
    session.add(
        Contact(
            org_id=world.org_id, account_id=account.id, name="Meera Sharma", email=contact_email
        )
    )
    await session.commit()

    _dev().incoming_changes.append(
        {
            "id": "evt-unlinked-1",
            "etag": "e1",
            "status": "confirmed",
            "attendees": [contact_email],
            "summary": "Catch up",
        }
    )
    await run_incremental_sync(session, world.north1.id, world.org_id)
    await session.commit()

    suggestion = (
        await session.execute(
            select(CalendarSuggestion).where(CalendarSuggestion.user_id == world.north1.id)
        )
    ).scalar_one()
    assert suggestion.status == "open"
    assert suggestion.deal_id is None  # never auto-linked


async def test_gmail_two_way_thread_logs_and_is_idempotent(world, session):
    await _connect(world, world.north1, session)
    account, deal, _activity = await _make_future_meeting(session, world, world.north1)
    from app.modules.accounts.models import Contact

    contact_email = "buyer@acme.test"
    session.add(
        Contact(org_id=world.org_id, account_id=account.id, name="Buyer", email=contact_email)
    )
    await session.commit()

    client = _dev()
    client.gmail_threads["t1"] = GmailThread(
        id="t1",
        subject="Pricing",
        participants=[contact_email],
        date="2024-01-01",
        snippet="...",
        message_count=2,
    )
    client.gmail_messages["t1"] = [
        GmailMessage(
            id="m1",
            from_email=world.north1.email,
            to_emails=[contact_email],
            cc_emails=[],
            sent_at="2024-01-01",
            body_text="Here is pricing",
            attachments=[],
        ),
        GmailMessage(
            id="m2",
            from_email=contact_email,
            to_emails=[world.north1.email],
            cc_emails=[],
            sent_at="2024-01-02",
            body_text="Thanks, looks good",
            attachments=[],
        ),
    ]

    threads = await gmail.search_threads(session, world.north1, deal.id, None)
    assert len(threads) == 1
    # search_threads records last_success_at via a flush on this shared session; commit it
    # before log_threads opens its own session on the same GoogleAccount row, or the second
    # session blocks forever waiting for a lock this one never released.
    await session.commit()

    req = RequestInfo()
    ids = await gmail.log_threads(
        world.north1,
        req,
        deal.id,
        ["t1"],
        {"discussed": "Pricing sent", "reason": "Logged from Gmail"},
    )
    assert len(ids) == 1

    from app.modules.activities.models import Activity

    activity = await session.get(Activity, ids[0])
    assert activity.two_way is True

    # logging again is a no-op -- same activity, no duplicate messages.
    ids_again = await gmail.log_threads(
        world.north1,
        req,
        deal.id,
        ["t1"],
        {"discussed": "Pricing sent", "reason": "Logged from Gmail"},
    )
    assert ids_again == ids
    from app.modules.google.models import GmailLoggedMessage

    count = (
        await session.execute(
            select(GmailLoggedMessage.id).where(GmailLoggedMessage.user_id == world.north1.id)
        )
    ).all()
    assert len(count) == 2  # not 4


async def test_rep_cannot_search_another_reps_gmail(world, session):
    await _connect(world, world.north1, session)
    account, deal, _activity = await _make_future_meeting(session, world, world.north1)
    await session.commit()
    with pytest.raises(PermissionDenied):
        await gmail.search_threads(session, world.north2, deal.id, None)
