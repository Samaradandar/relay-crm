# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""ST-02: Relay -> Google, one-way. Deterministic event IDs (base32hex of the activity's UUID,
Google's own documented scheme) mean a retried create can never produce a second event -- a 409
just means "already there", handled by GoogleClient.create_event itself.
"""

import base64
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import to_ist, utc_now
from app.modules.accounts.models import Contact
from app.modules.activities.models import Activity
from app.modules.google.client import GoogleError, get_client
from app.modules.google.models import GoogleEventLink
from app.modules.google.service import get_valid_access_token, mark_needs_reconnect

DEFAULT_DURATION_MINUTES = 30


def deterministic_event_id(activity_id: uuid.UUID) -> str:
    """base32hex of the UUID's bytes, lowercase, no padding -- Google's documented scheme for a
    client-supplied event id (letters a-v and digits 0-9 only, 5-1024 chars)."""
    raw = base64.b32hexencode(activity_id.bytes).decode().lower().rstrip("=")
    return raw


async def _attendee_emails(
    session: AsyncSession, account_id: uuid.UUID, names: list[str]
) -> list[str]:
    if not names:
        return []
    rows = (
        await session.execute(
            select(Contact.name, Contact.email).where(
                Contact.account_id == account_id, Contact.email.is_not(None)
            )
        )
    ).all()
    by_name = {n: e for n, e in rows if e}
    return [by_name[n] for n in names if n in by_name]


def _event_body(
    activity: Activity, company: str, deal_id: uuid.UUID, attendee_emails: list[str]
) -> dict[str, Any]:
    start_ist = to_ist(activity.occurred_at)
    end_ist = start_ist + timedelta(minutes=DEFAULT_DURATION_MINUTES)
    agenda_first_line = (
        (activity.discussed or "").splitlines()[0] if activity.discussed else "Meeting"
    )
    body: dict[str, Any] = {
        "summary": f"Relay · {company}: {agenda_first_line}"[:250],
        "description": f"{activity.discussed or ''}\n\nhttps://app.relay.local/app/deals/{deal_id}",
        "start": {"dateTime": start_ist.isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end_ist.isoformat(), "timeZone": "Asia/Kolkata"},
        "attendees": [{"email": e} for e in attendee_emails],
        "extendedProperties": {
            "private": {"relayActivityId": str(activity.id), "relayDealId": str(deal_id)}
        },
        "_send_updates": bool(activity.send_invite),
    }
    if activity.add_meet_link:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": str(activity.id),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    return body


async def sync_activity_to_google(
    session: AsyncSession,
    deal_owner_id: uuid.UUID,
    deal_id: uuid.UUID,
    account_id: uuid.UUID,
    company: str,
    activity: Activity,
    *,
    cancelled: bool = False,
) -> None:
    """Create, update or cancel the owner's calendar event for this (future) meeting activity.
    A no-op when the owner isn't connected -- calendar sync is additive, never blocking."""
    token = await get_valid_access_token(session, deal_owner_id)
    if token is None:
        return
    client = get_client()
    event_id = deterministic_event_id(activity.id)
    link = (
        await session.execute(
            select(GoogleEventLink).where(GoogleEventLink.activity_id == activity.id)
        )
    ).scalar_one_or_none()

    if cancelled:
        if link is None or link.status == "cancelled":
            return
        try:
            await client.cancel_event(token, link.calendar_id, link.google_event_id, link.etag)
        except GoogleError as e:
            if e.invalid_grant or e.status == 401:
                await _needs_reconnect(session, deal_owner_id, str(e))
                return
            raise
        link.status, link.last_direction, link.last_synced_at = "cancelled", "to_google", utc_now()
        await session.flush()
        return

    attendee_emails = await _attendee_emails(session, account_id, activity.attendees)
    body = _event_body(activity, company, deal_id, attendee_emails)
    try:
        if link is None:
            event = await client.create_event(token, "primary", event_id, body)
        else:
            event = await client.update_event(
                token, link.calendar_id, link.google_event_id, body, link.etag
            )
    except GoogleError as e:
        if e.invalid_grant or e.status == 401:
            await _needs_reconnect(session, deal_owner_id, str(e))
            return
        raise

    if link is None:
        link = GoogleEventLink(
            org_id=activity.org_id, activity_id=activity.id, deal_id=deal_id, user_id=deal_owner_id,
            calendar_id="primary", google_event_id=event.id, created_at=utc_now(),
        )  # fmt: skip
        session.add(link)
    link.etag, link.html_link, link.last_direction = event.etag, event.html_link, "to_google"
    link.last_synced_at, link.status = (
        utc_now(),
        "active" if event.status != "cancelled" else "cancelled",
    )
    if event.hangout_link:
        activity.meet_link = event.hangout_link
    await session.flush()


async def _needs_reconnect(session: AsyncSession, user_id: uuid.UUID, error: str) -> None:
    from app.modules.google.models import GoogleAccount

    account = (
        await session.execute(select(GoogleAccount).where(GoogleAccount.user_id == user_id))
    ).scalar_one_or_none()
    if account is not None:
        await mark_needs_reconnect(session, account, error)


async def move_event_to_new_owner(
    session: AsyncSession,
    activity: Activity,
    deal_id: uuid.UUID,
    account_id: uuid.UUID,
    company: str,
    old_owner_id: uuid.UUID | None,
    new_owner_id: uuid.UUID,
) -> None:
    """A deal's owner changed: cancel the old owner's event (if any), create one for the new
    owner (if connected). Both are notified by the caller (see handlers.py)."""
    if old_owner_id is not None:
        await sync_activity_to_google(
            session, old_owner_id, deal_id, account_id, company, activity, cancelled=True
        )
        old_link = (
            await session.execute(
                select(GoogleEventLink).where(GoogleEventLink.activity_id == activity.id)
            )
        ).scalar_one_or_none()
        if old_link is not None:
            await session.delete(old_link)
            await session.flush()
    await sync_activity_to_google(session, new_owner_id, deal_id, account_id, company, activity)
