# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pipeline configuration without a developer (PRD 5, Feature 2): edit a draft, validate it,
preview the impact, publish it as a new version, roll back.

Published versions are frozen (a database trigger enforces it) and kept forever. Only deals that
enter a stage after publishing get the new checklist; a deal already in a stage keeps its copy.
"""

import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.leads.models import Deal
from app.modules.leads.ownership import names
from app.modules.pipeline.config.models import (
    CustomFieldDef,
    FitItem,
    LostReason,
    Pipeline,
    PipelineVersion,
    Stage,
    StageCriterion,
    StageTransition,
)
from app.modules.pipeline.engine.proofs import valid_proof_config
from app.modules.pipeline.engine.service import _enter_stage, current_entry
from app.platform.context import Actor, RequestInfo
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

KEY = "pipeline.config"
LISTS_KEY = "custom_fields_and_reason_lists.manage"


# ---------------------------------------------------------------------------- reading
async def _pipeline(session: AsyncSession, org_id: uuid.UUID, key: str) -> Pipeline:
    row = (
        await session.execute(
            select(Pipeline).where(Pipeline.org_id == org_id, Pipeline.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Pipeline not found")
    return row


async def _version(
    session: AsyncSession, pipeline: Pipeline, version_no: int, lock: bool = False
) -> PipelineVersion:
    stmt = select(PipelineVersion).where(
        PipelineVersion.pipeline_id == pipeline.id, PipelineVersion.version_no == version_no
    )
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound("Version not found")
    return row


async def _children(
    session: AsyncSession, version_id: uuid.UUID
) -> tuple[list[Stage], list[StageTransition], list[StageCriterion]]:
    stages = list(
        (
            await session.execute(
                select(Stage).where(Stage.version_id == version_id).order_by(Stage.position)
            )
        ).scalars()
    )
    trans = list(
        (
            await session.execute(
                select(StageTransition)
                .where(StageTransition.version_id == version_id)
                .order_by(StageTransition.from_stage_key, StageTransition.to_stage_key)
            )
        ).scalars()
    )
    crit = list(
        (
            await session.execute(
                select(StageCriterion)
                .where(StageCriterion.version_id == version_id)
                .order_by(StageCriterion.stage_key, StageCriterion.position)
            )
        ).scalars()
    )
    return stages, trans, crit


def _summary(v: PipelineVersion, who: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": v.id,
        "version_no": v.version_no,
        "status": v.status,
        "notes": v.notes,
        "created_at": v.created_at,
        "published_at": v.published_at,
        "published_by_name": who.get(v.published_by) if v.published_by else None,
        "diff_summary": v.diff_summary,
    }


async def list_versions(session: AsyncSession, actor: Actor, key: str) -> list[dict[str, Any]]:
    await authorize_read(session, actor, KEY)
    p = await _pipeline(session, actor.org_id, key)
    rows = list(
        (
            await session.execute(
                select(PipelineVersion)
                .where(PipelineVersion.pipeline_id == p.id)
                .order_by(PipelineVersion.version_no.desc())
            )
        ).scalars()
    )
    who = await names(session, {r.published_by for r in rows})
    return [_summary(r, who) for r in rows]


async def get_version(
    session: AsyncSession, actor: Actor, key: str, version_no: int
) -> dict[str, Any]:
    await authorize_read(session, actor, KEY)
    p = await _pipeline(session, actor.org_id, key)
    v = await _version(session, p, version_no)
    return await _detail(session, p, v)


async def _detail(session: AsyncSession, p: Pipeline, v: PipelineVersion) -> dict[str, Any]:
    stages, trans, crit = await _children(session, v.id)
    who = await names(session, {v.published_by})
    return {
        **_summary(v, who),
        "pipeline_key": p.key,
        "stages": [
            {
                "id": s.id,
                "key": s.key,
                "name": s.name,
                "position": s.position,
                "probability_pct": s.probability_pct,
                "is_entry": s.is_entry,
                "is_final": s.is_final,
                "won_on_entry": s.won_on_entry,
                "is_conditional": s.is_conditional,
                "display_group": s.display_group,
                "entry_checks": s.entry_checks,
            }
            for s in stages
        ],
        "transitions": [
            {"from_stage_key": t.from_stage_key, "to_stage_key": t.to_stage_key, "kind": t.kind}
            for t in trans
        ],
        "criteria": [
            {
                "id": c.id,
                "stage_key": c.stage_key,
                "key": c.key,
                "label": c.label,
                "help_text": c.help_text,
                "position": c.position,
                "proof_type": c.proof_type,
                "proof_config": c.proof_config,
            }
            for c in crit
        ],
    }


# ---------------------------------------------------------------------------- draft
async def _replace_children(
    session: AsyncSession,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    stages: list[Any],
    transitions: list[Any],
    criteria: list[Any],
) -> None:
    for model in (StageCriterion, StageTransition, Stage):
        await session.execute(delete(model).where(model.version_id == version_id))
    for pos, st in enumerate(stages, start=1):
        session.add(
            Stage(
                org_id=org_id,
                version_id=version_id,
                key=st.key,
                name=st.name.strip(),
                position=pos,
                probability_pct=st.probability_pct,
                is_entry=st.is_entry,
                is_final=st.is_final,
                won_on_entry=st.won_on_entry,
                is_conditional=st.is_conditional,
                display_group=st.display_group or st.key,
                entry_checks=st.entry_checks,
            )
        )
    for t in transitions:
        session.add(
            StageTransition(
                org_id=org_id,
                version_id=version_id,
                from_stage_key=t.from_stage_key,
                to_stage_key=t.to_stage_key,
                kind=t.kind,
            )
        )
    counters: dict[str, int] = {}
    for c in criteria:
        counters[c.stage_key] = counters.get(c.stage_key, 0) + 1
        session.add(
            StageCriterion(
                org_id=org_id,
                version_id=version_id,
                stage_key=c.stage_key,
                key=c.key,
                label=c.label.strip(),
                help_text=c.help_text,
                position=counters[c.stage_key],
                proof_type=c.proof_type,
                proof_config=c.proof_config,
            )
        )
    await session.flush()


def _check_shape(stages: list[Any], transitions: list[Any], criteria: list[Any]) -> None:
    keys = [s.key for s in stages]
    if len(set(keys)) != len(keys):
        raise ValidationFailed("Two stages have the same key")
    pairs = {(t.from_stage_key, t.to_stage_key) for t in transitions}
    if len(pairs) != len(transitions):
        raise ValidationFailed("A transition is listed twice")
    ck = {(c.stage_key, c.key) for c in criteria}
    if len(ck) != len(criteria):
        raise ValidationFailed("Two criteria in one stage have the same key")


async def _draft_of(session: AsyncSession, p: Pipeline) -> PipelineVersion | None:
    return (
        await session.execute(
            select(PipelineVersion)
            .where(PipelineVersion.pipeline_id == p.id, PipelineVersion.status == "draft")
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _new_version(
    uow: UnitOfWork, p: Pipeline, notes: str | None, status: str = "draft"
) -> PipelineVersion:
    n = (
        await uow.session.execute(
            select(func.coalesce(func.max(PipelineVersion.version_no), 0)).where(
                PipelineVersion.pipeline_id == p.id
            )
        )
    ).scalar_one()
    v = PipelineVersion(
        org_id=p.org_id,
        pipeline_id=p.id,
        version_no=n + 1,
        status=status,
        created_by=uow.actor.id,
        notes=notes,
    )
    uow.session.add(v)
    await uow.session.flush()
    return v


async def _copy_children(session: AsyncSession, src: PipelineVersion, dst: PipelineVersion) -> None:
    stages, trans, crit = await _children(session, src.id)
    plain = [
        SimpleNamespace(**{c.name: getattr(s, c.name) for c in Stage.__table__.columns})
        for s in stages
    ]
    await _replace_children(session, src.org_id, dst.id, plain, trans, crit)


async def save_draft(actor: Actor, request: RequestInfo, key: str, body: Any) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(KEY)
        cleaned = uow.reason(KEY, body.reason)
        p = await _pipeline(s, actor.org_id, key)
        await uow.advisory_lock(f"pipeline:{key}")
        draft = await _draft_of(s, p)
        created = draft is None
        if draft is None:
            draft = await _new_version(uow, p, body.notes)
            src_no = body.from_version_no
            src = (
                await _version(s, p, src_no)
                if src_no is not None
                else await _published_or_none(s, p)
            )
            if src is not None and body.stages is None:
                await _copy_children(s, src, draft)
        elif body.from_version_no is not None and body.stages is None:
            raise Conflict("A draft already exists. Edit it, or discard it first.")
        if body.stages is not None:
            _check_shape(body.stages, body.transitions or [], body.criteria or [])
            await _replace_children(
                s, actor.org_id, draft.id, body.stages, body.transitions or [], body.criteria or []
            )
        if body.notes is not None:
            draft.notes = body.notes
        await uow.audit(
            KEY,
            "pipeline_version",
            draft.id,
            new={"pipeline": key, "version": draft.version_no, "created": created},
            reason=cleaned,
        )
        return await _detail(s, p, draft)


async def _published_or_none(session: AsyncSession, p: Pipeline) -> PipelineVersion | None:
    return (
        await session.execute(
            select(PipelineVersion).where(
                PipelineVersion.pipeline_id == p.id, PipelineVersion.status == "published"
            )
        )
    ).scalar_one_or_none()


async def discard_draft(actor: Actor, request: RequestInfo, key: str, reason: str | None) -> None:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize(KEY)
        cleaned = uow.reason(KEY, reason)
        p = await _pipeline(uow.session, actor.org_id, key)
        draft = await _draft_of(uow.session, p)
        if draft is None:
            raise NotFound("There is no draft")
        draft.status = "archived"  # versions are never erased
        await uow.audit(KEY, "pipeline_version", draft.id, note="Draft discarded", reason=cleaned)


# ---------------------------------------------------------------------------- validate
async def _validate(session: AsyncSession, v: PipelineVersion) -> list[dict[str, Any]]:
    stages, trans, crit = await _children(session, v.id)
    problems: list[dict[str, Any]] = []

    def add(code: str, msg: str, stage: str | None = None) -> None:
        problems.append({"code": code, "message": msg, "stage_key": stage})

    if not stages:
        add("no_stages", "The pipeline has no stages")
        return problems
    keys = {s.key for s in stages}
    entries = [s for s in stages if s.is_entry]
    if len(entries) != 1:
        add("entry_stage", f"There must be exactly one entry stage (found {len(entries)})")
    if not any(s.won_on_entry for s in stages):
        add("no_won_stage", "No stage is marked as 'won on entry'")
    for t in trans:
        for end in (t.from_stage_key, t.to_stage_key):
            if end not in keys:
                add("bad_transition", f"A transition points at an unknown stage '{end}'")
    if entries:
        seen = {entries[0].key}
        queue = [entries[0].key]
        while queue:
            cur = queue.pop()
            for t in trans:
                if (
                    t.from_stage_key == cur
                    and t.to_stage_key in keys
                    and t.to_stage_key not in seen
                ):
                    seen.add(t.to_stage_key)
                    queue.append(t.to_stage_key)
        for st in stages:
            if st.key not in seen:
                add("unreachable", f"'{st.name}' cannot be reached from the entry stage", st.key)
    for st in stages:
        if not st.is_final and not any(
            t.from_stage_key == st.key and t.kind == "forward" and t.to_stage_key != st.key
            for t in trans
        ):
            add("no_forward", f"'{st.name}' has no way forward", st.key)
        for chk in st.entry_checks:
            bad = valid_proof_config(chk.get("type", ""), chk.get("config", {}))
            if bad:
                add("bad_entry_check", f"Entry check of '{st.name}': {bad}", st.key)
    for c in crit:
        if c.stage_key not in keys:
            add("bad_criterion", f"Criterion '{c.label}' is on an unknown stage", c.stage_key)
            continue
        bad = valid_proof_config(c.proof_type, c.proof_config)
        if bad:
            add("bad_proof", f"Criterion '{c.label}': {bad}", c.stage_key)
    return problems


async def validate(
    session: AsyncSession, actor: Actor, key: str, version_no: int
) -> dict[str, Any]:
    await authorize_read(session, actor, KEY)
    p = await _pipeline(session, actor.org_id, key)
    v = await _version(session, p, version_no)
    problems = await _validate(session, v)
    return {"ok": not problems, "problems": problems}


# ---------------------------------------------------------------------------- impact
# A Won deal still sits in its stage (Onboarding) and is worked on there, so it counts as well.
_ACTIVE = or_(
    Deal.status.in_(("open", "pooled")), and_(Deal.status == "won", Deal.won_at.is_not(None))
)


async def _open_by_stage(
    session: AsyncSession, org_id: uuid.UUID, pipeline_key: str
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Deal.stage_key, func.count())
            .where(
                Deal.org_id == org_id,
                Deal.pipeline == pipeline_key,
                _ACTIVE,
                Deal.deleted_at.is_(None),
            )
            .group_by(Deal.stage_key)
        )
    ).all()
    return {k: n for k, n in rows}


async def impact(session: AsyncSession, actor: Actor, key: str, version_no: int) -> dict[str, Any]:
    await authorize_read(session, actor, KEY)
    p = await _pipeline(session, actor.org_id, key)
    v = await _version(session, p, version_no)
    draft_stages = {s.key: s for s in (await _children(session, v.id))[0]}
    live = await _published_or_none(session, p)
    live_stages = {s.key: s for s in (await _children(session, live.id))[0]} if live else {}
    counts = await _open_by_stage(session, actor.org_id, key)
    out = []
    for k, st in {**live_stages, **draft_stages}.items():
        out.append(
            {
                "key": k,
                "name": (draft_stages.get(k) or st).name,
                "open_deals": counts.get(k, 0),
                "in_draft": k in draft_stages,
            }
        )
    needs = [k for k in live_stages if k not in draft_stages and counts.get(k, 0) > 0]
    return {"stages": out, "needs_mapping": needs}


# ---------------------------------------------------------------------------- publish
def _diff(
    old: tuple[list[Stage], list[StageCriterion]], new: tuple[list[Stage], list[StageCriterion]]
) -> dict[str, Any]:
    o_st, n_st = {s.key: s for s in old[0]}, {s.key: s for s in new[0]}
    o_cr = {(c.stage_key, c.key): c for c in old[1]}
    n_cr = {(c.stage_key, c.key): c for c in new[1]}
    return {
        "stages_added": sorted(set(n_st) - set(o_st)),
        "stages_removed": sorted(set(o_st) - set(n_st)),
        "stages_renamed": sorted(k for k in n_st if k in o_st and n_st[k].name != o_st[k].name),
        "probability_changed": sorted(
            k for k in n_st if k in o_st and n_st[k].probability_pct != o_st[k].probability_pct
        ),
        "criteria_added": sorted(f"{a}.{b}" for a, b in set(n_cr) - set(o_cr)),
        "criteria_removed": sorted(f"{a}.{b}" for a, b in set(o_cr) - set(n_cr)),
        "criteria_changed": sorted(
            f"{a}.{b}"
            for (a, b) in set(n_cr) & set(o_cr)
            if (n_cr[(a, b)].label, n_cr[(a, b)].proof_type, n_cr[(a, b)].proof_config)
            != (o_cr[(a, b)].label, o_cr[(a, b)].proof_type, o_cr[(a, b)].proof_config)
        ),
    }


async def _publish_draft(
    uow: UnitOfWork,
    p: Pipeline,
    draft: PipelineVersion,
    mapping: dict[str, str],
    reason: str | None,
) -> PipelineVersion:
    s = uow.session
    problems = await _validate(s, draft)
    if problems:
        raise ValidationFailed("The draft is not ready to publish", {"problems": problems})
    new_stages = {st.key: st for st in (await _children(s, draft.id))[0]}
    live = await _published_or_none(s, p)
    old_children: tuple[list[Stage], list[StageCriterion]] = ([], [])
    removed: list[str] = []
    if live is not None:
        o_stages, _, o_crit = await _children(s, live.id)
        old_children = (o_stages, o_crit)
        removed = [st.key for st in o_stages if st.key not in new_stages]
    if live is not None:
        # wait for moves in flight (they hold the deal row) and keep new ones out until we commit.
        # A move locks a single deal, so locking in id order cannot deadlock with it.
        await s.execute(
            select(Deal.id)
            .where(
                Deal.org_id == p.org_id,
                Deal.pipeline == p.key,
                Deal.deleted_at.is_(None),
                or_(
                    Deal.status.in_(("open", "pooled")),
                    and_(Deal.status == "won", Deal.stage_key.in_(removed or [""])),
                ),
            )
            .order_by(Deal.id)
            .with_for_update()
        )
    counts = await _open_by_stage(s, p.org_id, p.key)
    for rk in removed:
        if counts.get(rk, 0) > 0:
            target = mapping.get(rk)
            if target is None or target not in new_stages:
                raise ValidationFailed(
                    f"Stage '{rk}' still holds deals. Choose a stage to move them to.",
                    {"needs_mapping": [r for r in removed if counts.get(r, 0) > 0]},
                )
    # move the deals of removed stages, logging each move
    for rk in removed:
        if counts.get(rk, 0) == 0:
            continue
        target_stage = new_stages[mapping[rk]]
        deals = list(
            (
                await s.execute(
                    select(Deal)
                    .where(
                        Deal.org_id == p.org_id,
                        Deal.pipeline == p.key,
                        Deal.stage_key == rk,
                        _ACTIVE,
                        Deal.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for d in deals:
            await current_entry(s, d.id, lock=True)
            await _enter_stage(
                uow, d, draft, target_stage, exit_kind="override", reason="config move", round_=1
            )
            await uow.audit(
                KEY,
                "deal",
                d.id,
                old={"stage": rk},
                new={"stage": target_stage.key},
                reason=reason,
                note="config move",
            )
    now = utc_now()
    if live is not None:
        live.status = "archived"
        await s.flush()
    draft.status, draft.published_by, draft.published_at = "published", uow.actor.id, now
    draft.diff_summary = _diff(
        old_children, (list(new_stages.values()), (await _children(s, draft.id))[2])
    )
    await s.flush()
    await uow.audit(
        KEY,
        "pipeline_version",
        draft.id,
        new={"pipeline": p.key, "version": draft.version_no, "diff": draft.diff_summary},
        reason=reason,
    )
    await uow.emit("pipeline.published", {"pipeline": p.key, "version": draft.version_no})
    return draft


async def publish(
    actor: Actor, request: RequestInfo, key: str, version_no: int, body: Any
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize(KEY)
        cleaned = uow.reason(KEY, body.reason)
        await uow.advisory_lock(f"pipeline:{key}")
        p = await _pipeline(uow.session, actor.org_id, key)
        v = await _version(uow.session, p, version_no, lock=True)
        if v.status != "draft":
            raise Conflict("Only the draft can be published")
        done = await _publish_draft(uow, p, v, body.stage_mapping, cleaned)
        return await _detail(uow.session, p, done)


async def rollback(
    actor: Actor, request: RequestInfo, key: str, version_no: int, body: Any
) -> dict[str, Any]:
    """Publish an earlier version again, as a new version number."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(KEY)
        cleaned = uow.reason(KEY, body.reason)
        await uow.advisory_lock(f"pipeline:{key}")
        p = await _pipeline(s, actor.org_id, key)
        src = await _version(s, p, version_no)
        if src.status == "draft":
            raise Conflict("A draft cannot be rolled back to")
        if await _draft_of(s, p) is not None:
            raise Conflict("Publish or discard the current draft first")
        draft = await _new_version(uow, p, f"Rollback to version {src.version_no}")
        await _copy_children(s, src, draft)
        done = await _publish_draft(uow, p, draft, body.stage_mapping, cleaned)
        return await _detail(s, p, done)


