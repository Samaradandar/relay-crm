# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Activities and structured interaction notes (PRD 11 Feature 7).

Logging an activity also sets the deal's next step, bumps `last_activity_at` and re-checks any
exit-criterion proof. Editing is for your own activity only (24 hours for a User or Team Leader);
the original and every edited version are kept. Deleting turns dependent ticks into 'Proof missing'.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.time import ensure_utc, utc_now
from app.modules.activities import events
from app.modules.activities.models import (
    DEAD_MEETING_STATUSES,
    TWO_WAY_TYPES,
    Activity,
    ActivityVersion,
)
from app.modules.deals import health
from app.modules.deals.common import ensure_live_deal, load_deal
from app.modules.deals.steps import open_step, replace_step
from app.modules.leads.events import DEAL_UPDATED
from app.modules.leads.models import Deal
from app.modules.leads.ownership import company_name, deal_rt, names
from app.modules.pipeline.engine.service import recheck_proofs
from app.platform.context import Actor, RequestInfo
from app.platform.notifications import service as notifications
from app.platform.permissions.roles import Role
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

MEETING_SCHEDULED_TYPE = "activity.meeting_scheduled"
EDIT_WINDOW = timedelta(hours=24)
FUTURE_SLACK = timedelta(minutes=5)
EDITABLE = (
    "occurred_at",
    "two_way",
    "attendees",
    "discussed",
    "requirements",
    "objections",
    "decisions",
    "actions",
)


def _body(a: Activity) -> dict[str, Any]:
    return {
        "type": a.type,
        "occurred_at": a.occurred_at.isoformat(),
        "two_way": a.two_way,
        "attendees": a.attendees,
        "discussed": a.discussed,
        "requirements": a.requirements,
        "objections": a.objections,
        "decisions": a.decisions,
        "actions": a.actions,
        "meeting_status": a.meeting_status,
    }


async def _last_activity(session: AsyncSession, deal_id: uuid.UUID) -> datetime | None:
    """Latest activity that has happened (a meeting booked for later does not count)."""
    rows = (
        await session.execute(
            select(Activity.occurred_at)
            .where(
                Activity.deal_id == deal_id,
                Activity.deleted_at.is_(None),
                Activity.occurred_at <= utc_now(),
                Activity.type != "note",
            )
            .order_by(Activity.occurred_at.desc())
            .limit(1)
        )
    ).first()
    return rows[0] if rows else None


