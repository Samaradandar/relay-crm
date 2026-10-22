# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The proposal loop (owner decision, 17 Oct 2026).

In the Proposal stage the rep holds a walkthrough meeting and logs it, then records the customer's
answer. If the customer wants changes (or raises objections) the rep writes down what they asked
for: that round is closed, its ticks are cleared, and a NEW meeting must be held to revise the
proposal before the next answer. The loop goes on until the customer accepts; only then is the
proposal drafted and sent. Every round, with what was asked, stays on the deal.

A rejection is not a round: it is a lost deal (Mark lost, with the reason)."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.time import ensure_utc, utc_now
from app.modules.activities.models import Activity, ActivityVersion
from app.modules.activities.service import _body
from app.modules.deals.common import load_deal
from app.modules.leads import events as lead_events
from app.modules.leads.models import Deal
from app.modules.leads.ownership import deal_rt, names
from app.modules.pipeline.engine.models import DealCriterion
from app.modules.pipeline.engine.service import current_entry, recheck_proofs
from app.modules.proposals.models import ProposalRound
from app.platform.context import Actor, RequestInfo
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

KEY = "criterion.tick"  # recording the customer's answer is the owner's job, like a tick
VIEW_KEY = "deal.view"
LABELS = {
    "accepted": "Accepted",
    "changes_requested": "Changes requested",
    "objections": "Objections",
}
# what a closed round takes away again: the next round has to earn these once more
ROUND_TICKS = ("walkthrough_logged", "feedback_recorded", "proposal_emailed")


async def last_closed_at(session: Any, deal_id: uuid.UUID) -> datetime | None:
    """When the latest round was closed: a meeting only counts for the NEXT round if it was held
    after this."""
    found: datetime | None = (
        await session.execute(
            select(func.max(ProposalRound.closed_at)).where(ProposalRound.deal_id == deal_id)
        )
    ).scalar_one()
    return found


async def _meeting_for_round(session: Any, deal: Deal, held: bool) -> Activity | None:
    """The held (or booked) meeting that counts for the round in hand. Meetings from before the deal
    entered Proposal (the Meeting scheduled stage) never count: the walkthrough is a NEW meeting,
    logged in this stage."""
    deal_id = deal.id
    q = select(Activity).where(
        Activity.deal_id == deal_id,
        Activity.created_at >= deal.stage_entered_at,
        Activity.type == "meeting",
        Activity.meeting_status == ("held" if held else "booked"),
        Activity.deleted_at.is_(None),
    )
    cutoff = await last_closed_at(session, deal_id)
    if held and cutoff is not None:
        q = q.where(Activity.occurred_at > cutoff)
    if not held and cutoff is not None:
        q = q.where(Activity.created_at >= cutoff)
    q = q.order_by(Activity.occurred_at.desc() if held else Activity.occurred_at.asc()).limit(1)
    found: Activity | None = (await session.execute(q)).scalar_one_or_none()
    return found


def _meeting_out(a: Activity | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "id": a.id,
        "occurred_at": a.occurred_at,
        "attendees": a.attendees,
        "discussed": a.discussed,
    }


async def list_rounds(session: Any, actor: Actor, deal_id: uuid.UUID) -> dict[str, Any]:
    await authorize_read(session, actor, VIEW_KEY, entity=("deal", deal_id))
    deal = await session.get(Deal, deal_id)
    if deal is None:
        raise NotFound("Deal not found")
    rows = list(
        (
            await session.execute(
                select(ProposalRound)
                .where(ProposalRound.deal_id == deal_id)
                .order_by(ProposalRound.round_no)
            )
        ).scalars()
    )
    meetings = {
        a.id: a
        for a in (
            await session.execute(
                select(Activity).where(
                    Activity.id.in_({r.meeting_id for r in rows if r.meeting_id})
                )
            )
        ).scalars()
    }
    who = await names(session, {r.recorded_by for r in rows if r.recorded_by})
    open_row = next((r for r in reversed(rows) if r.closed_at is None), None)
    return {
        "rounds": [
            {
                "id": r.id,
                "round_no": r.round_no,
                "feedback": r.feedback,
                "changes": r.changes,
                "recorded_by_name": who.get(r.recorded_by) if r.recorded_by else None,
                "recorded_at": r.recorded_at,
                "closed_at": r.closed_at,
                "meeting": _meeting_out(meetings.get(r.meeting_id)) if r.meeting_id else None,
            }
            for r in rows
        ],
        "current_round_no": (
            rows[-1].round_no if open_row else (rows[-1].round_no + 1 if rows else 1)
        ),
        "held_meeting": _meeting_out(await _meeting_for_round(session, deal, True)),
        "booked_meeting": _meeting_out(await _meeting_for_round(session, deal, False)),
        "accepted": bool(open_row and open_row.feedback == "accepted"),
    }


