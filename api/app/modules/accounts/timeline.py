# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The account page's visual history (PRD 11 Feature 3): stage bands, activities, documents,
ownership changes and milestones on one timeline, plus a weekly engagement strip.

Only deals inside the caller's scope contribute; the rest are counted as locked rows."""

import uuid
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.accounts.service import _deal_scope, _load_visible
from app.modules.activities.models import Activity
from app.modules.documents.models import Document
from app.modules.leads.models import Deal
from app.modules.leads.ownership import names
from app.modules.pipeline.config.models import Stage
from app.modules.pipeline.engine.models import DealStageEntry
from app.platform.audit.models import AuditLog
from app.platform.context import Actor
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read

OWNERSHIP_KEYS = (
    "lead.assigned",
    "pool.assign",
    "lead.reassign",
    "deal.owner.change",
    "deal.bulk_reassign",
    "deal.team_transfer.decide_or_move",
    "deal.override_or_skip",
    "deal.lose",
    "deal.reopen",
)


async def account_timeline(
    session: AsyncSession, actor: Actor, account_id: uuid.UUID
) -> dict[str, Any]:
    await authorize_read(session, actor, "deal.view")
    acc = await _load_visible(session, actor, account_id)
    scope = await _deal_scope(session, actor)
    deals = list(
        (
            await session.execute(
                select(Deal)
                .where(
                    Deal.account_id == acc.id,
                    Deal.deleted_at.is_(None),
                    *scope_conditions(actor, Deal, scope, team_attr="team_id"),
                )
                .order_by(Deal.created_at)
            )
        ).scalars()
    )
    ids = [d.id for d in deals]
    events: list[dict[str, Any]] = []
    bands: list[dict[str, Any]] = []
    if ids:
        version_ids = {d.pipeline_version_id for d in deals if d.pipeline_version_id}
        stage_names = {
            (v, k): n
            for v, k, n in (
                await session.execute(
                    select(Stage.version_id, Stage.key, Stage.name).where(
                        Stage.version_id.in_(version_ids)
                    )
                )
            ).all()
        }
        for e in (
            await session.execute(
                select(DealStageEntry)
                .where(DealStageEntry.deal_id.in_(ids))
                .order_by(DealStageEntry.entered_at)
            )
        ).scalars():
            name = stage_names.get((e.version_id, e.stage_key), e.stage_key)
            bands.append(
                {
                    "deal_id": e.deal_id,
                    "stage_key": e.stage_key,
                    "stage_name": name,
                    "from": e.entered_at,
                    "to": e.exited_at,
                    "exit_kind": e.exit_kind,
                    "round": e.revision_round,
                }
            )
            events.append(
                {
                    "at": e.entered_at,
                    "kind": "stage",
                    "title": f"Entered {name}"
                    + (f" (round {e.revision_round})" if e.revision_round > 1 else ""),
                    "detail": e.reason,
                    "deal_id": e.deal_id,
                    "by": e.entered_by,
                }
            )
        acts = list(
            (
                await session.execute(
                    select(Activity).where(Activity.deal_id.in_(ids), Activity.deleted_at.is_(None))
                )
            ).scalars()
        )
        for a in acts:
            events.append(
                {
                    "at": a.occurred_at,
                    "kind": "activity",
                    "title": a.type.title(),
                    "detail": a.discussed,
                    "deal_id": a.deal_id,
                    "by": a.attributed_to,
                    "two_way": a.two_way,
                }
            )
        for doc in (
            await session.execute(
                select(Document).where(Document.deal_id.in_(ids), Document.deleted_at.is_(None))
            )
        ).scalars():
            events.append(
                {
                    "at": doc.sent_at or doc.created_at,
                    "kind": "document",
                    "title": f"{doc.kind.title()} v{doc.version_no} {doc.status}",
                    "detail": doc.number,
                    "deal_id": doc.deal_id,
                    "by": doc.created_by,
                }
            )
        for r in (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.org_id == actor.org_id,
                    AuditLog.entity_type == "deal",
                    AuditLog.entity_id.in_([str(i) for i in ids]),
                    AuditLog.action_key.in_(OWNERSHIP_KEYS),
                    AuditLog.outcome == "ok",
                )
                .order_by(AuditLog.seq)
            )
        ).scalars():
            new = r.new_value or {}
            events.append(
                {
                    "at": r.occurred_at,
                    "kind": "ownership"
                    if r.action_key not in ("deal.lose", "deal.reopen")
                    else "milestone",
                    "title": _ownership_title(r.action_key, new),
                    "detail": r.reason,
                    "deal_id": uuid.UUID(r.entity_id),
                    "by": r.actor_id,
                }
            )
        for wd in deals:
            if wd.won_at:
                events.append(
                    {
                        "at": wd.won_at,
                        "kind": "milestone",
                        "title": "Won",
                        "detail": None,
                        "deal_id": wd.id,
                        "by": wd.owner_id,
                    }
                )
        week = Counter(
            f"{a.occurred_at.isocalendar().year}-W{a.occurred_at.isocalendar().week:02d}"
            for a in acts
        )
    else:
        week = Counter()
    who = await names(session, {ev["by"] for ev in events})
    for ev in events:
        ev["by"] = who.get(ev["by"]) if ev["by"] else "System"
    events.sort(key=lambda e: e["at"])
    return {
        "account_id": acc.id,
        "now": utc_now(),
        "bands": bands,
        "events": events,
        "engagement": [{"week": k, "count": week[k]} for k in sorted(week)],
    }


def _ownership_title(key: str, new: dict[str, Any]) -> str:
    owner = new.get("owner")
    return {
        "lead.assigned": f"Assigned to {owner}" if owner else "Assigned",
        "pool.assign": f"Assigned from the Pool to {owner}",
        "lead.reassign": f"Reassigned to {owner}" if owner else "Returned to the Pool",
        "deal.owner.change": f"Owner changed to {owner}",
        "deal.bulk_reassign": f"Reassigned to {owner}",
        "deal.team_transfer.decide_or_move": f"Moved to another team ({owner})",
        "deal.override_or_skip": "Moved with an override",
        "deal.lose": "Closed as lost",
        "deal.reopen": "Reopened",
    }.get(key, key)
