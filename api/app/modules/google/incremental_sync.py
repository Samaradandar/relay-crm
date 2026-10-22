# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""ST-03: Google -> Relay. Only Relay-linked events (carrying relayActivityId) change Relay
data; an unlinked event whose attendees include a deal contact's email becomes a suggestion,
never an automatic link. Loop prevention: a change Relay itself pushed and Google echoed back is
dropped when the returned etag matches what we stored, or the link's last_direction was
"to_google" within the last 2 minutes.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import to_ist, utc_now
from app.modules.accounts.models import Contact
from app.modules.activities.models import Activity
from app.modules.google.client import GoogleError, GoogleEvent, get_client
from app.modules.google.models import CalendarSuggestion, CalendarSyncState, GoogleEventLink
from app.modules.google.service import get_valid_access_token, mark_needs_reconnect
from app.platform.notifications import service as notifications

LOOP_WINDOW = timedelta(minutes=2)
FULL_SYNC_PAST_DAYS = 30
FULL_SYNC_FUTURE_DAYS = 90
MEETING_ENDED_TYPE = "google.meeting_ended"
MEETING_CANCELLED_TYPE = "google.meeting_cancelled_in_google"
MEETING_MOVED_TYPE = "google.meeting_moved_in_google"


WEBHOOK_PATH = "/integrations/google/calendar/push"
CHANNEL_RENEW_WINDOW = timedelta(hours=24)


async def subscribe_channel(
    session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, base_url: str
) -> bool:
    """Opens (or renews) a push-notification channel for this user's primary calendar. Returns
    False when not connected -- never raises for that case, same as sync itself."""
    import secrets
    from datetime import datetime

    token = await get_valid_access_token(session, user_id)
    if token is None:
        return False
    state = (
        await session.execute(
            select(CalendarSyncState).where(
                CalendarSyncState.user_id == user_id, CalendarSyncState.calendar_id == "primary"
            )
        )
    ).scalar_one_or_none()
    if state is None:
        state = CalendarSyncState(org_id=org_id, user_id=user_id, calendar_id="primary")
        session.add(state)
        await session.flush()
    channel_id, channel_token = str(uuid.uuid4()), secrets.token_urlsafe(24)
    try:
        result = await get_client().watch_calendar(
            token, state.calendar_id, channel_id, channel_token, base_url.rstrip("/") + WEBHOOK_PATH
        )
    except GoogleError as e:
        if e.invalid_grant or e.status == 401:
            await _needs_reconnect(session, user_id, str(e))
        return False
    state.channel_id, state.channel_token, state.resource_id = (
        channel_id,
        channel_token,
        result.resource_id,
    )
    state.channel_expiry = datetime.fromtimestamp(
        int(result.expiration) / 1000, tz=utc_now().tzinfo
    )
    await session.flush()
    return True


async def renew_expiring_channels(session: AsyncSession, org_id: uuid.UUID, base_url: str) -> int:
    cutoff = utc_now() + CHANNEL_RENEW_WINDOW
    states = (
        (
            await session.execute(
                select(CalendarSyncState).where(
                    CalendarSyncState.org_id == org_id,
                    (CalendarSyncState.channel_expiry.is_(None))
                    | (CalendarSyncState.channel_expiry < cutoff),
                )
            )
        )
        .scalars()
        .all()
    )
    renewed = 0
    for state in states:
        if await subscribe_channel(session, state.user_id, org_id, base_url):
            renewed += 1
    return renewed


