# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for Phase 6: leaderboard, targets, incentives, dashboards, reports, forecast
and team colours; thin, calls the service/dashboard/reports modules.

Built in: Phase 6. See Relay CRM Build Plan.
"""

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.errors import NotFound, PermissionDenied
from app.modules.leads.models import Deal
from app.modules.performance import dashboard, forecast, reports, service
from app.modules.performance.reports import record_active_day
from app.modules.performance.schemas import (
    BulkTargetIn,
    ColourRegenerateIn,
    IncentiveRateIn,
    RecomputeMonthIn,
    TargetIn,
    camelize,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["performance"])


@router.get("/leaderboard")
async def get_leaderboard(
    actor: ActorDep, month: str | None = None, team_id: uuid.UUID | None = None
) -> dict[str, Any]:
    m = date.fromisoformat(f"{month}-01") if month else None
    async with get_sessionmaker()() as s:
        return await service.leaderboard(s, actor, m, team_id)


@router.get("/targets")
async def get_target(actor: ActorDep, user_id: uuid.UUID, month: str) -> dict[str, Any]:
    m = date.fromisoformat(f"{month}-01")
    async with get_sessionmaker()() as s:
        row = await service.get_target(s, actor, user_id, m)
        if row is None:
            raise NotFound("No target set")
        return row


@router.put("/targets/{user_id}/{month}")
async def put_target(
    user_id: uuid.UUID, month: str, body: TargetIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    m = date.fromisoformat(f"{month}-01")
    return await service.set_target(actor, req, user_id, m, body.amount_paise, body.reason)


@router.put("/targets/bulk")
async def put_targets_bulk(
    body: BulkTargetIn, actor: ActorDep, req: RequestDep
) -> list[dict[str, Any]]:
    items = [(uuid.UUID(i.user_id), i.amount_paise) for i in body.items]
    return await service.set_targets_bulk(actor, req, body.month, items, body.reason)


@router.get("/deals/{deal_id}/incentive")
async def get_incentive(deal_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_incentive(s, actor, deal_id)


@router.put("/deals/{deal_id}/incentive")
async def put_incentive(
    deal_id: uuid.UUID, body: IncentiveRateIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.set_incentive_rate(actor, req, deal_id, body.rate_bp, body.reason)


@router.get("/incentives")
async def list_incentives(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_incentives(s, actor)


@router.post("/deals/{deal_id}/incentive/request")
async def request_incentive(
    deal_id: uuid.UUID, body: IncentiveRateIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    """Team Leader: ask for a rate on a Won deal. An Admin or Super admin approves it."""
    return await service.request_incentive_on_won_deal(
        actor, req, deal_id, body.rate_bp, body.reason
    )


@router.post("/deals/{deal_id}/incentive/pledge")
async def pledge_incentive(
    deal_id: uuid.UUID, body: IncentiveRateIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    """Team Leader: pledge a rate on an open deal; it applies if the deal is Won."""
    return await service.pledge_incentive_on_active_deal(
        actor, req, deal_id, body.rate_bp, body.reason
    )


@router.get("/incentives/missing-rates")
async def get_missing_rates(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.missing_rates(s, actor)


@router.get("/dashboard/{user_id}")
async def get_dashboard(user_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    """Personal sales dashboard (P6-07): own rank/numbers/target/trend plus -- the one place
    incentives appear outside the dedicated incentive endpoints -- this rep's own incentives,
    scoped exactly like /deals/{id}/incentive (own / TL's team / Admin / SA)."""
    async with get_sessionmaker()() as s:
        profile = await dashboard.rep_profile_for(s, actor, user_id)
        won_deal_ids = (
            (
                await s.execute(
                    select(Deal.id).where(Deal.won_by_user_id == user_id, Deal.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        incentives: list[dict[str, Any]] = []
        for deal_id in won_deal_ids:
            try:
                incentives.append(await service.get_incentive(s, actor, deal_id))
            except (NotFound, PermissionDenied):
                break  # out of scope for every deal alike; stop asking rather than raise 404
        profile["incentives"] = incentives
        return profile


@router.post("/teams/{team_id}/colour/regenerate")
async def regenerate_colour(
    team_id: uuid.UUID, body: ColourRegenerateIn, actor: ActorDep, req: RequestDep
) -> dict[str, str]:
    colour = await service.regenerate_colour(actor, req, team_id, body.reason)
    return {"colour": colour}


@router.get("/reports/{name}")
async def get_report(
    name: Literal[
        "leads",
        "conversion",
        "won_lost",
        "revenue_billing",
        "renewals_churn",
        "rep_performance",
        "measures",
    ],  # fmt: skip
    actor: ActorDep,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    async with get_sessionmaker()() as s, s.begin():
        await record_active_day(s, actor.org_id, actor.id) if actor.id else None
        return await reports.get_report(s, actor, name, date_from=date_from, date_to=date_to)


@router.get("/forecast")
async def get_forecast(
    actor: ActorDep, group_by: Literal["month", "team", "rep"] = "month"
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await forecast.forecast(s, actor, group_by=group_by)


@router.post("/admin/stats/recompute")
async def recompute_month(
    month: str, body: RecomputeMonthIn, actor: ActorDep, req: RequestDep
) -> dict[str, int]:
    m = date.fromisoformat(f"{month}-01")
    n = await service.recompute_month(actor, req, m, body.reason)
    return {"reps_recomputed": n}


# ---------------------------------------------------------------- section 12a: frontend contract
@router.get("/me/numbers")
async def get_my_numbers(actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        out = await dashboard.my_numbers(s, actor)
        if out is None:
            raise NotFound("Not a sales rep")
        return dict(camelize(out))


@router.get("/team/overview")
async def get_team_overview(actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return dict(camelize(await dashboard.team_overview(s, actor)))


@router.get("/team/reps/{user_id}")
async def get_rep_profile(user_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return dict(camelize(await dashboard.rep_profile_for(s, actor, user_id)))


@router.get("/admin/overview")
async def get_company_overview(actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return dict(camelize(await dashboard.company_overview(s, actor)))
