# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The stage engine (PRD 5, 11 Feature 2): one `move` for every way a deal changes stage, plus
exit criteria (tick, untick, verify, reject) and the proof-missing check.

Rules that hold everywhere:
- Every change locks the deal row first, so a tick racing a rejection, or two moves, end in one
  consistent state (the loser gets 409 or a clear error).
- A deal in a stage keeps the checklist copied when it entered (`checklist_snapshot`); a later
  published pipeline never changes it.
- Permission, scope and reason come from the UnitOfWork, never from `if role == ...`.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    Conflict,
    CriteriaIncomplete,
    NotFound,
    ProofRequired,
    ReasonRequired,
    ValidationFailed,
)
from app.core.money import format_rupees
from app.core.time import utc_now
from app.modules.deals.common import load_deal
from app.modules.leads import events as lead_events
from app.modules.leads.models import Deal
from app.modules.leads.ownership import company_name, deal_rt, names
from app.modules.pipeline.config.models import (
    Pipeline,
    PipelineVersion,
    Stage,
    StageCriterion,
    StageTransition,
)
from app.modules.pipeline.engine import events
from app.modules.pipeline.engine.models import DealCriterion, DealStageEntry
from app.modules.pipeline.engine.proofs import build_ctx, run_proof
from app.platform.context import Actor, RequestInfo
from app.platform.uow import UnitOfWork