async def _refresh(uow: UnitOfWork, deal: Deal) -> None:
    s = uow.session
    deal.last_activity_at = await _last_activity(s, deal.id)
    health.apply(deal, await open_step(s, deal.id))
    deal.version, deal.updated_at = deal.version + 1, utc_now()
    await recheck_proofs(uow, deal)
    await uow.emit(DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def log_activity(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, body: Any
) -> uuid.UUID:
    when = ensure_utc(body.occurred_at) if body.occurred_at else utc_now()
    if when > utc_now() + FUTURE_SLACK and body.type != "meeting":
        raise ValidationFailed(
            "Only a meeting can be dated in the future", {"field": "occurred_at"}
        )
    if body.type in TWO_WAY_TYPES and body.two_way is None:
        raise ValidationFailed("Say whether the customer responded", {"field": "two_way"})
    if not body.discussed.strip():
        raise ValidationFailed("Write what was discussed", {"field": "discussed"})
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("activity.log", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("activity.log", body.reason)
        if deal.status not in ("open", "won"):
            raise Conflict("Activities are logged on open deals")
        act = Activity(
            org_id=deal.org_id,
            deal_id=deal.id,
            account_id=deal.account_id,
            type=body.type,
            occurred_at=when,
            two_way=bool(body.two_way) if body.type in TWO_WAY_TYPES else False,
            attendees=[a.strip() for a in body.attendees if a.strip()],
            discussed=body.discussed.strip(),
            requirements=body.requirements,
            objections=body.objections,
            decisions=body.decisions,
            actions=body.actions,
            send_invite=bool(body.send_invite) if body.type == "meeting" else False,
            add_meet_link=bool(body.add_meet_link) if body.type == "meeting" else False,
            meeting_status=(
                ("booked" if when > utc_now() + FUTURE_SLACK else "held")
                if body.type == "meeting"
                else None
            ),
            created_by=actor.id,
            attributed_to=actor.id,
        )
        s.add(act)
        await s.flush()
        s.add(
            ActivityVersion(
                activity_id=act.id,
                version_no=1,
                body=_body(act),
                edited_by=actor.id,
                reason=cleaned,
            )
        )
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
            "activity.log",
            "deal",
            deal.id,
            new={
                "activity": act.id.hex,
                "type": act.type,
                "when": when.isoformat(),
                "next_step": body.next_step_text,
            },
            reason=cleaned,
        )
        company = await company_name(s, deal)
        await uow.emit(
            events.ACTIVITY_LOGGED,
            {
                "deal_id": str(deal.id),
                "company": company,
                "activity_id": str(act.id),
                "type": act.type,
                "occurred_at": when.isoformat(),
            },  # fmt: skip
        )
        is_future_meeting = act.type == "meeting" and when > utc_now()
        if is_future_meeting and deal.owner_id and deal.owner_id != actor.id:
            # Minimal version (PRD 11, P5-08): no calendar integration in this build, and
            # attendees are free text, not user references, so only the owner can be notified --
            # "and attendee users" from the PRD table is not reachable without that integration.
            await notifications.notify(
                s,
                deal.org_id,
                [deal.owner_id],
                MEETING_SCHEDULED_TYPE,
                "Meeting scheduled",
                f"A meeting was scheduled for {company} on {when.isoformat()}.",
                f"/deals/{deal.id}",
                dedupe_key=f"{MEETING_SCHEDULED_TYPE}:{act.id}",
            )
        return act.id


async def _load_activity(uow: UnitOfWork, activity_id: uuid.UUID) -> Activity:
    act = (
        await uow.session.execute(
            select(Activity)
            .where(
                Activity.id == activity_id,
                Activity.org_id == uow.actor.org_id,
                Activity.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if act is None:
        raise NotFound("Activity not found")
    return act


async def edit_activity(
    actor: Actor, request: RequestInfo, activity_id: uuid.UUID, patch: Any
) -> uuid.UUID:
    sent = patch.model_fields_set - {"reason"}
    if not sent:
        raise ValidationFailed("Nothing to change")
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        act = await _load_activity(uow, activity_id)
        deal = await load_deal(uow, act.deal_id, lock=True)
        await uow.authorize("activity.edit_own", entity=("deal", deal.id), owner_id=deal.owner_id)
        if act.created_by != actor.id:  # nobody edits someone else's activity, not even an Admin
            raise PermissionDenied("You can only edit your own activity")
        if act.meeting_status in DEAD_MEETING_STATUSES:
            raise Conflict(f"This meeting was {act.meeting_status.replace('_', ' ')}: it is closed")
        if act.meeting_status == "booked" and "occurred_at" in sent:
            raise ValidationFailed(
                "To change a booked meeting's date, postpone it and give the reason",
                {"field": "occurred_at"},
            )
        if actor.role in (Role.USER, Role.TEAM_LEADER) and utc_now() - act.created_at > EDIT_WINDOW:
            raise PermissionDenied("The 24-hour window for editing this activity has passed")
        cleaned = uow.reason("activity.edit_own", patch.reason)
        before = _body(act)
        for name in EDITABLE:
            if name in sent:
                value = getattr(patch, name)
                if name == "occurred_at":
                    if value is None:
                        raise ValidationFailed("The date cannot be emptied", {"field": name})
                    value = ensure_utc(value)
                    if value > utc_now() + FUTURE_SLACK and (
                        act.type != "meeting" or act.meeting_status == "held"
                    ):
                        raise ValidationFailed("A past activity cannot be dated in the future")
                if name == "discussed" and not (value or "").strip():
                    raise ValidationFailed("Write what was discussed", {"field": name})
                if name == "two_way" and value is None:
                    raise ValidationFailed("Say whether the customer responded", {"field": name})
                setattr(act, name, value)
        after = _body(act)
        changed = {k: (before[k], after[k]) for k in after if before[k] != after[k]}
        if not changed:
            return act.id
        act.version_no, act.updated_at = act.version_no + 1, utc_now()
        s.add(
            ActivityVersion(
                activity_id=act.id,
                version_no=act.version_no,
                body=after,
                edited_by=actor.id,
                reason=cleaned,
            )
        )
        await s.flush()
        await _refresh(uow, deal)
        await uow.audit(
            "activity.edit_own",
            "deal",
            deal.id,
            old={k: v[0] for k, v in changed.items()},
            new={k: v[1] for k, v in changed.items()},
            reason=cleaned,
            note=f"activity {act.id.hex} v{act.version_no}",
        )
        if act.type == "meeting" and ("occurred_at" in changed or "attendees" in changed):
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


async def delete_activity(
    actor: Actor, request: RequestInfo, activity_id: uuid.UUID, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        act = await _load_activity(uow, activity_id)
        deal = await load_deal(uow, act.deal_id, lock=True)
        await uow.authorize("activity.delete", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("activity.delete", reason)
        act.deleted_at, act.deleted_reason = utc_now(), cleaned
        await s.flush()
        await _refresh(uow, deal)
        await uow.audit(
            "activity.delete",
            "deal",
            deal.id,
            old=_body(act),
            reason=cleaned,
            note=f"activity {act.id.hex}",
        )
        if act.type == "meeting":
            await uow.emit(
                events.ACTIVITY_DELETED,
                {"deal_id": str(deal.id), "activity_id": str(act.id), "type": act.type},
            )


# ---------------------------------------------------------------------------- reading
async def _out(session: AsyncSession, rows: list[Activity]) -> list[dict[str, Any]]:
    who = await names(
        session,
        {a.created_by for a in rows}
        | {a.attributed_to for a in rows}
        | {a.status_by for a in rows if a.status_by},
    )
    # Phase 8 (side track): a meeting's Google Calendar link, if the owner is connected. Local
    # import: activities (Phase 3) has no other reason to depend on the google module.
    calendar_by_activity: dict[uuid.UUID, dict[str, Any]] = {}
    meeting_ids = [a.id for a in rows if a.type == "meeting"]
    if meeting_ids:
        from app.modules.google.models import GoogleEventLink

        links = (
            await session.execute(
                select(GoogleEventLink).where(GoogleEventLink.activity_id.in_(meeting_ids))
            )
        ).scalars().all()  # fmt: skip
        calendar_by_activity = {
            link.activity_id: {"status": link.status, "html_link": link.html_link} for link in links
        }
    return [
        {
            "calendar": calendar_by_activity.get(a.id),
            "meeting_status": a.meeting_status,
            "status_reason_code": a.status_reason_code,
            "status_reason": a.status_reason,
            "status_at": a.status_at,
            "status_by_name": who.get(a.status_by) if a.status_by else None,
            "rescheduled_from": a.rescheduled_from,
            "id": a.id,
            "deal_id": a.deal_id,
            "type": a.type,
            "occurred_at": a.occurred_at,
            "two_way": a.two_way,
            "attendees": a.attendees,
            "discussed": a.discussed,
            "requirements": a.requirements,
            "objections": a.objections,
            "decisions": a.decisions,
            "actions": a.actions,
            "version_no": a.version_no,
            "created_by": a.created_by,
            "created_by_name": who.get(a.created_by),
            "attributed_to_name": who.get(a.attributed_to),
            "created_at": a.created_at,
            "edited": a.version_no > 1,
            "deleted": a.deleted_at is not None,
        }
        for a in rows
    ]


async def list_for_deal(
    session: AsyncSession, actor: Actor, deal_id: uuid.UUID, limit: int = 200
) -> list[dict[str, Any]]:
    await authorize_read(session, actor, "deal.view", ("deal", deal_id))
    await ensure_live_deal(session, actor, deal_id)
    rows = list(
        (
            await session.execute(
                select(Activity)
                .where(
                    Activity.deal_id == deal_id,
                    Activity.org_id == actor.org_id,
                    Activity.deleted_at.is_(None),
                )
                .order_by(Activity.occurred_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return await _out(session, rows)


async def versions_of(
    session: AsyncSession, actor: Actor, activity_id: uuid.UUID
) -> list[dict[str, Any]]:
    act = await session.get(Activity, activity_id)
    if act is None or act.org_id != actor.org_id:
        raise NotFound("Activity not found")
    await authorize_read(session, actor, "deal.view", ("deal", act.deal_id))
    await ensure_live_deal(session, actor, act.deal_id)
    rows = list(
        (
            await session.execute(
                select(ActivityVersion)
                .where(ActivityVersion.activity_id == activity_id)
                .order_by(ActivityVersion.version_no)
            )
        ).scalars()
    )
    who = await names(session, {r.edited_by for r in rows})
    return [
        {
            "version_no": r.version_no,
            "body": r.body,
            "edited_by_name": who.get(r.edited_by),
            "edited_at": r.edited_at,
            "reason": r.reason,
        }
        for r in rows
    ]
