# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Notifications: in-app rows plus an email event per recipient. Required types cannot be
switched off. Dedupe keys make the outbox's at-least-once delivery safe."""

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.users.models import User
from app.platform.notifications.models import Notification, NotificationSetting
from app.platform.outbox import service as outbox

REQUIRED_TYPES = frozenset(
    {
        "approval.requested",
        "approval.decided",
        "approval.reminder",
        "user.invited",
        "user.team_changed",
        "user.role_changed",
        # PRD 11 / P5-08: money alerts and churn risk cannot be switched off either.
        "billing.payment_due",
        "billing.payment_overdue",
        "billing.low_balance",
        "billing.run_out",
        "billing.overage",
        "churn.risk",
        "incentive.set_rate",
        "incentive.approved",
    }
)

# Catalog for the notification-settings screen (P5-08): every type `notify()` can fire, with a
# human label. A type not in REQUIRED_TYPES can be switched off per user, per PRD, on the email
# channel only -- the in-app bell row is never suppressed (see NotificationSetting.in_app, which
# this PR's settings screen does not expose a toggle for).
NOTIFICATION_TYPES: dict[str, str] = {
    "approval.requested": "Approval requested",
    "approval.decided": "Approval decided",
    "approval.reminder": "Approval still waiting (reminder)",
    "user.invited": "You were added to Relay CRM",
    "user.team_changed": "Your team changed",
    "user.role_changed": "Your role changed",
    "lead.assigned": "New lead assigned",
    "lead.waiting": "Lead waiting in the Pool, no eligible rep",
    "lead.rep_waiting": "A rep's lead needs a decision",
    "lead.rep_waiting.creator": "Your lead is waiting for your Team Leader",
    "deal.owner_changed": "A deal's owner changed",
    "incentive.set_rate": "Onboarding done: set the incentive",
    "incentive.pending": "Your deal is onboarded: your incentive is being set",
    "incentive.approved": "Your incentive was approved",
    "duplicate.created_anyway_outside_scope": "Possible duplicate created",
    "transfer.requested": "Transfer request involving your team",
    "transfer.commented": "New comment on a transfer request",
    "transfer.decided": "Transfer decided",
    "criterion.rejected": "A tick was rejected",
    "deal.review_needed": "A deal needs review",
    "deal.proof_missing": "Proof is missing on a deal",
    "deal.overridden": "Your deal was moved with an override",
    "deal.won": "Deal won",
    "deal.lost": "Deal lost",
    "deal.reopened": "A lost deal was reopened",
    "task.assigned": "A task was assigned to you",
    "nudge.due": "Customer has not responded (Nudge due)",
    "deal.stale": "Deal gone stale",
    "deal.followup_due": "Follow-up due",
    "activity.meeting_scheduled": "Meeting scheduled",
    "proposal.revision_required": "Proposal needs revision",
    "proposal.pending": "Proposal pending customer feedback",
    "billing.low_balance": "Minute balance is low",
    "billing.run_out": "Minutes ran out",
    "billing.overage": "Unbilled overage recorded",
    "billing.payment_due": "Payment due",
    "billing.payment_overdue": "Payment overdue",
    "billing.awaiting_confirmation": "Subscription awaiting confirmation",
    "billing.contract_approaching_end": "Contract approaching end",
    "billing.contract_ended_no_renewal": "Contract ended without renewal",
    "churn.risk": "Customer at risk of churn",
    "usage.sync_failing": "Usage sync is failing",
    "audit.chain_broken": "Audit trail integrity problem",
}


async def notify(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_ids: Iterable[uuid.UUID],
    type_: str,
    title: str,
    body: str = "",
    link: str | None = None,
    *,
    dedupe_key: str | None = None,
    in_app: bool = True,
) -> int:
    """Create notifications. Returns how many were created (skips duplicates and inactive users)."""
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return 0
    users = {
        u.id: u
        for u in (
            await session.execute(
                select(User).where(
                    User.id.in_(ids), User.org_id == org_id, User.is_active.is_(True)
                )
            )
        ).scalars()
    }
    prefs = {
        p.user_id: p
        for p in (
            await session.execute(
                select(NotificationSetting).where(
                    NotificationSetting.user_id.in_(ids), NotificationSetting.type == type_
                )
            )
        ).scalars()
    }
    required = type_ in REQUIRED_TYPES
    created = 0
    for uid in ids:
        user = users.get(uid)
        if user is None:
            continue
        pref = prefs.get(uid)
        want_app = in_app and (required or pref is None or pref.in_app)
        want_email = required or pref is None or pref.email
        fresh = True
        if not want_app and want_email and dedupe_key is not None:
            # In-app off: the e-mail is deduped on its own (an outbox retry must not mail twice).
            mail_key = f"{uid}:{dedupe_key}"
            seen = await session.execute(
                text(
                    "SELECT 1 FROM outbox_events WHERE org_id = :o AND type = 'email.send' "
                    "AND payload->>'dedupe' = :k LIMIT 1"
                ),
                {"o": org_id, "k": mail_key},
            )
            fresh = seen.first() is None
        if want_app:
            stmt = (
                pg_insert(Notification)
                .values(
                    org_id=org_id,
                    user_id=uid,
                    type=type_,
                    title=title,
                    body=body,
                    link=link,
                    dedupe_key=dedupe_key,
                )
                .on_conflict_do_nothing()
                .returning(Notification.id)
            )
            fresh = (await session.execute(stmt)).first() is not None
        if not fresh:
            continue  # already notified (outbox retry): do not email twice either
        created += 1
        if want_email:
            await outbox.enqueue(
                session,
                org_id,
                "email.send",
                {
                    "to": user.email,
                    "subject": title,
                    "body": body,
                    "link": link,
                    **({"dedupe": f"{uid}:{dedupe_key}"} if dedupe_key is not None else {}),
                },
            )
    return created


async def list_for_user(
    session: AsyncSession, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return list(
        (
            await session.execute(stmt.order_by(Notification.created_at.desc()).limit(limit))
        ).scalars()
    )


async def unread_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            )
        ).scalar_one()
    )


async def mark_read(session: AsyncSession, user_id: uuid.UUID, ids: list[uuid.UUID] | None) -> int:
    stmt = update(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    res = await session.execute(stmt.values(read_at=utc_now()))
    return int(getattr(res, "rowcount", 0) or 0)