# ---------------------------------------------------------------------------- settings lists
async def list_custom_fields(session: AsyncSession, actor: Actor) -> list[CustomFieldDef]:
    await authorize_read(session, actor, "deal.view")
    return list(
        (
            await session.execute(
                select(CustomFieldDef)
                .where(CustomFieldDef.org_id == actor.org_id)
                .order_by(CustomFieldDef.entity, CustomFieldDef.key)
            )
        ).scalars()
    )


async def put_custom_fields(
    actor: Actor, request: RequestInfo, items: list[Any], reason: str | None
) -> list[CustomFieldDef]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(LISTS_KEY)
        cleaned = uow.reason(LISTS_KEY, reason)
        if len({(i.entity, i.key) for i in items}) != len(items):
            raise ValidationFailed("Two fields have the same key")
        for it in items:
            if it.type == "dropdown" and not it.options:
                raise ValidationFailed(f"'{it.label}' needs at least one option")
        existing = {
            (d.entity, d.key): d
            for d in (
                await s.execute(select(CustomFieldDef).where(CustomFieldDef.org_id == actor.org_id))
            ).scalars()
        }
        sent = {(i.entity, i.key) for i in items}
        for it in items:
            row = existing.get((it.entity, it.key))
            if row is None:
                s.add(
                    CustomFieldDef(
                        org_id=actor.org_id,
                        entity=it.entity,
                        key=it.key,
                        label=it.label,
                        type=it.type,
                        options=it.options,
                        active=it.active,
                    )
                )
            else:
                if row.type != it.type:
                    raise ValidationFailed(f"The type of '{it.key}' cannot change")
                row.label, row.options, row.active = it.label, it.options, it.active
        for k, row in existing.items():
            if k not in sent:
                row.active = False  # never erased
        await s.flush()
        await uow.audit(
            LISTS_KEY, "settings", "custom_fields", new={"fields": len(items)}, reason=cleaned
        )
    async with get_sessionmaker()() as s2:
        return await list_custom_fields(s2, actor)


