# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P6-09: weighted forecast. Sum of value_paise * stage probability over OPEN deals, both
pipelines, grouped by expected close month / team / rep. No targets in the forecast (PRD). Shown
in reports, never on the Kanban board.
"""

import uuid
from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.leads.models import Deal
from app.modules.pipeline.config.models import Stage
from app.modules.users.models import Team, User
from app.platform.context import Actor
from app.platform.permissions.roles import Scope
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read

UNSCHEDULED = "unscheduled"


async def _open_deals_in_scope(session: AsyncSession, actor: Actor, scope: Scope) -> Sequence[Deal]:
    conds = scope_conditions(actor, Deal, scope, team_attr="team_id")
    return (
        (
            await session.execute(
                select(Deal).where(*conds, Deal.status == "open", Deal.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )


async def _weighted(session: AsyncSession, deal: Deal) -> int:
    if deal.pipeline_version_id is None:
        return 0
    pct = (
        await session.execute(
            select(Stage.probability_pct).where(
                Stage.version_id == deal.pipeline_version_id, Stage.key == deal.stage_key
            )
        )
    ).scalar_one_or_none() or 0
    return (deal.value_paise or 0) * pct // 100


async def weighted_total(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    team_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> int:
    """A plain scalar total -- used by the dashboard/overview endpoints, which want one number,
    not the grouped breakdown the /forecast endpoint returns."""
    conds = [Deal.org_id == org_id, Deal.status == "open", Deal.deleted_at.is_(None)]
    if team_id is not None:
        conds.append(Deal.team_id == team_id)
    if user_id is not None:
        conds.append(Deal.owner_id == user_id)
    deals = (await session.execute(select(Deal).where(*conds))).scalars().all()
    total = 0
    for d in deals:
        total += await _weighted(session, d)
    return total


async def forecast(
    session: AsyncSession,
    actor: Actor,
    *,
    group_by: Literal["month", "team", "rep"] = "month",
) -> dict[str, Any]:
    decision = await authorize_read(session, actor, "forecast.view")
    deals = await _open_deals_in_scope(session, actor, decision.scope)
    names: dict[uuid.UUID, str] = {}
    if group_by == "team":
        ids = {d.team_id for d in deals if d.team_id}
        if ids:
            rows = (await session.execute(select(Team.id, Team.name).where(Team.id.in_(ids)))).all()
            names = dict(rows)
    elif group_by == "rep":
        ids = {d.owner_id for d in deals if d.owner_id}
        if ids:
            rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
            names = dict(rows)

    buckets: dict[str, dict[str, Any]] = {}
    for d in deals:
        w = await _weighted(session, d)
        if group_by == "month":
            key = (
                d.expected_close_date.replace(day=1).isoformat()
                if d.expected_close_date
                else UNSCHEDULED
            )
            label = key
        elif group_by == "team":
            key = str(d.team_id) if d.team_id else UNSCHEDULED
            label = names.get(d.team_id, "No team") if d.team_id else "No team"
        else:
            key = str(d.owner_id) if d.owner_id else UNSCHEDULED
            label = names.get(d.owner_id, "Unassigned") if d.owner_id else "Unassigned"
        b = buckets.setdefault(
            key, {"key": key, "label": label, "weighted_paise": 0, "deal_count": 0}
        )
        b["weighted_paise"] += w
        b["deal_count"] += 1

    ordered = sorted((b for k, b in buckets.items() if k != UNSCHEDULED), key=lambda b: b["key"])
    if UNSCHEDULED in buckets:
        ordered.append(buckets[UNSCHEDULED])
    return {
        "group_by": group_by,
        "buckets": ordered,
        "total_weighted_paise": sum(b["weighted_paise"] for b in ordered),
    }
