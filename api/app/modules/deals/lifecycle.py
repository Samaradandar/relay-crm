# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Closing and reopening deals, soft delete and restore, and the bulk versions (P3-13, P3-18).

Nothing is ever erased: a lost deal keeps its full history, a deleted deal can be restored.
Every deal that stops being open emits `deal.closed`, which frees the rep's lead cap and makes
the Lead Pool retry waiting leads.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.deals.common import load_deal
from app.modules.leads import events as lead_events
from app.modules.leads.models import Deal, LeadPoolEntry
from app.modules.leads.ownership import (
    company_name,
    deal_rt,
    open_pool_entry,
    pool_rt,
    return_to_pool,
    team_names,
)
from app.modules.pipeline.config.models import LostReason
from app.modules.pipeline.engine import events
from app.modules.pipeline.engine.models import DealStageEntry
from app.modules.pipeline.engine.service import (
    _enter_stage,
    current_entry,
    published_version,
    stages_of,
)
from app.modules.users.models import User
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork


def _payload(deal: Deal, company: str, actor: Actor, **extra: Any) -> dict[str, Any]:
    return {
        "deal_id": str(deal.id),
        "owner_id": str(deal.owner_id) if deal.owner_id else None,
        "team_id": str(deal.team_id) if deal.team_id else None,
        "company": company,
        "by": actor.name,
        "by_id": str(actor.id) if actor.id else None,
        **extra,
    }


async def _lose_in(
    uow: UnitOfWork,
    deal: Deal,
    *,
    key: str,
    lost_reason: str,
    notes: str | None,
) -> None:
    s = uow.session
    await uow.authorize(key, entity=("deal", deal.id), owner_id=deal.owner_id)
    labels = {
        r.label
        for r in (
            await s.execute(
                select(LostReason).where(
                    LostReason.org_id == deal.org_id, LostReason.active.is_(True)
                )
            )
        ).scalars()
    }
    if lost_reason not in labels:
        raise ValidationFailed("Choose a lost reason from the list", {"field": "lost_reason"})
    cleaned = uow.reason(key, lost_reason)  # the listed reason is the reason for the policy
    if deal.status != "open":
        raise Conflict("Only open deals can be closed as lost")
    entry = await current_entry(s, deal.id, lock=True)
    now = utc_now()
    entry.exited_at, entry.exit_kind, entry.reason = now, "lost", lost_reason
    old = {"status": deal.status, "stage": deal.stage_key}
    deal.status, deal.lost_at = "lost", now
    deal.lost_reason, deal.lost_notes = lost_reason, (notes or "").strip() or None
    deal.lost_from_stage_key = deal.stage_key
    deal.version, deal.updated_at = deal.version + 1, now
    await s.flush()
    await uow.audit(
        key,
        "deal",
        deal.id,
        old=old,
        new={"status": "lost", "lost_reason": lost_reason, "notes": deal.lost_notes},
        reason=cleaned,
    )
    payload = _payload(deal, await company_name(s, deal), uow.actor, lost_reason=lost_reason)
    await uow.emit(events.DEAL_LOST, payload, rt=deal_rt(deal))
    await uow.emit(events.DEAL_CLOSED, payload)


async def lose(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    *,
    lost_reason: str,
    notes: str | None,
    key: str = "deal.lose",
) -> None:
    async with UnitOfWork(actor, request) as uow:
        deal = await load_deal(uow, deal_id, lock=True)
        await _lose_in(uow, deal, key=key, lost_reason=lost_reason, notes=notes)