async def _list_rows(session: AsyncSession, actor: Actor, model: Any) -> list[Any]:
    await authorize_read(session, actor, "deal.view")
    return list(
        (
            await session.execute(
                select(model).where(model.org_id == actor.org_id).order_by(model.position)
            )
        ).scalars()
    )


async def list_lost_reasons(session: AsyncSession, actor: Actor) -> list[LostReason]:
    return await _list_rows(session, actor, LostReason)


async def list_fit_items(session: AsyncSession, actor: Actor) -> list[FitItem]:
    return await _list_rows(session, actor, FitItem)


async def _put_list(
    actor: Actor, request: RequestInfo, model: Any, name: str, items: list[Any], reason: str | None
) -> list[Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(LISTS_KEY)
        cleaned = uow.reason(LISTS_KEY, reason)
        rows = {
            r.id: r
            for r in (await s.execute(select(model).where(model.org_id == actor.org_id))).scalars()
        }
        labels = [i.label.strip().lower() for i in items]
        if len(set(labels)) != len(labels):
            raise ValidationFailed("Two entries have the same text")
        kept: set[uuid.UUID] = set()
        by_label = {r.label.strip().lower(): r for r in rows.values()}
        for pos, it in enumerate(items, start=1):
            row = rows.get(it.id) if it.id else None
            if row is None:
                row = by_label.get(
                    it.label.strip().lower()
                )  # a hidden entry typed again is revived
                if row is not None and row.id in kept:
                    row = None
            if row is None:
                s.add(model(org_id=actor.org_id, label=it.label.strip(), position=pos))
            else:
                row.label, row.position, row.active = it.label.strip(), pos, True
                kept.add(row.id)
        for rid, row in rows.items():
            if rid not in kept:
                row.active = False  # deals keep pointing at it; it is hidden, never erased
        await s.flush()
        await uow.audit(
            LISTS_KEY, "settings", name, new={"items": [i.label for i in items]}, reason=cleaned
        )
    async with get_sessionmaker()() as s2:
        return await _list_rows(s2, actor, model)


async def put_lost_reasons(
    actor: Actor, request: RequestInfo, items: list[Any], reason: str | None
) -> list[Any]:
    return await _put_list(actor, request, LostReason, "lost_reasons", items, reason)


async def put_fit_items(
    actor: Actor, request: RequestInfo, items: list[Any], reason: str | None
) -> list[Any]:
    return await _put_list(actor, request, FitItem, "fit_checklist", items, reason)
