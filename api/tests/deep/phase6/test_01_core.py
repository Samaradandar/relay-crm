# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 6 deep tests: the stats engine, leaderboard, targets, incentives and the incentive
privacy scan. Expected numbers are hand-calculated from the PRD, not from the code.
"""

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.errors import NotFound, OutOfScope, PermissionDenied
from app.modules.performance import service, stats
from app.platform.context import RequestInfo
from tests.modules.journey import run_to


@pytest.fixture
async def session(database):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database.app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_won_credit_survives_reassignment_and_feeds_leaderboard(
    client, world, admin_engine, session
):
    deal, owner = await run_to(client, world, admin_engine, "onboarding")
    did = uuid.UUID(deal["id"])
    async with admin_engine.begin() as c:
        won = (
            await c.execute(
                text(
                    "SELECT won_by_user_id, won_team_id, won_value_paise, status FROM deals WHERE id=:d"
                ),
                {"d": did},
            )
        ).one()
    assert won.status == "won"
    assert str(won.won_by_user_id) == str(owner.id)
    assert won.won_team_id is not None
    assert won.won_value_paise is not None and won.won_value_paise > 0

    # Reassign to a teammate who did NOT win it: Won credit must NOT move (PRD decision).
    new_owner = world.north2 if owner.id != world.north2.id else world.north1
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE deals SET owner_id=:u WHERE id=:d"), {"u": new_owner.id, "d": did}
        )

    month = stats.current_ist_month()
    await stats.recompute_rep_month(session, world.org_id, owner.id, month)
    await stats.recompute_rep_month(session, world.org_id, new_owner.id, month)
    await stats.recompute_ranks_for_month(session, world.org_id, month)
    await session.commit()

    out = await service.leaderboard(session, world.north1, month, None)
    by_user = {r["user_id"]: r for r in out["rows"]}
    assert by_user[owner.id]["won_deals"] == 1
    assert by_user[owner.id]["revenue_current_paise"] == won.won_value_paise
    assert by_user[new_owner.id]["won_deals"] == 0  # the new owner gets no credit for an old Won

    # Privacy: the leaderboard response carries no incentive field whatsoever.
    dumped = json.dumps(out, default=str)
    for forbidden in ("rate_bp", "earned_amount", "pending_amount", "incentive"):
        assert forbidden not in dumped


async def test_target_set_scope_tl_team_vs_user_denied(world, session):
    req = RequestInfo()
    month = stats.current_ist_month()
    out = await service.set_target(
        world.tl_north, req, world.north1.id, month, 10_00_000_00, "Q target"
    )
    assert out["amount_paise"] == 10_00_000_00

    with pytest.raises(PermissionDenied):
        await service.set_target(world.north1, req, world.north2.id, month, 5_000_00, "nope")

    with pytest.raises(OutOfScope):  # TL cannot set a target for the OTHER team
        await service.set_target(
            world.tl_north, req, world.south1.id, month, 1_000_00, "wrong team"
        )


async def test_tl_can_only_request_incentive_admin_must_approve(
    client, world, admin_engine, session
):
    """Leaderboard & Incentive module (approved 6 Oct 2026): a TL can no longer set a deal's rate
    directly, only request one on a Won deal; Admin/SA approves via the Phase 1 approval inbox."""
    deal, owner = await run_to(client, world, admin_engine, "onboarding")
    did = uuid.UUID(deal["id"])
    req = RequestInfo()

    with pytest.raises(PermissionDenied):  # superseded: direct-set is Admin/SA only now
        await service.set_incentive_rate(world.tl_north, req, did, 500, "5% standard rate")

    with pytest.raises(PermissionDenied):  # the OTHER team's TL may not request on this deal
        await service.request_incentive_on_won_deal(world.tl_south, req, did, 999, "not yours")

    requested = await service.request_incentive_on_won_deal(
        world.tl_north, req, did, 500, "5% standard rate"
    )
    assert requested["status"] == "pending_approval"
    approval_id = requested["approval_id"]

    seen = await service.get_incentive(session, world.tl_north, did)
    assert seen["status"] == "pending_approval"
    with pytest.raises(NotFound):  # a different rep never sees another rep's incentive
        await service.get_incentive(session, world.south1, did)

    from app.platform.approvals import service as approvals

    decided = await approvals.decide(world.admin, req, approval_id, approve=True, reason="ok")
    assert decided.status == "approved"

    out = await service.get_incentive(session, world.tl_north, did)
    assert out["rate_bp"] == 500
    assert out["status"] == "approved"  # nothing received yet, but the rate is live