async def reopen(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("deal.reopen", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("deal.reopen", reason)
        if deal.status != "lost":
            raise Conflict("Only a lost deal can be reopened")
        version = await published_version(s, deal.org_id, deal.pipeline)
        stages = await stages_of(s, version.id)
        target = stages.get(deal.lost_from_stage_key or "")
        if target is None:
            raise Conflict("The stage this deal was lost from no longer exists")
        old = {"status": "lost", "lost_reason": deal.lost_reason}
        await _enter_stage(
            uow, deal, version, target, exit_kind=None, reason=cleaned, round_=deal.revision_round
        )
        deal.status = "open"
        deal.lost_at = deal.lost_reason = deal.lost_notes = deal.lost_from_stage_key = None
        await s.flush()
        await uow.audit(
            "deal.reopen",
            "deal",
            deal.id,
            old=old,
            new={"status": "open", "stage": target.name},
            reason=cleaned,
        )
        payload = _payload(deal, await company_name(s, deal), actor)
        await uow.emit(events.DEAL_REOPENED, payload, rt=deal_rt(deal))


async def delete_deal(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("deal.delete", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("deal.delete", reason)
        now = utc_now()
        old = {"status": deal.status, "deleted": False}
        deal.deleted_at, deal.deleted_reason = now, cleaned
        deal.version, deal.updated_at = deal.version + 1, now
        entry = await open_pool_entry(s, deal.id, lock=True)
        if entry is not None:
            entry.left_at, entry.left_reason = now, "deleted"
        await s.flush()
        await uow.audit(
            "deal.delete", "deal", deal.id, old=old, new={"deleted": True}, reason=cleaned
        )
        payload = _payload(deal, await company_name(s, deal), actor)
        await uow.emit(events.DEAL_CLOSED, payload)
        await uow.emit(
            lead_events.POOL_CHANGED, {"deal_id": str(deal.id)}, rt=pool_rt(deal.team_id)
        )
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def restore_deal(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True, deleted=True)
        await uow.authorize("deal.restore", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("deal.restore", reason)
        if deal.deleted_at is None:
            raise Conflict("This deal is not deleted")
        deal.deleted_at = deal.deleted_reason = None
        deal.version, deal.updated_at = deal.version + 1, utc_now()
        sent_to_pool = False
        if deal.status == "open" and deal.owner_id is not None:
            # The owner may have changed teams (or left active service) while this deal was
            # deleted: `on_user_team_moved` and friends only touch non-deleted deals, so an open
            # deal's team_id can go stale. Re-sync it now, mirroring that hook, instead of
            # bringing the deal back with a team that no longer matches its owner (PRD invariant:
            # an open deal's team is always its owner's team).
            owner = (
                await s.execute(
                    select(User).where(User.id == deal.owner_id, User.org_id == deal.org_id)
                )
            ).scalar_one_or_none()
            if owner is None or owner.deleted_at is not None or not owner.is_active:
                await return_to_pool(
                    uow,
                    deal,
                    deal.team_id,
                    source="the owner is no longer available to restore this deal to",
                    hold=True,
                    reason=cleaned,
                )
                sent_to_pool = True
            elif owner.team_id != deal.team_id:
                teams = await team_names(s, {deal.team_id, owner.team_id})
                before = {"team": teams.get(deal.team_id) if deal.team_id else None}
                deal.team_id, deal.version, deal.updated_at = (
                    owner.team_id,
                    deal.version + 1,
                    utc_now(),
                )
                await uow.audit(
                    "team.manage",
                    "deal",
                    deal.id,
                    old=before,
                    new={"team": teams.get(owner.team_id) if owner.team_id else None},
                    note=f"Resynced to {owner.name}'s current team on restore",
                )
        if deal.status == "pooled" and not sent_to_pool:
            s.add(
                LeadPoolEntry(
                    org_id=deal.org_id,
                    deal_id=deal.id,
                    entered_by=actor.id,
                    entered_source="restored",
                    routed_team_id=deal.team_id,
                )
            )
            await uow.emit(lead_events.DEAL_POOLED, {"deal_id": str(deal.id)})
        await s.flush()
        await uow.audit(
            "deal.restore", "deal", deal.id, old={"deleted": True}, new={"deleted": False},
            reason=cleaned,
        )  # fmt: skip
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def list_deleted(
    session: AsyncSession, actor: Actor, limit: int = 200
) -> list[dict[str, Any]]:
    from app.modules.leads.service import build_deals

    decision = await authorize_read(session, actor, "deleted.view")
    rows = list(
        (
            await session.execute(
                select(Deal)
                .where(
                    *scope_conditions(actor, Deal, decision.scope, team_attr="team_id"),
                    Deal.deleted_at.is_not(None),
                )
                .order_by(Deal.deleted_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    out = await build_deals(session, rows)
    for d, row in zip(out, rows, strict=True):
        d["deleted_at"] = row.deleted_at
        d["deleted_reason"] = row.deleted_reason
    return out


# ---------------------------------------------------------------------------- bulk
async def bulk_each(ids: list[uuid.UUID], run: Any) -> dict[str, Any]:
    """Run `run(deal_id)` for each deal in its own transaction; one failure never blocks others."""
    done: list[uuid.UUID] = []
    failed: list[dict[str, Any]] = []
    for did in ids:
        try:
            await run(did)
            done.append(did)
        except AppError as e:
            failed.append({"deal_id": str(did), "code": e.code, "message": e.message})
    return {"done": done, "failed": failed}


async def bulk_lose(
    actor: Actor,
    request: RequestInfo,
    ids: list[uuid.UUID],
    lost_reason: str,
    notes: str | None,
) -> dict[str, Any]:
    return await bulk_each(
        ids,
        lambda did: lose(
            actor, request, did, lost_reason=lost_reason, notes=notes, key="deal.bulk_lose"
        ),
    )


_ = (DealStageEntry, NotFound)
