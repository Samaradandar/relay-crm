# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The Today view (PRD 11 Feature 6) and the daily health refresh.

Today: two lanes (Your move / Waiting on them), stale deals, and leads newly assigned to the rep.
A Team Leader opens any team member's Today; Admin and Super admin open anyone's.
"""

import logging
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFound
from app.core.time import utc_now
from app.modules.accounts.models import Account
from app.modules.deals import health
from app.modules.leads.models import Deal, NextStep
from app.modules.leads.ownership import names
from app.modules.pipeline.config.models import Stage
from app.modules.pipeline.engine.service import published_version, stages_of
from app.modules.users.models import Org, User
from app.platform.context import Actor
from app.platform.notifications import service as notifications
from app.platform.outbox import service as outbox
from app.platform.permissions.roles import Role, Scope
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read

log = logging.getLogger("relay.today")
NEW_LEAD_DAYS = 3
DIGEST_EVENT = "deal.digest"
FOLLOWUP_DUE_TYPE = "deal.followup_due"


async def today_view(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID | None
) -> dict[str, Any]:
    decision = await authorize_read(session, actor, "today.view")
    target_id = user_id or actor.id
    assert target_id is not None
    target = (
        await session.execute(select(User).where(User.id == target_id, User.org_id == actor.org_id))
    ).scalar_one_or_none()
    allowed = target is not None and (
        target.id == actor.id
        or decision.scope == Scope.ALL
        or (decision.scope == Scope.TEAM and target.team_id == actor.team_id)
    )
    if not allowed or target is None:
        raise NotFound("Not found")
    if target.role == Role.SUPER_ADMIN and not actor.is_privileged:
        raise NotFound("Not found")  # Super admin-owned work is hidden from Team Leaders and Users

    deal_scope = scope_conditions(actor, Deal, Scope.ALL, team_attr="team_id")
    version = await published_version(session, actor.org_id, "new_business")
    stage_names = {s.key: s.name for s in (await stages_of(session, version.id)).values()}
    today = health.ist_date(utc_now())

    rows = (
        await session.execute(
            select(NextStep, Deal, Account.name)
            .join(Deal, Deal.id == NextStep.deal_id)
            .join(Account, Account.id == Deal.account_id)
            .where(
                *deal_scope,
                NextStep.done_at.is_(None),
                NextStep.responsible_id == target.id,
                Deal.status == "open",
                Deal.deleted_at.is_(None),
            )
            .order_by(NextStep.due_date, NextStep.created_at)
        )
    ).all()
    who = await names(
        session, {d.owner_id for _, d, _ in rows} | {st.assigned_by for st, _, _ in rows}
    )

    def row(step: NextStep, d: Deal, company: str) -> dict[str, Any]:
        fl = d.flags or {}
        return {
            "id": step.id,
            "deal_id": d.id,
            "company": company,
            "text": step.text,
            "due_date": step.due_date,
            "lane": step.lane,
            "lane_since": step.lane_since,
            "snooze_count": step.snooze_count,
            "overdue": step.due_date < today,
            "nudge_due": bool(fl.get("nudge_due")),
            "health": d.health,
            "stage_key": d.stage_key,
            "stage_name": stage_names.get(d.stage_key, d.stage_key),
            "value_paise": d.value_paise,
            "flags": fl,
            "owner": {"id": d.owner_id, "name": who[d.owner_id]} if d.owner_id in who else None,
            "assigned_by": who.get(step.assigned_by) if step.assigned_by else None,
        }

    all_rows = [row(st, d, c) for st, d, c in rows]
    stale_rows = (
        await session.execute(
            select(NextStep, Deal, Account.name)
            .join(Deal, Deal.id == NextStep.deal_id)
            .join(Account, Account.id == Deal.account_id)
            .where(
                *deal_scope,
                NextStep.done_at.is_(None),
                Deal.owner_id == target.id,
                Deal.status == "open",
                Deal.health == "stale",
                Deal.deleted_at.is_(None),
            )
        )
    ).all()
    new_rows = (
        await session.execute(
            select(NextStep, Deal, Account.name)
            .join(Deal, Deal.id == NextStep.deal_id)
            .join(Account, Account.id == Deal.account_id)
            .where(
                *deal_scope,
                NextStep.done_at.is_(None),
                Deal.owner_id == target.id,
                Deal.status == "open",
                Deal.last_activity_at.is_(None),
                Deal.assigned_at >= utc_now() - timedelta(days=NEW_LEAD_DAYS),
                Deal.deleted_at.is_(None),
            )
        )
    ).all()
    your = [r for r in all_rows if r["lane"] == "your_move"]
    waiting = [r for r in all_rows if r["lane"] == "waiting_on_them"]
    stale = [row(st, d, c) for st, d, c in stale_rows]
    new = [row(st, d, c) for st, d, c in new_rows]
    return {
        "user": {"id": target.id, "name": target.name},
        "your_move": your,
        "waiting_on_them": waiting,
        "stale": stale,
        "new_leads": new,
        "counts": {
            "your_move": len(your),
            "overdue": sum(1 for r in your if r["overdue"]),
            "waiting": len(waiting),
            "nudge_due": sum(1 for r in waiting if r["nudge_due"]),
            "stale": len(stale),
            "new_leads": len(new),
        },
    }


# ---------------------------------------------------------------------------- daily refresh
async def refresh_health(
    factory: async_sessionmaker[AsyncSession], now: Any = None, batch: int = 200
) -> dict[str, int]:
    """Recalculate health and flags for every open deal (runs 00:30 IST), then queue one digest
    per owner listing what needs a nudge or has gone stale."""
    now = now or utc_now()
    changed = 0
    nudges: dict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
    stale: dict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
    org_of: dict[uuid.UUID, uuid.UUID] = {}
    async with factory() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    for org_id in org_ids:
        last: uuid.UUID | None = None
        while True:
            async with factory() as s, s.begin():
                q = (
                    select(Deal, Account.name)
                    .join(Account, Account.id == Deal.account_id)
                    .where(
                        Deal.org_id == org_id,
                        Deal.status == "open",
                        Deal.deleted_at.is_(None),
                        *([Deal.id > last] if last else []),
                    )
                    .order_by(Deal.id)
                    .limit(batch)
                    .with_for_update(of=Deal)
                )
                rows = (await s.execute(q)).all()
                if not rows:
                    break
                steps = {
                    st.deal_id: st
                    for st in (
                        await s.execute(
                            select(NextStep).where(
                                NextStep.deal_id.in_([d.id for d, _ in rows]),
                                NextStep.done_at.is_(None),
                            )
                        )
                    ).scalars()
                }
                for d, company in rows:
                    if health.apply(d, steps.get(d.id), now):
                        changed += 1
                    if d.owner_id:
                        org_of[d.owner_id] = d.org_id
                        if (d.flags or {}).get("nudge_due"):
                            nudges[d.owner_id].append({"deal_id": str(d.id), "company": company})
                        if d.health == "stale":
                            stale[d.owner_id].append({"deal_id": str(d.id), "company": company})
                last = rows[-1][0].id
    day = health.ist_date(now).isoformat()
    owners = set(nudges) | set(stale)
    async with factory() as s, s.begin():
        for owner in owners:
            await outbox.enqueue(
                s,
                org_of[owner],
                DIGEST_EVENT,
                {
                    "owner_id": str(owner),
                    "day": day,
                    "nudges": nudges.get(owner, []),
                    "stale": stale.get(owner, []),
                },
            )
    log.info("health refreshed: %s changed, %s digests", changed, len(owners))
    return {"changed": changed, "digests": len(owners)}


# ---------------------------------------------------------------------------- follow-up digest
async def send_followup_digest(factory: async_sessionmaker[AsyncSession], now: Any = None) -> int:
    """Daily 09:00 IST (PRD 11 'Follow-up due', digest half): one notification per responsible
    user listing every open next step due today. The at-due-time half is covered separately: a
    step overdue or due today already shows up as 'your move' / overdue in the Today view itself,
    so this digest is the one piece that was genuinely missing (P5-08)."""
    now = now or utc_now()
    today = health.ist_date(now)
    async with factory() as s:
        rows = (
            await s.execute(
                select(NextStep, Deal, Account.name)
                .join(Deal, Deal.id == NextStep.deal_id)
                .join(Account, Account.id == Deal.account_id)
                .where(
                    NextStep.done_at.is_(None),
                    NextStep.due_date == today,
                    Deal.status == "open",
                    Deal.deleted_at.is_(None),
                )
            )
        ).all()
    by_user: dict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
    org_of: dict[uuid.UUID, uuid.UUID] = {}
    for step, deal, company in rows:
        if step.responsible_id is None:
            continue
        by_user[step.responsible_id].append({"deal_id": str(deal.id), "company": company})
        org_of[step.responsible_id] = deal.org_id
    day = today.isoformat()
    sent = 0
    async with factory() as s, s.begin():
        for user_id, items in by_user.items():
            sent += await notifications.notify(
                s,
                org_of[user_id],
                [user_id],
                FOLLOWUP_DUE_TYPE,
                f"{len(items)} follow-up(s) due today",
                ", ".join(i["company"] for i in items[:8]),
                "/",
                dedupe_key=f"{FOLLOWUP_DUE_TYPE}:{user_id}:{day}",
            )
    return sent


_ = (and_, Stage)
