# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The deal page: everything one deal shows, in one read (scope-checked)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.accounts.models import Contact
from app.modules.leads.models import Deal
from app.modules.leads.service import build_deals
from app.modules.pipeline.config.models import CustomFieldDef, FitItem, Stage
from app.modules.pipeline.engine.models import DealStageEntry
from app.modules.pipeline.engine.service import progress
from app.platform.context import Actor
from app.platform.reads import authorize_read


async def deal_detail(
    session: AsyncSession, actor: Actor, deal_id: uuid.UUID, *, include_deleted: bool = False
) -> dict[str, Any]:
    await authorize_read(session, actor, "deal.view", ("deal", deal_id))
    deal = await session.get(Deal, deal_id)
    if deal is None or deal.org_id != actor.org_id:
        raise NotFound("Not found")
    if deal.deleted_at is not None and not include_deleted:
        raise NotFound("Not found")
    return await detail_of(session, deal)


async def detail_of(session: AsyncSession, deal: Deal) -> dict[str, Any]:
    base = (await build_deals(session, [deal]))[0]
    prog = await progress(session, deal)
    stage_rows = {
        s.key: s
        for s in (
            await session.execute(select(Stage).where(Stage.version_id == deal.pipeline_version_id))
        ).scalars()
    }
    cur = stage_rows.get(deal.stage_key)
    contacts = list(
        (
            await session.execute(
                select(Contact)
                .where(Contact.account_id == deal.account_id, Contact.deleted_at.is_(None))
                .order_by(Contact.created_at)
            )
        ).scalars()
    )
    fit_items = list(
        (
            await session.execute(
                select(FitItem)
                .where(FitItem.org_id == deal.org_id, FitItem.active.is_(True))
                .order_by(FitItem.position)
            )
        ).scalars()
    )
    defs = list(
        (
            await session.execute(
                select(CustomFieldDef)
                .where(
                    CustomFieldDef.org_id == deal.org_id,
                    CustomFieldDef.entity == "deal",
                    CustomFieldDef.active.is_(True),
                )
                .order_by(CustomFieldDef.key)
            )
        ).scalars()
    )
    visits = list(
        (
            await session.execute(
                select(DealStageEntry)
                .where(DealStageEntry.deal_id == deal.id)
                .order_by(DealStageEntry.entered_at)
            )
        ).scalars()
    )
    ticked = deal.fit_checklist or {}
    return {
        **base,
        "stage_name": cur.name if cur else deal.stage_key,
        "probability_pct": cur.probability_pct if cur else None,
        "pricing_model": deal.pricing_model,
        "plan": deal.plan,
        "expected_minutes_per_month": deal.expected_minutes_per_month,
        "use_case": deal.use_case,
        "pain_point": deal.pain_point,
        "expected_close_reason": deal.expected_close_reason,
        "lost_reason": deal.lost_reason,
        "lost_notes": deal.lost_notes,
        "lost_from_stage": (
            stage_rows[deal.lost_from_stage_key].name
            if deal.lost_from_stage_key in stage_rows
            else deal.lost_from_stage_key
        ),
        "data": deal.data or {},
        "fit": [
            {"id": i.id, "label": i.label, "ticked": bool(ticked.get(str(i.id)))} for i in fit_items
        ],
        "custom": [
            {
                "key": d.key,
                "label": d.label,
                "type": d.type,
                "options": d.options,
                "value": (deal.custom_fields or {}).get(d.key),
            }
            for d in defs
        ],
        "contacts": [
            {
                "id": c.id,
                "name": c.name,
                "role": c.role,
                "phone": c.phone_e164,
                "email": c.email,
                "is_decision_maker": c.is_decision_maker,
            }
            for c in contacts
        ],
        "progress": prog,
        "visits": [
            {
                "stage_key": v.stage_key,
                "stage_name": stage_rows[v.stage_key].name
                if v.stage_key in stage_rows
                else v.stage_key,
                "entered_at": v.entered_at,
                "exited_at": v.exited_at,
                "exit_kind": v.exit_kind,
                "reason": v.reason,
                "revision_round": v.revision_round,
            }
            for v in visits
        ],
        "deleted": deal.deleted_at is not None,
        "deleted_reason": deal.deleted_reason,
    }