# ---------------------------------------------------------------------------- configuration reads
async def published_version(session: AsyncSession, org_id: uuid.UUID, key: str) -> PipelineVersion:
    row = (
        await session.execute(
            select(PipelineVersion)
            .join(Pipeline, Pipeline.id == PipelineVersion.pipeline_id)
            .where(
                Pipeline.org_id == org_id,
                Pipeline.key == key,
                PipelineVersion.status == "published",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise Conflict(f"The {key.replace('_', ' ')} pipeline has no published version")
    return row


async def stages_of(session: AsyncSession, version_id: uuid.UUID) -> dict[str, Stage]:
    rows = (
        await session.execute(
            select(Stage).where(Stage.version_id == version_id).order_by(Stage.position)
        )
    ).scalars()
    return {s.key: s for s in rows}


async def transitions_of(
    session: AsyncSession, version_id: uuid.UUID
) -> dict[tuple[str, str], str]:
    rows = (
        await session.execute(
            select(StageTransition).where(StageTransition.version_id == version_id)
        )
    ).scalars()
    return {(t.from_stage_key, t.to_stage_key): t.kind for t in rows}


def snapshot_item(c: StageCriterion) -> dict[str, Any]:
    return {
        "key": c.key,
        "label": c.label,
        "help_text": c.help_text,
        "proof_type": c.proof_type,
        "proof_config": c.proof_config,
    }


async def snapshot_for(
    session: AsyncSession, version_id: uuid.UUID, stage_key: str
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(StageCriterion)
            .where(StageCriterion.version_id == version_id, StageCriterion.stage_key == stage_key)
            .order_by(StageCriterion.position)
        )
    ).scalars()
    return [snapshot_item(c) for c in rows]


async def current_entry(
    session: AsyncSession, deal_id: uuid.UUID, *, lock: bool = False
) -> DealStageEntry:
    stmt = select(DealStageEntry).where(
        DealStageEntry.deal_id == deal_id, DealStageEntry.exited_at.is_(None)
    )
    if lock:
        stmt = stmt.with_for_update()
    entry = (await session.execute(stmt)).scalar_one_or_none()
    if entry is None:
        raise Conflict("This deal is not in a stage")
    return entry


# ---------------------------------------------------------------------------- entering stages
async def _enter_stage(
    uow: UnitOfWork,
    deal: Deal,
    version: PipelineVersion,
    stage: Stage,
    *,
    exit_kind: str | None,
    reason: str | None,
    round_: int,
) -> DealStageEntry:
    """Close the current visit (if any) and open a new one with a fresh checklist snapshot."""
    s = uow.session
    now = utc_now()
    existing = (
        await s.execute(
            select(DealStageEntry).where(
                DealStageEntry.deal_id == deal.id, DealStageEntry.exited_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.exited_at, existing.exit_kind, existing.reason = now, exit_kind, reason
        await s.flush()
    entry = DealStageEntry(
        org_id=deal.org_id,
        deal_id=deal.id,
        stage_key=stage.key,
        version_id=version.id,
        revision_round=round_,
        entered_at=now,
        entered_by=uow.actor.id,
        checklist_snapshot=await snapshot_for(s, version.id, stage.key),
    )
    s.add(entry)
    deal.stage_key, deal.pipeline_version_id = stage.key, version.id
    deal.stage_entered_at, deal.revision_round = now, round_
    deal.version, deal.updated_at = deal.version + 1, now
    await s.flush()
    return entry


async def start_deal(uow: UnitOfWork, deal: Deal) -> None:
    """A new lead enters the entry stage of the published pipeline."""
    version = await published_version(uow.session, deal.org_id, deal.pipeline)
    stages = await stages_of(uow.session, version.id)
    entry_stage = next((st for st in stages.values() if st.is_entry), None)
    if entry_stage is None:
        raise Conflict("The pipeline has no entry stage")
    await _enter_stage(uow, deal, version, entry_stage, exit_kind=None, reason=None, round_=1)


# ---------------------------------------------------------------------------- the move
@dataclass
class MoveResult:
    deal_id: uuid.UUID
    kind: str
    from_stage: str
    to_stage: str
    overridden: list[str]
    won: bool


async def _unmet(session: AsyncSession, entry: DealStageEntry) -> list[dict[str, Any]]:
    """Criteria of this visit that are not validly ticked (never ticked, or proof missing)."""
    rows = {
        r.criterion_key: r
        for r in (
            await session.execute(select(DealCriterion).where(DealCriterion.entry_id == entry.id))
        ).scalars()
    }
    out = []
    for c in entry.checklist_snapshot:
        r = rows.get(c["key"])
        if r is None:
            out.append({"key": c["key"], "label": c["label"], "reason": "not ticked"})
        elif r.proof_missing and r.verify_status != "overridden":
            out.append({"key": c["key"], "label": c["label"], "reason": "proof missing"})
    return out


async def move(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    *,
    to_stage: str,
    reason: str | None,
    override: bool = False,
    expected_stage: str | None = None,
    expected_version: int | None = None,
) -> MoveResult:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        entity = ("deal", deal.id)
        if expected_stage is not None and deal.stage_key != expected_stage:
            raise Conflict(
                "This deal was moved by someone else. Reload and try again.",
                {"current_stage": deal.stage_key},
            )
        if expected_version is not None and deal.version != expected_version:
            raise Conflict(
                "This deal changed. Reload and try again.", {"current_version": deal.version}
            )
        if deal.status != "open":
            raise Conflict("Only open deals can change stage")
        version = await published_version(s, deal.org_id, deal.pipeline)
        stages = await stages_of(s, version.id)
        cur, to = stages.get(deal.stage_key), stages.get(to_stage)
        if cur is None:
            raise Conflict("This deal's stage no longer exists in the pipeline")
        if to is None:
            raise ValidationFailed("That stage does not exist", {"field": "to_stage"})
        trans = (await transitions_of(s, version.id)).get((cur.key, to.key))
        if to.key == cur.key and trans != "conditional":
            raise ValidationFailed("The deal is already in that stage")

        # what kind of move is this, and which permission decides it
        if trans == "backward":
            kind, key = "backward", "deal.move_backward"
        elif trans == "conditional":
            kind, key = "conditional", "deal.move_forward"
        elif trans == "forward" and not override:
            kind, key = "forward", "deal.move_forward"
        elif to.position > cur.position:
            kind, key = ("override" if trans == "forward" else "skip"), "deal.override_or_skip"
        else:
            raise ValidationFailed("That move is not allowed")
        await uow.authorize(key, entity=entity, owner_id=deal.owner_id)
        cleaned = uow.reason(key, reason)
        if kind in ("conditional", "override", "skip") and not cleaned:
            raise ReasonRequired("A reason is required for this move")

        entry = await current_entry(s, deal.id, lock=True)
        overridden: list[str] = []
        if kind == "forward":
            unmet = await _unmet(s, entry)
            if unmet:
                raise CriteriaIncomplete(
                    "Tick every exit criterion before moving forward", {"missing": unmet}
                )
            if to.entry_checks:
                ctx = await build_ctx(s, deal)
                problems: list[str] = []
                for chk in to.entry_checks:
                    res = await run_proof(ctx, chk["type"], chk.get("config", {}))
                    problems += res.missing
                if problems:
                    raise ProofRequired(
                        f"{to.name} needs more first", {"missing": [{"label": p} for p in problems]}
                    )
        elif kind in ("override", "skip"):
            for item in await _unmet(s, entry):
                row = (
                    await s.execute(
                        select(DealCriterion).where(
                            DealCriterion.entry_id == entry.id,
                            DealCriterion.criterion_key == item["key"],
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = DealCriterion(
                        org_id=deal.org_id,
                        deal_id=deal.id,
                        entry_id=entry.id,
                        criterion_key=item["key"],
                    )
                    s.add(row)
                row.ticked_by, row.proof_ref, row.proof_missing = None, None, False
                row.verify_status, row.verified_by = "overridden", actor.id
                row.verified_at = utc_now()
                row.verify_reason = f"Overridden by {actor.name}"
                overridden.append(item["key"])

        # revision rounds: Revision 2 is a new round; round 3+ stays in Revision 2
        if kind == "conditional":
            round_ = deal.revision_round + 1
        elif to.key == "revision_2" and cur.key != "revision_2":
            round_ = max(deal.revision_round, 2)  # skipping straight in still means round 2
        elif to.display_group == "revision" and cur.display_group != "revision":
            round_ = 1 if cur.position < to.position else max(deal.revision_round, 1)
        else:
            round_ = deal.revision_round

        exit_kind = {"conditional": "forward"}.get(kind, kind)
        old = {"stage": cur.name, "round": deal.revision_round}
        await _enter_stage(
            uow, deal, version, to, exit_kind=exit_kind, reason=cleaned, round_=round_
        )
        won = to.won_on_entry
        if won:
            deal.status, deal.won_at = "won", utc_now()
            # Phase 6: snapshot Won credit so a later reassignment never moves it.
            deal.won_by_user_id, deal.won_team_id = deal.owner_id, deal.team_id
            deal.won_value_paise = max(0, deal.value_paise)
        await s.flush()
        new: dict[str, Any] = {"stage": to.name, "round": round_, "kind": kind}
        if overridden:
            new["overridden"] = overridden
        await uow.audit(key, "deal", deal.id, old=old, new=new, reason=cleaned)
        from app.modules.pipeline.engine.nextstep import refresh_after_move

        # the new stage's first unmet criterion, unless a person chose the open next step
        await refresh_after_move(uow, deal)
        company = await company_name(s, deal)
        payload = {
            "deal_id": str(deal.id),
            "owner_id": str(deal.owner_id) if deal.owner_id else None,
            "team_id": str(deal.team_id) if deal.team_id else None,
            "company": company,
            "from": cur.key,
            "to": to.key,
            "kind": kind,
            "by": actor.name,
            "by_id": str(actor.id) if actor.id else None,
        }
        await uow.emit(events.DEAL_MOVED, payload, rt=deal_rt(deal))
        if overridden:
            await uow.emit(events.DEAL_OVERRIDDEN, payload)
        if won:
            await uow.emit(events.DEAL_WON, payload)
            await uow.emit(events.DEAL_CLOSED, payload)
        return MoveResult(deal.id, kind, cur.key, to.key, overridden, won)


# ---------------------------------------------------------------------------- criteria
def _find(snapshot: list[dict[str, Any]], key: str) -> dict[str, Any]:
    item = next((c for c in snapshot if c["key"] == key), None)
    if item is None:
        raise NotFound("That criterion is not part of this stage")
    return item


async def tick(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    key: str,
    *,
    proof_ref: dict[str, Any] | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("criterion.tick", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("criterion.tick", reason)
        if deal.status not in ("open", "won"):
            raise Conflict("This deal is not open")
        entry = await current_entry(s, deal.id, lock=True)
        item = _find(entry.checklist_snapshot, key)
        if (
            await s.execute(
                select(DealCriterion.id).where(
                    DealCriterion.entry_id == entry.id, DealCriterion.criterion_key == key
                )
            )
        ).first():
            raise Conflict("This criterion is already ticked")
        ctx = await build_ctx(s, deal)
        res = await run_proof(ctx, item["proof_type"], item["proof_config"], proof_ref)
        if not res.ok:
            raise ProofRequired(
                "The proof for this criterion is not there yet",
                {"criterion": key, "missing": [{"label": m} for m in res.missing]},
            )
        row = DealCriterion(
            org_id=deal.org_id,
            deal_id=deal.id,
            entry_id=entry.id,
            criterion_key=key,
            ticked_by=actor.id,
            proof_ref=res.ref,
        )
        s.add(row)
        await s.flush()
        await uow.audit(
            "criterion.tick",
            "deal",
            deal.id,
            new={"stage": entry.stage_key, "criterion": item["label"], "proof": res.ref},
            reason=cleaned,
        )
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
        if entry.stage_key == "onboarding" and await _entry_complete(s, entry):
            await uow.emit(
                events.ONBOARDING_COMPLETED,
                {
                    "deal_id": str(deal.id),
                    "owner_id": str(deal.owner_id) if deal.owner_id else None,
                    "team_id": str(deal.team_id) if deal.team_id else None,
                    "company": await company_name(s, deal),
                },
            )
        return row.id


async def untick(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, key: str, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("criterion.untick", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("criterion.untick", reason)
        entry = await current_entry(s, deal.id, lock=True)
        row = (
            await s.execute(
                select(DealCriterion)
                .where(DealCriterion.entry_id == entry.id, DealCriterion.criterion_key == key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.verify_status == "overridden":
            raise NotFound("That criterion is not ticked")
        label = _find(entry.checklist_snapshot, key)["label"]
        await s.delete(row)
        await s.flush()
        await recheck_proofs(uow, deal)  # the warning may have been about this very tick
        await uow.audit(
            "criterion.untick",
            "deal",
            deal.id,
            old={"stage": entry.stage_key, "criterion": label, "verify": row.verify_status},
            reason=cleaned,
        )
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def verify(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    key: str,
    *,
    decision: str,
    reason: str | None,
    entry_id: uuid.UUID | None = None,
) -> None:
    """Mark a tick Verified, or Rejected with a reason (the tick is then removed)."""
    perm = "criterion.verify" if decision == "verify" else "criterion.reject"
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize(perm, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(perm, reason)
        if decision == "reject" and not cleaned:
            raise ReasonRequired("A rejection needs a reason")
        stmt = select(DealCriterion).where(
            DealCriterion.deal_id == deal.id, DealCriterion.criterion_key == key
        )
        if entry_id is not None:
            stmt = stmt.where(DealCriterion.entry_id == entry_id)
        row = (
            await s.execute(
                stmt.order_by(DealCriterion.ticked_at.desc()).limit(1).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("That criterion is not ticked")
        if row.verify_status == "overridden":
            raise Conflict("This criterion was overridden, not ticked")
        entry = await s.get(DealStageEntry, row.entry_id)
        assert entry is not None
        item = next((c for c in entry.checklist_snapshot if c["key"] == key), {"label": key})
        stage_passed = entry.exited_at is not None
        now = utc_now()
        company = await company_name(s, deal)
        base = {
            "deal_id": str(deal.id),
            "owner_id": str(deal.owner_id) if deal.owner_id else None,
            "team_id": str(deal.team_id) if deal.team_id else None,
            "company": company,
            "criterion": item["label"],
            "by": actor.name,
        }
        if decision == "verify":
            row.verify_status, row.verified_by, row.verified_at = "verified", actor.id, now
            row.verify_reason = cleaned
            await uow.audit(
                "criterion.verify",
                "deal",
                deal.id,
                new={"stage": entry.stage_key, "criterion": item["label"]},
                reason=cleaned,
            )
        else:
            await s.delete(row)
            await s.flush()
            await recheck_proofs(uow, deal)
            await uow.audit(
                "criterion.reject",
                "deal",
                deal.id,
                old={"stage": entry.stage_key, "criterion": item["label"]},
                new={"rejected": True},
                reason=cleaned,
            )
            await uow.emit(events.CRITERION_REJECTED, {**base, "reason": cleaned})
            if stage_passed:
                flags = dict(deal.flags or {})
                flags["review_needed"] = True
                deal.flags = flags
                await uow.emit(events.DEAL_REVIEW_NEEDED, base)
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def clear_review(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, reason: str | None
) -> None:
    """The Team Leader (or above) has looked at a deal flagged 'review needed'."""
    async with UnitOfWork(actor, request) as uow:
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("criterion.verify", entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason("criterion.verify", reason)
        flags = dict(deal.flags or {})
        if not flags.pop("review_needed", None):
            raise Conflict("This deal is not waiting for a review")
        deal.flags = flags
        await uow.audit("criterion.verify", "deal", deal.id, note="Review done", reason=cleaned)
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


# ---------------------------------------------------------------------------- proof missing
async def recheck_proofs(uow: UnitOfWork, deal: Deal) -> None:
    """Call (with the deal locked) after anything that is proof changes: an activity, a document,
    a deal field, a product line, a contact tag. Ticks whose proof is gone are marked 'Proof
    missing'; the mark clears by itself when the proof comes back."""
    s = uow.session
    rows = list(
        (
            await s.execute(
                select(DealCriterion).where(
                    DealCriterion.deal_id == deal.id, DealCriterion.verify_status != "overridden"
                )
            )
        ).scalars()
    )
    flags = dict(deal.flags or {})
    if not rows:
        if flags.pop("proof_missing", None):
            deal.flags = flags
        return
    entries = {
        e.id: e
        for e in (
            await s.execute(
                select(DealStageEntry).where(DealStageEntry.id.in_({r.entry_id for r in rows}))
            )
        ).scalars()
    }
    ctx = await build_ctx(s, deal, recheck=True)
    newly_missing: list[str] = []
    any_missing = False
    for r in rows:
        item = next(
            (c for c in entries[r.entry_id].checklist_snapshot if c["key"] == r.criterion_key), None
        )
        if item is None:
            continue
        res = await run_proof(ctx, item["proof_type"], item["proof_config"], r.proof_ref)
        if res.ok and res.ref and res.ref != r.proof_ref:
            r.proof_ref = res.ref  # the proof moved to another qualifying record
        if not res.ok and not r.proof_missing:
            newly_missing.append(item["label"])
        r.proof_missing = not res.ok
        any_missing = any_missing or not res.ok
    if any_missing != bool(flags.get("proof_missing")):
        if any_missing:
            flags["proof_missing"] = True
        else:
            flags.pop("proof_missing", None)
        deal.flags = flags
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
    if newly_missing:
        await uow.emit(
            events.DEAL_PROOF_MISSING,
            {
                "deal_id": str(deal.id),
                "owner_id": str(deal.owner_id) if deal.owner_id else None,
                "team_id": str(deal.team_id) if deal.team_id else None,
                "company": await company_name(s, deal),
                "criteria": newly_missing,
            },
        )


# ---------------------------------------------------------------------------- reading
async def _entry_complete(session: AsyncSession, entry: DealStageEntry) -> bool:
    """Every exit criterion of this visit has a tick."""
    keys = {c["key"] for c in entry.checklist_snapshot}
    if not keys:
        return False
    ticked = set(
        (
            await session.execute(
                select(DealCriterion.criterion_key).where(DealCriterion.entry_id == entry.id)
            )
        ).scalars()
    )
    return keys <= ticked


async def onboarding_done(session: AsyncSession, deal: Deal) -> bool:
    """The deal is in Onboarding and every Onboarding exit criterion is ticked."""
    if deal.stage_key != "onboarding":
        return False
    entry = (
        await session.execute(
            select(DealStageEntry)
            .where(DealStageEntry.deal_id == deal.id, DealStageEntry.stage_key == "onboarding")
            .order_by(DealStageEntry.entered_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return entry is not None and await _entry_complete(session, entry)


async def stage_history(
    session: AsyncSession, actor: Actor, deal_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every stage visit of a deal, oldest first, each with the checklist it had and what was
    ticked (who, when, the meeting or the values behind it), so an earlier stage can be looked at
    from any later stage. Read-only."""
    from app.modules.activities.models import Activity
    from app.modules.pipeline.engine.proofs import field_value, filled
    from app.modules.pipeline.engine.proofs import label as field_label
    from app.platform.reads import authorize_read

    deal = await session.get(Deal, deal_id)
    if deal is None or deal.deleted_at is not None:
        raise NotFound("Deal not found")
    await authorize_read(session, actor, "deal.view", entity=("deal", deal.id))
    entries = list(
        (
            await session.execute(
                select(DealStageEntry)
                .where(DealStageEntry.deal_id == deal.id)
                .order_by(DealStageEntry.entered_at)
            )
        ).scalars()
    )
    ticks: dict[uuid.UUID, dict[str, DealCriterion]] = {}
    for tick_row in (
        await session.execute(select(DealCriterion).where(DealCriterion.deal_id == deal.id))
    ).scalars():
        ticks.setdefault(tick_row.entry_id, {})[tick_row.criterion_key] = tick_row
    activity_ids = {
        uuid.UUID(str(r.proof_ref["activity_id"]))
        for per in ticks.values()
        for r in per.values()
        if r.proof_ref and r.proof_ref.get("activity_id")
    }
    acts = (
        {
            a.id: a
            for a in (
                await session.execute(select(Activity).where(Activity.id.in_(activity_ids)))
            ).scalars()
        }
        if activity_ids
        else {}
    )
    people = await names(
        session,
        {r.ticked_by for per in ticks.values() for r in per.values()}
        | {e.entered_by for e in entries},
    )
    ctx = await build_ctx(session, deal)
    stage_names: dict[uuid.UUID, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for e in entries:
        if e.version_id not in stage_names:
            stage_names[e.version_id] = await stages_of(session, e.version_id)
        st = stage_names[e.version_id].get(e.stage_key)
        per = ticks.get(e.id, {})
        crit: list[dict[str, Any]] = []
        for c in e.checklist_snapshot:
            r = per.get(c["key"])
            details: list[dict[str, str]] = []
            cfg = c.get("proof_config") or {}
            fields = list(cfg.get("require", [])) if c["proof_type"] in ("fields", "field") else []
            for extra in cfg.get("also", []):
                if extra.get("type") in ("fields", "field"):
                    fields += list(extra.get("config", {}).get("require", []))
            names_: list[str] = []
            for f in fields:  # a requirement is a field name or {"any": [names]}
                names_ += [str(n) for n in f.get("any", [])] if isinstance(f, dict) else [str(f)]
            for f in dict.fromkeys(names_):
                v = field_value(ctx, f)
                if filled(v):
                    shown = (
                        format_rupees(int(v))
                        if f.endswith("_paise") and isinstance(v, int)
                        else str(v)
                    )
                    details.append({"label": field_label(f), "value": shown})
            meeting = None
            if r and r.proof_ref and r.proof_ref.get("activity_id"):
                a = acts.get(uuid.UUID(str(r.proof_ref["activity_id"])))
                if a is not None:
                    meeting = {
                        "type": a.type,
                        "at": a.occurred_at,
                        "status": a.meeting_status,
                        "attendees": a.attendees,
                        "discussed": a.discussed,
                    }
            crit.append(
                {
                    "key": c["key"],
                    "label": c["label"],
                    "ticked": r is not None,
                    "status": r.verify_status if r else None,
                    "ticked_by": people.get(r.ticked_by) if r and r.ticked_by else None,
                    "ticked_at": r.ticked_at if r else None,
                    "proof_missing": bool(r and r.proof_missing),
                    "details": details if r else [],
                    "activity": meeting,
                }
            )
        out.append(
            {
                "entry_id": e.id,
                "stage_key": e.stage_key,
                "stage_name": st.name if st else e.stage_key,
                "round": e.revision_round,
                "entered_at": e.entered_at,
                "entered_by": people.get(e.entered_by) if e.entered_by else None,
                "exited_at": e.exited_at,
                "exit_kind": e.exit_kind,
                "reason": e.reason,
                "current": e.exited_at is None,
                "criteria": crit,
            }
        )
    return out


async def progress(session: AsyncSession, deal: Deal) -> dict[str, Any]:
    """The deal page's stage bar and checklist: this visit's criteria with their state, and
    whether each unticked one could be ticked right now."""
    stages = {}
    if deal.pipeline_version_id:
        stages = await stages_of(session, deal.pipeline_version_id)
    published = await published_version(session, deal.org_id, deal.pipeline)
    live = await stages_of(session, published.id)
    entry = (
        await session.execute(
            select(DealStageEntry).where(
                DealStageEntry.deal_id == deal.id, DealStageEntry.exited_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if entry is None:  # won deals keep their last open visit; lost deals do not
        entry = (
            await session.execute(
                select(DealStageEntry)
                .where(DealStageEntry.deal_id == deal.id)
                .order_by(DealStageEntry.entered_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    criteria: list[dict[str, Any]] = []
    if entry is not None:
        rows = {
            r.criterion_key: r
            for r in (
                await session.execute(
                    select(DealCriterion).where(DealCriterion.entry_id == entry.id)
                )
            ).scalars()
        }
        who = await names(
            session, {r.ticked_by for r in rows.values()} | {r.verified_by for r in rows.values()}
        )
        ctx = await build_ctx(session, deal) if deal.status == "open" else None
        for c in entry.checklist_snapshot:
            r = rows.get(c["key"])
            item: dict[str, Any] = {
                "key": c["key"],
                "label": c["label"],
                "help_text": c.get("help_text"),
                "proof_type": c["proof_type"],
                "ticked": r is not None,
                "status": r.verify_status if r else None,
                "ticked_by": who.get(r.ticked_by) if r and r.ticked_by else None,
                "ticked_at": r.ticked_at if r else None,
                "verified_by": who.get(r.verified_by) if r and r.verified_by else None,
                "verify_reason": r.verify_reason if r else None,
                "proof_missing": bool(r and r.proof_missing),
                "ready": None,
                "missing": [],
            }
            if r is None and ctx is not None:
                res = await run_proof(ctx, c["proof_type"], c["proof_config"])
                item["ready"], item["missing"] = res.ok, res.missing
            criteria.append(item)
    order = [
        {
            "key": st.key,
            "name": st.name,
            "group": st.display_group,
            "position": st.position,
            "probability_pct": st.probability_pct,
            "conditional": st.is_conditional,
        }
        for st in (stages or live).values()
    ]
    return {
        "stages": order,
        "current": deal.stage_key,
        "entry_id": entry.id if entry else None,
        "criteria": criteria,
    }
