# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The next step that comes from the pipeline itself.

For a deal in stage S the suggested next step is the FIRST UNMET criterion of S (its label, as
copied into the deal's checklist when it entered the stage, judged by the same `_unmet` check
`move()` uses); when every criterion is met it is "Move to <next stage name>". Everything is read
from the version the deal is pinned to, so a later published pipeline never changes it.

A new lead gets it when it is created (unless the person typed one). After a successful stage move
the deal gets the new stage's suggestion, but only when the open next step is absent or was itself
written by the system: a next step a person chose is never overwritten.
"""

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.core.time import utc_now
from app.modules.deals import health
from app.modules.deals.steps import open_step
from app.modules.leads.models import Deal
from app.modules.pipeline.engine import service
from app.modules.pipeline.engine.models import DealStageEntry
from app.modules.pipeline.engine.proofs import build_ctx, run_proof
from app.platform.uow import UnitOfWork


def tomorrow_ist() -> date:
    return health.ist_date(utc_now()) + timedelta(days=1)


async def _entry(session: AsyncSession, deal_id: uuid.UUID) -> DealStageEntry | None:
    """The deal's current visit (a won deal keeps its last one)."""
    return (
        await session.execute(
            select(DealStageEntry)
            .where(DealStageEntry.deal_id == deal_id)
            .order_by(DealStageEntry.exited_at.is_(None).desc(), DealStageEntry.entered_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def suggest(session: AsyncSession, deal: Deal) -> str | None:
    """Text of the next step the pipeline asks for, or None when it asks for nothing (no stage
    visit, the stage left the version, or the last stage with everything met)."""
    entry = await _entry(session, deal.id)
    if entry is None:
        return None
    unmet = await service._unmet(session, entry)
    stages = await service.stages_of(session, entry.version_id)
    cur = stages.get(entry.stage_key)
    if cur is None:
        return None
    # A criterion whose proof already holds (e.g. "basic info" on a lead that was just created with
    # it) only needs a tick, so it is not the work to ask for: ask for the first one still missing.
    ctx = await build_ctx(session, deal)
    by_key = {c["key"]: c for c in entry.checklist_snapshot}
    missing = []
    for u in unmet:
        item = by_key[u["key"]]
        res = await run_proof(ctx, item["proof_type"], item["proof_config"], None)
        if not res.ok:
            missing.append(u)
    if missing:
        return str(missing[0]["label"])
    only_ticks = bool(unmet)
    trans = await service.transitions_of(session, entry.version_id)
    forward = [
        stages[to]
        for (frm, to), kind in trans.items()
        if frm == cur.key and kind == "forward" and to in stages
    ]
    later = [st for st in forward if st.position > cur.position] or [
        st for st in stages.values() if st.position > cur.position and not st.is_conditional
    ]
    if not later:
        return None
    name = min(later, key=lambda st: st.position).name
    return (
        f"Tick the finished checklist items, then move to {name}"
        if only_ticks
        else f"Move to {name}"
    )


async def refresh_after_move(uow: UnitOfWork, deal: Deal) -> None:
    """Call inside `move()` once the deal is in its new stage."""
    from app.modules.deals.nextsteps import apply_step

    s = uow.session
    step = await open_step(s, deal.id, lock=True)
    if step is not None and not step.auto_generated:
        return  # a person's own next step stays
    text = await suggest(s, deal)
    if text is None or (step is not None and step.text == text):
        return
    try:
        await apply_step(
            uow,
            deal,
            text=text,
            due=tomorrow_ist(),
            responsible_id=step.responsible_id if step is not None else None,
            action="auto",
            auto=True,
            note="Next step set from the pipeline stage",
        )
    except ValidationFailed:  # e.g. the responsible person is no longer active: keep the move
        return