async def record_feedback(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, body: Any
) -> None:
    changes = (body.changes or "").strip()
    if body.feedback != "accepted" and len(changes) < 3:
        raise ValidationFailed(
            "Write what the customer asked for" if body.feedback == "changes_requested"
            else "Write the customer's objections",
            {"field": "changes"},
        )  # fmt: skip
    next_at = ensure_utc(body.next_meeting_at) if body.next_meeting_at else None
    if body.feedback != "accepted":
        if next_at is None:
            raise ValidationFailed(
                "Book the next meeting: its date and time", {"field": "next_meeting_at"}
            )
        if next_at <= utc_now() + timedelta(minutes=5):
            raise ValidationFailed(
                "The next meeting has to be in the future", {"field": "next_meeting_at"}
            )
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize(KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(KEY, body.reason)
        if deal.status != "open" or deal.stage_key != "proposal":
            raise Conflict("The customer's feedback is recorded while the deal is in Proposal")
        rows = list(
            (
                await s.execute(
                    select(ProposalRound)
                    .where(ProposalRound.deal_id == deal.id)
                    .order_by(ProposalRound.round_no)
                    .with_for_update()
                )
            ).scalars()
        )
        if any(r.closed_at is None and r.feedback == "accepted" for r in rows) and (
            body.feedback == "accepted"
        ):
            raise Conflict("The customer has already accepted this proposal")
        meeting = await _meeting_for_round(s, deal, True)
        if meeting is None:
            raise ValidationFailed(
                "Log the meeting that was held first (the walkthrough, or the meeting to revise "
                "the proposal): the customer's feedback is about that meeting",
                {"field": "meeting"},
            )
        now = utc_now()
        row = ProposalRound(
            org_id=deal.org_id,
            deal_id=deal.id,
            round_no=(rows[-1].round_no + 1) if rows else 1,
            meeting_id=meeting.id,
            feedback=body.feedback,
            changes=changes or None,
            recorded_by=actor.id,
            closed_at=None if body.feedback == "accepted" else now,
        )
        s.add(row)
        data = dict(deal.data or {})
        data["feedback"] = LABELS[body.feedback]
        if changes:
            data["requested_changes"] = changes  # what the Revision column on the board shows
        deal.data = data
        cleared: list[str] = []
        if body.feedback != "accepted":
            for r in rows:  # an accepted round that is still open is superseded by this one
                if r.closed_at is None:
                    r.closed_at = now
            entry = await current_entry(s, deal.id, lock=True)
            for tick in (
                await s.execute(
                    select(DealCriterion).where(
                        DealCriterion.entry_id == entry.id,
                        DealCriterion.criterion_key.in_(ROUND_TICKS),
                    )
                )
            ).scalars():
                cleared.append(tick.criterion_key)
                await s.delete(tick)
        booked: Activity | None = None
        if body.feedback != "accepted" and next_at is not None:
            # the meeting to revise the proposal is booked together with the changes
            for old in (
                await s.execute(
                    select(Activity).where(
                        Activity.deal_id == deal.id,
                        Activity.type == "meeting",
                        Activity.meeting_status == "booked",
                        Activity.deleted_at.is_(None),
                    )
                )
            ).scalars():  # an older booking would be mistaken for the new one
                old.meeting_status, old.status_at, old.status_by = "cancelled", now, actor.id
                old.status_reason_code = "other"
                old.status_reason = "Replaced by the meeting booked with the customer's changes"
            booked = Activity(
                org_id=deal.org_id,
                deal_id=deal.id,
                account_id=deal.account_id,
                type="meeting",
                occurred_at=next_at,
                attendees=[],
                discussed=(
                    ("Revise the proposal: " if body.feedback == "changes_requested"
                     else "Address the objections: ") + changes
                ),
                meeting_status="booked",
                created_at=now,
                created_by=actor.id,
                attributed_to=actor.id,
            )  # fmt: skip
            s.add(booked)
            await s.flush()
            s.add(
                ActivityVersion(
                    activity_id=booked.id,
                    version_no=1,
                    body=_body(booked),
                    edited_by=actor.id,
                    reason=cleaned,
                )
            )
        await s.flush()
        await recheck_proofs(uow, deal)
        deal.version, deal.updated_at = deal.version + 1, now
        await uow.audit(
            "proposal.feedback",
            "deal",
            deal.id,
            new={
                "round": row.round_no,
                "feedback": body.feedback,
                "changes": changes or None,
                "ticks_cleared": cleared,
                "next_meeting": booked.occurred_at.isoformat() if booked else None,
            },
            reason=cleaned,
        )
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
