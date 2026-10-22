# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox handlers that keep rep_month_stats live (P6-01) and generate a new team's colour
(P6-03). Idempotent: recompute is a pure read-and-overwrite of one rep/month, safe to run twice.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.leads import events as lead_events
from app.modules.leads.handlers import _leader
from app.modules.leads.models import Deal
from app.modules.performance import events as perf_events
from app.modules.performance import service as perf_service
from app.modules.performance import stats
from app.modules.performance.colours import generate_colour
from app.modules.pipeline.engine import events as engine_events
from app.modules.users import events as user_events
from app.modules.users.models import Team
from app.platform.notifications import service as notifications
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import enqueue, on_event


async def _broadcast_leaderboard(session: AsyncSession, org_id: uuid.UUID) -> None:
    await enqueue(
        session,
        org_id,
        perf_events.STATS_RECOMPUTED,
        {
            "rt": {
                "entity_type": "leaderboard",
                "entity_id": "-",
                "owner_id": None,
                "team_id": None,
                "scope_key": "leaderboard.view",
            }
        },
    )


def _months_for(deal: Deal | None) -> set[date]:
    months = {stats.current_ist_month()}
    if deal is not None:
        if deal.expected_close_date:
            months.add(deal.expected_close_date.replace(day=1))
        if deal.won_at:
            months.add(stats.current_ist_month(deal.won_at))
    return months


async def _recompute_users(
    session: AsyncSession, org_id: uuid.UUID, user_ids: set[uuid.UUID], months: set[date]
) -> None:
    for uid in user_ids:
        for m in months:
            await stats.recompute_rep_month(session, org_id, uid, m)
    await stats.recompute_ranks_for_month(session, org_id, stats.current_ist_month())


@on_event(
    engine_events.DEAL_WON,
    engine_events.DEAL_LOST,
    engine_events.DEAL_REOPENED,
    engine_events.DEAL_MOVED,
)
async def deal_changed(session: AsyncSession, event: OutboxEvent) -> None:
    deal_id = event.payload.get("deal_id")
    if not deal_id:
        return
    deal = await session.get(Deal, uuid.UUID(deal_id))
    if deal is None:
        return
    user_ids = {u for u in (deal.owner_id, deal.won_by_user_id) if u is not None}
    if not user_ids:
        return
    await _recompute_users(session, event.org_id, user_ids, _months_for(deal))
    await _broadcast_leaderboard(session, event.org_id)


@on_event(engine_events.DEAL_WON)
async def apply_won_pledge(session: AsyncSession, event: OutboxEvent) -> None:
    """Leaderboard & Incentive module: an approved pledge on this deal accrues from now on,
    with no second approval."""
    deal_id = event.payload.get("deal_id")
    if not deal_id:
        return
    deal = await session.get(Deal, uuid.UUID(deal_id))
    if deal is None:
        return
    await perf_service.apply_pledge_on_won(session, deal)


@on_event(engine_events.ONBOARDING_COMPLETED)
async def onboarding_completed(session: AsyncSession, event: OutboxEvent) -> None:
    """Onboarding is done: the Team Leader is asked to set the incentive (an Admin or Super admin
    approves it afterwards); the rep hears that it is being worked on."""
    p = event.payload
    deal_id, company = p.get("deal_id"), p.get("company") or "a deal"
    if not deal_id:
        return
    team_id = uuid.UUID(p["team_id"]) if p.get("team_id") else None
    if leader := await _leader(session, team_id):
        await notifications.notify(
            session, event.org_id, [leader], "incentive.set_rate",
            f"Onboarding done: set the incentive for {company}",
            "Choose the incentive for the rep. An Admin or Super admin approves it "
            "before it is paid.",
            "/app/incentives", dedupe_key=f"incentive.set_rate:{deal_id}",
        )  # fmt: skip
    if p.get("owner_id"):
        await notifications.notify(
            session, event.org_id, [uuid.UUID(p["owner_id"])], "incentive.pending",
            f"Onboarding for {company} is complete",
            "Your Team Leader will propose your incentive; an Admin approves it.",
            "/app/incentives", dedupe_key=f"incentive.pending:{deal_id}",
        )  # fmt: skip


@on_event(lead_events.DEAL_OWNER_CHANGED)
async def owner_changed(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    deal = None
    if deal_id := p.get("deal_id"):
        deal = await session.get(Deal, uuid.UUID(deal_id))
    user_ids = {uuid.UUID(u) for u in (p.get("from_id"), p.get("to_id")) if u}
    if not user_ids:
        return
    await _recompute_users(session, event.org_id, user_ids, _months_for(deal))
    await _broadcast_leaderboard(session, event.org_id)


@on_event(user_events.USER_DEACTIVATED, user_events.USER_REACTIVATED)
async def user_active_changed(session: AsyncSession, event: OutboxEvent) -> None:
    """A deactivated rep is removed from the current month's live ranking (PRD); the row and its
    past-month history are kept untouched."""
    await stats.recompute_ranks_for_month(session, event.org_id, stats.current_ist_month())
    await _broadcast_leaderboard(session, event.org_id)


@on_event(perf_events.TARGET_SET)
async def target_changed(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    user_id, month = uuid.UUID(p["user_id"]), date.fromisoformat(p["month"])
    await stats.recompute_rep_month(session, event.org_id, user_id, month)
    if month == stats.current_ist_month():
        await stats.recompute_ranks_for_month(session, event.org_id, month)
    await _broadcast_leaderboard(session, event.org_id)


@on_event(user_events.TEAM_CREATED)
async def team_created(session: AsyncSession, event: OutboxEvent) -> None:
    team = await session.get(Team, uuid.UUID(event.payload["team_id"]))
    if team is None or team.colour:
        return
    existing = (
        (
            await session.execute(
                select(Team.colour).where(Team.org_id == event.org_id, Team.colour.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    team.colour = generate_colour([c for c in existing if c])
    await session.flush()