async def stop_channel_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Stop channels on disconnect (PRD)."""
    state = (
        await session.execute(
            select(CalendarSyncState).where(
                CalendarSyncState.user_id == user_id, CalendarSyncState.calendar_id == "primary"
            )
        )
    ).scalar_one_or_none()
    if state is None or not state.channel_id:
        return
    token = await get_valid_access_token(session, user_id)
    if token is not None and state.resource_id:
        try:
            await get_client().stop_channel(token, state.channel_id, state.resource_id)
        except GoogleError:
            pass
    state.channel_id, state.channel_token, state.resource_id, state.channel_expiry = (
        None,
        None,
        None,
        None,
    )
    await session.flush()


async def run_incremental_sync(session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> int:
    """Returns the number of Google changes applied. A no-op (0) when not connected."""
    token = await get_valid_access_token(session, user_id)
    if token is None:
        return 0
    state = (
        await session.execute(
            select(CalendarSyncState).where(
                CalendarSyncState.user_id == user_id, CalendarSyncState.calendar_id == "primary"
            )
        )
    ).scalar_one_or_none()
    if state is None:
        state = CalendarSyncState(org_id=org_id, user_id=user_id, calendar_id="primary")
        session.add(state)
        await session.flush()

    client = get_client()
    try:
        page = await client.list_events(token, state.calendar_id, state.sync_token, None)
    except GoogleError as e:
        if e.status == 410:
            # Section 5: 410 Gone -> full resync of the window, then store the fresh token.
            time_min = (utc_now() - timedelta(days=FULL_SYNC_PAST_DAYS)).isoformat()
            page = await client.list_events(token, state.calendar_id, None, time_min)
            state.last_full_sync_at = utc_now()
        elif e.invalid_grant or e.status == 401:
            await _needs_reconnect(session, user_id, str(e))
            return 0
        else:
            raise

    applied = 0
    for event in page.events:
        extended = page.extended.get(event.id, {})
        applied += await _apply_change(session, user_id, org_id, event, extended)
    state.sync_token, state.last_push_at = page.next_sync_token, utc_now()
    await session.flush()
    return applied


async def _needs_reconnect(session: AsyncSession, user_id: uuid.UUID, error: str) -> None:
    from app.modules.google.models import GoogleAccount

    account = (
        await session.execute(select(GoogleAccount).where(GoogleAccount.user_id == user_id))
    ).scalar_one_or_none()
    if account is not None:
        await mark_needs_reconnect(session, account, error)


def _recently_pushed(link: GoogleEventLink, event: GoogleEvent) -> bool:
    if link.etag and link.etag == event.etag:
        return True  # Google echoed exactly what we sent -- not a real external change
    if link.last_direction == "to_google" and link.last_synced_at:
        return utc_now() - link.last_synced_at < LOOP_WINDOW
    return False


async def _apply_change(
    session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, event: GoogleEvent,
    extended: dict[str, Any],
) -> int:  # fmt: skip
    activity_id = extended.get("relayActivityId")
    if not activity_id:
        await _maybe_suggest(session, user_id, org_id, event)
        return 0

    link = (
        await session.execute(
            select(GoogleEventLink).where(
                GoogleEventLink.google_event_id == event.id, GoogleEventLink.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if link is None or link.status == "cancelled" and event.status == "cancelled":
        return 0
    if _recently_pushed(link, event):
        return 0  # loop prevention: this is our own push, echoed back

    activity = await session.get(Activity, link.activity_id)
    if activity is None:
        return 0
    deal_id = link.deal_id

    if event.status == "cancelled":
        if link.status != "cancelled":
            link.status = "cancelled"
            await _timeline_note(session, org_id, deal_id, "Meeting cancelled in Google Calendar.")
            await notifications.notify(
                session,
                org_id,
                [user_id],
                MEETING_CANCELLED_TYPE,
                "A meeting was cancelled in Google",
                "Set a new next step for this deal.",
                f"/deals/{deal_id}",
                dedupe_key=f"{MEETING_CANCELLED_TYPE}:{activity.id}",
            )
        link.etag, link.last_direction, link.last_synced_at = event.etag, "from_google", utc_now()
        await session.flush()
        return 1

    if event.start and event.start != to_ist(activity.occurred_at).isoformat():
        from datetime import datetime

        try:
            new_time = datetime.fromisoformat(event.start)
        except ValueError:
            new_time = None
        if new_time is not None:
            activity.occurred_at = new_time
            await _timeline_note(
                session, org_id, deal_id, f"Meeting moved in Google Calendar to {event.start}."
            )
            await notifications.notify(
                session,
                org_id,
                [user_id],
                MEETING_MOVED_TYPE,
                "A meeting moved in Google",
                f"New time: {event.start}",
                f"/deals/{deal_id}",
                dedupe_key=f"{MEETING_MOVED_TYPE}:{activity.id}:{event.updated}",
            )
    link.etag, link.last_direction, link.last_synced_at = event.etag, "from_google", utc_now()
    await session.flush()
    return 1


async def _timeline_note(
    session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID, text: str
) -> None:
    """A lightweight audit-trail note (see capture/service.py's `_handle_duplicate` for the same
    pattern) -- not a full Activity, since nothing was actually discussed."""
    from app.modules.leads.models import Deal

    deal = await session.get(Deal, deal_id)
    if deal is None:
        return
    flags = dict(deal.flags or {})
    flags.setdefault("google_calendar_notes", []).append(f"{text} ({utc_now().isoformat()})")
    deal.flags = flags
    await session.flush()


async def _maybe_suggest(
    session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, event: GoogleEvent
) -> CalendarSuggestion | None:
    """An unlinked Google event. If any attendee matches a deal contact's email, offer it as a
    suggestion -- never link it automatically."""
    if not event.attendees:
        return None
    contact = (
        (await session.execute(select(Contact).where(Contact.email.in_(event.attendees))))
        .scalars()
        .first()
    )
    if contact is None:
        return None
    existing = (
        await session.execute(
            select(CalendarSuggestion).where(
                CalendarSuggestion.user_id == user_id,
                CalendarSuggestion.google_event_id == event.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    start = None
    if event.start:
        from datetime import datetime

        try:
            start = datetime.fromisoformat(event.start)
        except ValueError:
            start = None
    suggestion = CalendarSuggestion(
        org_id=org_id,
        user_id=user_id,
        google_event_id=event.id,
        deal_id=None,
        contact_email=contact.email,
        summary=event.summary,
        starts_at=start,
        status="open",
        created_at=utc_now(),
    )
    session.add(suggestion)
    await session.flush()
    return suggestion


async def link_suggestion(
    session: AsyncSession,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    deal_id: uuid.UUID,
) -> None:
    suggestion = await session.get(CalendarSuggestion, suggestion_id)
    if suggestion is None or suggestion.user_id != user_id or suggestion.status != "open":
        return
    suggestion.status, suggestion.deal_id = "linked", deal_id
    suggestion.decided_by, suggestion.decided_at = user_id, utc_now()
    await session.flush()


async def ignore_suggestion(
    session: AsyncSession, user_id: uuid.UUID, suggestion_id: uuid.UUID
) -> None:
    suggestion = await session.get(CalendarSuggestion, suggestion_id)
    if suggestion is None or suggestion.user_id != user_id or suggestion.status != "open":
        return
    suggestion.status = "ignored"
    suggestion.decided_by, suggestion.decided_at = user_id, utc_now()
    await session.flush()
