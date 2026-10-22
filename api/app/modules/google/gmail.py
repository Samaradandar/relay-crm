# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""ST-04: Gmail logging. Only the connected rep can search or browse their own mailbox -- there
is no admin mailbox search. Nothing is stored until the rep picks a thread; unlogged emails
never touch the database.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, PermissionDenied
from app.core.time import utc_now
from app.modules.accounts.models import Contact
from app.modules.activities.models import Activity
from app.modules.google.client import GoogleError, get_client
from app.modules.google.models import GmailLoggedMessage, GmailLoggedThread
from app.modules.google.service import get_valid_access_token
from app.modules.leads.models import Deal
from app.platform.context import Actor, RequestInfo
from app.platform.uow import UnitOfWork

LOG_KEY = "gmail.log"


async def _deal_and_owner_check(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> Deal:
    deal = await session.get(Deal, deal_id)
    if deal is None:
        raise NotFound("Deal not found")
    if actor.id is None or deal.owner_id != actor.id:
        raise PermissionDenied("Only the deal's owner can search their own Gmail")
    return deal


async def search_threads(
    session: AsyncSession, actor: Actor, deal_id: uuid.UUID, query: str | None
) -> list[dict[str, Any]]:
    deal = await _deal_and_owner_check(session, actor, deal_id)
    assert actor.id is not None
    token = await get_valid_access_token(session, actor.id)
    if token is None:
        return []
    contacts = (
        (
            await session.execute(
                select(Contact.email).where(
                    Contact.account_id == deal.account_id, Contact.email.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    built = " OR ".join(f"from:{e} OR to:{e}" for e in contacts) if contacts else ""
    q = query or f"({built}) newer_than:90d" if built else (query or "")
    try:
        threads = await get_client().search_threads(token, q)
    except GoogleError:
        return []
    return [
        {
            "id": t.id,
            "subject": t.subject,
            "participants": t.participants,
            "date": t.date,
            "snippet": t.snippet,
            "message_count": t.message_count,
        }
        for t in threads
    ]


def _is_two_way(messages: list[Any], contact_emails: set[str], rep_email: str) -> bool:
    from_contact = any(m.from_email in contact_emails for m in messages)
    from_rep = any(m.from_email == rep_email or rep_email in m.from_email for m in messages)
    return from_contact and from_rep


async def log_threads(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, thread_ids: list[str],
    notes: dict[str, Any],
) -> list[uuid.UUID]:  # fmt: skip
    """Each picked thread becomes one Email activity. Idempotent: a message already logged for
    this user is a no-op (unique on (user_id, gmail_message_id))."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await _deal_and_owner_check(s, actor, deal_id)
        assert actor.id is not None
        await uow.authorize(LOG_KEY)
        cleaned = uow.reason(LOG_KEY, notes.get("reason"))
        token = await get_valid_access_token(s, actor.id)
        if token is None:
            raise PermissionDenied("Connect Google first")
        contacts = (
            (
                await s.execute(
                    select(Contact.email).where(
                        Contact.account_id == deal.account_id, Contact.email.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        contact_emails = {c for c in contacts if c}

        activity_ids: list[uuid.UUID] = []
        client = get_client()
        for thread_id in thread_ids:
            existing = (
                await s.execute(
                    select(GmailLoggedThread).where(
                        GmailLoggedThread.user_id == actor.id,
                        GmailLoggedThread.gmail_thread_id == thread_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                activity_ids.append(existing.activity_id)
                continue
            try:
                messages = await client.get_thread_messages(token, thread_id)
            except GoogleError:
                continue
            if not messages:
                continue
            two_way = _is_two_way(messages, contact_emails, actor.email or "")
            activity = Activity(
                org_id=actor.org_id,
                deal_id=deal.id,
                account_id=deal.account_id,
                type="email",
                occurred_at=utc_now(),
                two_way=two_way,
                attendees=[],
                discussed=notes.get("discussed", ""),
                requirements=notes.get("requirements"),
                objections=notes.get("objections"),
                decisions=notes.get("decisions"),
                actions=notes.get("actions"),
                created_by=actor.id,
                attributed_to=actor.id,
            )
            s.add(activity)
            await s.flush()
            thread_row = GmailLoggedThread(
                org_id=actor.org_id,
                deal_id=deal.id,
                activity_id=activity.id,
                user_id=actor.id,
                gmail_thread_id=thread_id,
                subject=None,
                logged_at=utc_now(),
                logged_by=actor.id,
            )
            s.add(thread_row)
            await s.flush()
            for m in messages:
                exists = (
                    await s.execute(
                        select(GmailLoggedMessage.id).where(
                            GmailLoggedMessage.user_id == actor.id,
                            GmailLoggedMessage.gmail_message_id == m.id,
                        )
                    )
                ).first()
                if exists:
                    continue
                s.add(
                    GmailLoggedMessage(
                        org_id=actor.org_id,
                        thread_row_id=thread_row.id,
                        gmail_message_id=m.id,
                        user_id=actor.id,
                        from_email=m.from_email,
                        to_emails=m.to_emails,
                        cc_emails=m.cc_emails,
                        sent_at=utc_now(),
                        body_text=m.body_text,
                        attachments=[],
                    )
                )
            await s.flush()
            activity_ids.append(activity.id)
        await uow.audit(LOG_KEY, "deal", deal.id, new={"threads": thread_ids}, reason=cleaned)
        return activity_ids


async def refresh_thread(
    session: AsyncSession, actor: Actor, deal_id: uuid.UUID, thread_id: str
) -> int:
    """Manual check for newer messages -- never automatic (PRD). Returns how many new messages
    were found (the caller still decides whether to actually log them)."""
    await _deal_and_owner_check(session, actor, deal_id)
    assert actor.id is not None
    thread_row = (
        await session.execute(
            select(GmailLoggedThread).where(
                GmailLoggedThread.user_id == actor.id,
                GmailLoggedThread.gmail_thread_id == thread_id,
            )
        )
    ).scalar_one_or_none()
    if thread_row is None:
        raise NotFound("This thread hasn't been logged yet")
    token = await get_valid_access_token(session, actor.id)
    if token is None:
        return 0
    known = set(
        (
            await session.execute(
                select(GmailLoggedMessage.gmail_message_id).where(
                    GmailLoggedMessage.thread_row_id == thread_row.id
                )
            )
        ).scalars()
    )
    try:
        messages = await get_client().get_thread_messages(token, thread_id)
    except GoogleError:
        return 0
    return sum(1 for m in messages if m.id not in known)


async def remove_logged_thread(
    actor: Actor, request: RequestInfo, thread_row_id: uuid.UUID, reason: str | None
) -> None:
    """Soft delete -- audit keeps the metadata, not the body (PRD). Within 24h for the rep who
    logged it; a TL (team, reason) or Admin (reason) any time."""
    from datetime import timedelta

    from app.core.errors import PermissionDenied as _PD
    from app.platform.permissions.roles import Role

    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        row = await s.get(GmailLoggedThread, thread_row_id)
        if row is None:
            raise NotFound("Not found")
        is_owner_within_window = (
            row.logged_by == actor.id and utc_now() - row.logged_at <= timedelta(hours=24)
        )
        if not (
            is_owner_within_window or actor.role in (Role.TEAM_LEADER, Role.ADMIN, Role.SUPER_ADMIN)
        ):
            raise _PD("You can only remove your own logged email within 24 hours")
        await uow.authorize(LOG_KEY)
        cleaned = uow.reason(LOG_KEY, reason)
        row.deleted_at, row.deleted_reason = utc_now(), cleaned
        await s.flush()
        await uow.audit(
            LOG_KEY, "gmail_logged_thread", row.id, new={"deleted": True}, reason=cleaned
        )
