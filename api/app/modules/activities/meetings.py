# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""What becomes of a booked meeting: held, postponed, or cancelled / no-show.

A customer gives a date and the rep books (locks) it. From then on the lead can only move past
the meeting stage when the meeting is really held. If it is postponed the old booking stays,
marked postponed with the reason, and a new booking replaces it; if it is cancelled (or the
customer does not turn up) the reason is kept and the lead needs a new meeting. Nothing is ever
erased, so the whole story of how the meeting came about is in the deal's history."""

import uuid
from typing import Any

from app.core.errors import Conflict, ValidationFailed
from app.core.time import ensure_utc, utc_now
from app.modules.activities import events
from app.modules.activities.models import Activity, ActivityVersion
from app.modules.activities.service import FUTURE_SLACK, _body, _load_activity, _refresh
from app.modules.deals.common import load_deal
from app.modules.deals.steps import replace_step
from app.modules.leads.models import Deal
from app.platform.context import Actor, RequestInfo
from app.platform.uow import UnitOfWork

KEY = "activity.log"  # whoever may log on the deal (its owner, their Team Leader, Admins)
STATUS_WORDS = {
    "held": "held",
    "postponed": "postponed",
    "cancelled": "cancelled",
    "no_show": "marked no-show",
}


async def _booked(uow: UnitOfWork, activity_id: uuid.UUID) -> tuple[Activity, Deal]:
    act = await _load_activity(uow, activity_id)
    if act.type != "meeting":
        raise Conflict("Only a meeting can be held, postponed or cancelled")
    deal = await load_deal(uow, act.deal_id, lock=True)
    await uow.authorize(KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
    if deal.status not in ("open", "won"):
        raise Conflict("This deal is not open")
    if act.meeting_status != "booked":
        word = STATUS_WORDS.get(act.meeting_status or "", "closed")
        raise Conflict(f"This meeting was already {word}: only a booked meeting can change")
    return act, deal


def _version(act: Activity, uow: UnitOfWork, reason: str | None) -> ActivityVersion:
    act.version_no, act.updated_at = act.version_no + 1, utc_now()
    return ActivityVersion(
        activity_id=act.id,
        version_no=act.version_no,
        body=_body(act),
        edited_by=uow.actor.id,
        reason=reason,
    )


async def mark_held(
    actor: Actor, request: RequestInfo, activity_id: uuid.UUID, body: Any
) -> uuid.UUID:
    """The meeting happened: record who came and what was discussed, and the next step."""
    if not body.discussed.strip():
        raise ValidationFailed("Write what was discussed", {"field": "discussed"})
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        act, deal = await _booked(uow, activity_id)
        cleaned = uow.reason(KEY, body.reason)
        when = ensure_utc(body.occurred_at) if body.occurred_at else min(act.occurred_at, utc_now())
        if when > utc_now() + FUTURE_SLACK:
            raise ValidationFailed(
                "A meeting that has not happened yet cannot be marked held",
                {"field": "occurred_at"},
            )
        attendees = [
            str(a).strip() for a in (act.attendees if body.attendees is None else body.attendees)
        ]
        attendees = [a for a in attendees if a]
        if not attendees:
            raise ValidationFailed("Add who attended the meeting", {"field": "attendees"})
        booked_for = act.occurred_at
        act.occurred_at, act.attendees, act.two_way = when, attendees, True
        act.discussed = body.discussed.strip()
        for name in ("requirements", "objections", "decisions", "actions"):
            setattr(act, name, getattr(body, name))
        act.meeting_status, act.status_at, act.status_by = "held", utc_now(), actor.id
        s.add(_version(act, uow, cleaned))
        await s.flush()
        await replace_step(
            uow,
            deal,
            text=body.next_step_text,
            due=body.next_step_due_date,
            responsible_id=body.next_step_responsible_id,
            lane=body.next_step_lane,
            assigned_by=actor.id if body.next_step_responsible_id not in (None, actor.id) else None,
        )
        await s.flush()
        await _refresh(uow, deal)
        await uow.audit(
            "activity.meeting_held",
            "deal",
            deal.id,
            old={"meeting_status": "booked", "booked_for": booked_for.isoformat()},
            new={"meeting_status": "held", "held_at": when.isoformat(), "attendees": attendees},
            reason=cleaned,
            note=f"activity {act.id.hex}",
        )
        await uow.emit(
            events.ACTIVITY_EDITED,
            {
                "deal_id": str(deal.id),
                "activity_id": str(act.id),
                "type": act.type,
                "occurred_at": act.occurred_at.isoformat(),
            },  # fmt: skip
        )
        return act.id


async def postpone(
    actor: Actor, request: RequestInfo, activity_id: uuid.UUID, body: Any
) -> uuid.UUID:
    """The date moved. Returns the NEW booking's id; the old one stays, marked postponed."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        act, deal = await _booked(uow, activity_id)
        cleaned = uow.reason(KEY, body.reason or body.why)
        new_at = ensure_utc(body.new_at)
        if new_at <= utc_now() + FUTURE_SLACK:
            raise ValidationFailed("The new date must be in the future", {"field": "new_at"})
        attendees = [
            str(a).strip() for a in (act.attendees if body.attendees is None else body.attendees)
        ]
        attendees = [a for a in attendees if a]
        if not attendees:
            raise ValidationFailed("Add who will attend the meeting", {"field": "attendees"})
        old_at = act.occurred_at
        act.meeting_status, act.status_at, act.status_by = "postponed", utc_now(), actor.id
        act.status_reason_code, act.status_reason = body.reason_code, body.why.strip()
        s.add(_version(act, uow, cleaned))
        new = Activity(
            org_id=deal.org_id,
            deal_id=deal.id,
            account_id=deal.account_id,
            type="meeting",
            occurred_at=new_at,
            two_way=False,
            attendees=attendees,
            discussed=act.discussed,  # the agenda carries over
            send_invite=act.send_invite,
            add_meet_link=act.add_meet_link,
            meeting_status="booked",
            rescheduled_from=act.id,
            created_by=actor.id,
            attributed_to=act.attributed_to,
        )
        s.add(new)
        await s.flush()
        s.add(
            ActivityVersion(
                activity_id=new.id,
                version_no=1,
                body=_body(new),
                edited_by=actor.id,
                reason=cleaned,
            )
        )
        await s.flush()
        await _refresh(uow, deal)
        await uow.audit(
            "activity.meeting_postponed",
            "deal",
            deal.id,
            old={"meeting_status": "booked", "booked_for": old_at.isoformat()},
            new={
                "meeting_status": "postponed",
                "new_date": new_at.isoformat(),
                "reason_code": body.reason_code,
                "why": body.why.strip(),
            },
            reason=cleaned,
            note=f"activity {act.id.hex} -> {new.id.hex}",
        )
        # the old booking leaves the calendar; the new one is announced like any new booking
        await uow.emit(
            events.ACTIVITY_DELETED,
            {"deal_id": str(deal.id), "activity_id": str(act.id), "type": "meeting"},
        )
        await uow.emit(
            events.ACTIVITY_LOGGED,
            {
                "deal_id": str(deal.id),
                "activity_id": str(new.id),
                "type": "meeting",
                "occurred_at": new_at.isoformat(),
            },  # fmt: skip
        )
        return new.id


async def cancel(
    actor: Actor, request: RequestInfo, activity_id: uuid.UUID, body: Any
) -> uuid.UUID:
    """It will not happen, or the customer did not turn up. The lead needs a new meeting."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        act, deal = await _booked(uow, activity_id)
        cleaned = uow.reason(KEY, body.reason or body.why)
        status = "no_show" if body.reason_code == "no_show" else "cancelled"
        old_at = act.occurred_at
        act.meeting_status, act.status_at, act.status_by = status, utc_now(), actor.id
        act.status_reason_code, act.status_reason = body.reason_code, body.why.strip()
        s.add(_version(act, uow, cleaned))
        await s.flush()
        await _refresh(uow, deal)  # a tick that relied on this meeting now shows "Proof missing"
        await uow.audit(
            "activity.meeting_cancelled",
            "deal",
            deal.id,
            old={"meeting_status": "booked", "booked_for": old_at.isoformat()},
            new={
                "meeting_status": status,
                "reason_code": body.reason_code,
                "why": body.why.strip(),
            },
            reason=cleaned,
            note=f"activity {act.id.hex}",
        )
        await uow.emit(
            events.ACTIVITY_DELETED,
            {"deal_id": str(deal.id), "activity_id": str(act.id), "type": "meeting"},
        )
        return act.id
