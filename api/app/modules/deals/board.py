# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The Kanban board, stage pages and stage metrics (PRD 11 Features 4 and 5, PRD 9).

Everything here is read-only and goes through the caller's scope (Own / Team / All, Super
admin-owned deals hidden from Team Leaders and Users). The board never shows a weighted forecast.
Columns are display groups: Revision and Revision 2 share one column with an R1/R2 badge.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.accounts.models import Account, Contact
from app.modules.activities.models import DEAD_MEETING_STATUSES, Activity
from app.modules.catalog.models import DealProduct, Product
from app.modules.deals import health
from app.modules.documents.models import Document
from app.modules.leads.models import Deal, NextStep
from app.modules.leads.ownership import names, team_names
from app.modules.pipeline.config.models import FitItem, Stage, StageCriterion
from app.modules.pipeline.engine.models import DealStageEntry
from app.modules.pipeline.engine.service import published_version, stages_of
from app.modules.users.models import User
from app.platform.context import Actor
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read

PIPELINE = "new_business"  # default: callers that do not pass `pipeline` keep today's board
PAGE_SIZES = (10, 25, 50)
SORTS = ("age", "value", "company", "due")


async def _scope(
    session: AsyncSession, actor: Actor, pipeline: str, key: str = "board.view"
) -> list[Any]:
    decision = await authorize_read(session, actor, key)
    return [
        *scope_conditions(actor, Deal, decision.scope, team_attr="team_id"),
        Deal.deleted_at.is_(None),
        Deal.pipeline == pipeline,
    ]


def _filters(
    *,
    team_id: uuid.UUID | None,
    owner_id: uuid.UUID | None,
    source: str | None,
    health_: str | None,
    min_age_days: int | None,
    q: str | None,
) -> list[ColumnElement[bool]]:
    out: list[ColumnElement[bool]] = []
    if team_id:
        out.append(Deal.team_id == team_id)
    if owner_id:
        out.append(Deal.owner_id == owner_id)
    if source:
        out.append(Deal.lead_source == source)
    if health_:
        out.append(Deal.health == health_)
    if min_age_days:
        out.append(Deal.stage_entered_at <= utc_now() - timedelta(days=min_age_days))
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = f"%{escaped}%"
        out.append(
            Deal.account_id.in_(select(Account.id).where(Account.name.ilike(like, escape="\\")))
        )
    return out


async def columns_of(
    session: AsyncSession, actor: Actor, pipeline: str = PIPELINE
) -> list[dict[str, Any]]:
    version = await published_version(session, actor.org_id, pipeline)
    groups: dict[str, dict[str, Any]] = {}
    for st in (await stages_of(session, version.id)).values():
        if st.won_on_entry:
            continue  # Won deals leave the board
        g = groups.setdefault(
            st.display_group,
            {
                "key": st.display_group,
                "name": st.name if st.display_group == st.key else st.display_group.title(),
                "stage_keys": [],
                "probability_pct": st.probability_pct,
                "position": st.position,
            },
        )
        g["stage_keys"].append(st.key)
    return sorted(groups.values(), key=lambda g: g["position"])


async def _cards(
    session: AsyncSession, where: list[Any], keys: list[str], offset: int, limit: int
) -> list[dict[str, Any]]:
    today = health.ist_date(utc_now())
    owner = User
    stmt = (
        select(Deal, Account.name, owner.name, NextStep.text, NextStep.due_date)
        .join(Account, Account.id == Deal.account_id)
        .outerjoin(owner, owner.id == Deal.owner_id)
        .outerjoin(NextStep, and_(NextStep.deal_id == Deal.id, NextStep.done_at.is_(None)))
        .where(*where, Deal.status == "open", Deal.stage_key.in_(keys))
        .order_by(Deal.stage_entered_at.asc(), Deal.id)
        .offset(offset)
        .limit(limit)
    )
    out = []
    for d, company, owner_name, step_text, step_due in (await session.execute(stmt)).all():
        out.append(
            {
                "id": d.id,
                "company": company,
                "owner": {"id": d.owner_id, "name": owner_name} if d.owner_id else None,
                "stage_key": d.stage_key,
                "value_paise": d.value_paise,
                "health": d.health,
                "flags": d.flags or {},
                "revision_round": d.revision_round if d.stage_key.startswith("revision") else None,
                "next_step_text": step_text,
                "next_step_due": step_due,
                "overdue": bool(step_due and step_due < today),
                "age_days": (utc_now() - d.stage_entered_at).days,
                "lead_source": d.lead_source,
            }
        )
    return out


async def board(
    session: AsyncSession,
    actor: Actor,
    *,
    pipeline: str = PIPELINE,
    team_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    source: str | None = None,
    health_: str | None = None,
    min_age_days: int | None = None,
    q: str | None = None,
    per_column: int = 30,
) -> dict[str, Any]:
    where = [
        *await _scope(session, actor, pipeline),
        *_filters(team_id=team_id, owner_id=owner_id, source=source, health_=health_,
                  min_age_days=min_age_days, q=q),
    ]  # fmt: skip
    cols = await columns_of(session, actor, pipeline)
    agg = {
        k: (n, int(v or 0))
        for k, n, v in (
            await session.execute(
                select(Deal.stage_key, func.count(), func.sum(Deal.value_paise))
                .where(*where, Deal.status == "open")
                .group_by(Deal.stage_key)
            )
        ).all()
    }
    stale = (
        await session.execute(
            select(func.count())
            .select_from(Deal)
            .where(*where, Deal.status == "open", Deal.health == "stale")
        )
    ).scalar_one()
    out_cols = []
    total_n = total_v = 0
    for c in cols:
        n = sum(agg.get(k, (0, 0))[0] for k in c["stage_keys"])
        v = sum(agg.get(k, (0, 0))[1] for k in c["stage_keys"])
        total_n, total_v = total_n + n, total_v + v
        out_cols.append(
            {
                "key": c["key"],
                "name": c["name"],
                "stage_keys": c["stage_keys"],
                "probability_pct": c["probability_pct"],
                "count": n,
                "value_paise": v,
                "cards": await _cards(session, where, c["stage_keys"], 0, per_column),
            }
        )
    return {
        "columns": out_cols,
        "summary": {"open_deals": total_n, "pipeline_value_paise": total_v, "stale": stale},
    }


async def board_cards(
    session: AsyncSession,
    actor: Actor,
    column: str,
    *,
    pipeline: str = PIPELINE,
    offset: int = 0,
    limit: int = 30,
    team_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    source: str | None = None,
    health_: str | None = None,
    min_age_days: int | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    cols = {c["key"]: c for c in await columns_of(session, actor, pipeline)}
    if column not in cols:
        raise NotFound("Column not found")
    where = [
        *await _scope(session, actor, pipeline),
        *_filters(team_id=team_id, owner_id=owner_id, source=source, health_=health_,
                  min_age_days=min_age_days, q=q),
    ]  # fmt: skip
    return await _cards(session, where, cols[column]["stage_keys"], offset, min(limit, 100))


# ---------------------------------------------------------------------------- stage pages
EXTRA_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "lead": [
        ("generated_by", "Generated by"),
        ("assigned_by", "Assigned by"),
        ("contact_verified", "Contact verified?"),
        ("company_identified", "Company identified?"),
        ("fit_check", "Fit check"),
    ],
    "qualification": [
        ("pain_point", "Pain point"),
        ("meeting_date", "Meeting date"),
        ("meeting_status", "Meeting"),
    ],
    "meeting_scheduled": [
        ("meeting_date", "Meeting date"),
        ("meeting_status", "Meeting"),
        ("attendees", "Attendees"),
        ("completed", "Held?"),
        ("use_case", "Use case"),
    ],
    "prospecting": [
        ("budget", "Budget"),
        ("decision_maker", "Decision maker"),
        ("timeline", "Timeline"),
        ("pricing_model", "Pricing model"),
        ("plan", "Plan"),
        ("expected_minutes", "Expected minutes / month"),
    ],
    "proposal": [
        ("proposal_version", "Proposal version"),
        ("sent_date", "Sent"),
        ("walkthrough_date", "Walkthrough"),
        ("feedback", "Feedback"),
        ("decision_date", "Decision date"),
    ],
    "revision": [
        ("round", "Round"),
        ("round_reason", "Reason for this round"),
        ("requested_changes", "Requested changes"),
        ("final_price", "Final price"),
        ("agreement_status", "Agreement"),
    ],
    "closure": [
        ("agreement_status", "Agreement"),
        ("onboarding_info", "Onboarding info collected?"),
    ],
    "onboarding": [
        ("kickoff_date", "Kickoff"),
        ("go_live_date", "Go-live date"),
        ("live_date", "Live date"),
        ("go_live_delayed", "Go-live delayed"),
        ("first_invoice", "First invoice"),
    ],
    "won": [
        ("won_at", "Won"),
        ("product", "Product"),
        ("value", "Value"),
        ("days_taken", "Days taken"),
    ],
    "lost": [
        ("lost_reason", "Lost reason"),
        ("lost_notes", "Notes"),
        ("lost_from", "Lost at stage"),
        ("lost_at", "Lost on"),
    ],
}
EXTRA_COLUMNS["revision_2"] = EXTRA_COLUMNS["revision"]
ONBOARDING_FIELDS = (
    "billing_contact", "subscription_start_date", "pricing_model", "contract_term",
    "billing_frequency", "renewal_date",
)  # fmt: skip


async def _extras(
    session: AsyncSession,
    org_id: uuid.UUID,
    key: str,
    deals: list[Deal],
    stage_names: dict[str, str],
) -> dict[uuid.UUID, dict[str, Any]]:
    ids = [d.id for d in deals]
    out: dict[uuid.UUID, dict[str, Any]] = {d.id: {} for d in deals}
    if not ids:
        return out
    acts: dict[uuid.UUID, list[Activity]] = {}
    for a in (
        await session.execute(
            select(Activity)
            .where(Activity.deal_id.in_(ids), Activity.deleted_at.is_(None))
            .order_by(Activity.occurred_at.desc())
        )
    ).scalars():
        acts.setdefault(a.deal_id, []).append(a)
    docs: dict[uuid.UUID, list[Document]] = {}
    for dc in (
        await session.execute(
            select(Document)
            .where(Document.deal_id.in_(ids), Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
        )
    ).scalars():
        docs.setdefault(dc.deal_id, []).append(dc)
    accounts = {
        a.id: a
        for a in (
            await session.execute(
                select(Account).where(Account.id.in_({d.account_id for d in deals}))
            )
        ).scalars()
    }
    contacts: dict[uuid.UUID, list[Contact]] = {}
    for c in (
        await session.execute(
            select(Contact).where(
                Contact.account_id.in_(set(accounts)), Contact.deleted_at.is_(None)
            )
        )
    ).scalars():
        contacts.setdefault(c.account_id, []).append(c)
    people = await names(session, {d.generated_by for d in deals} | {d.assigned_by for d in deals})
    fit_total = (
        await session.execute(
            select(func.count())
            .select_from(FitItem)
            .where(FitItem.active.is_(True), FitItem.org_id == org_id)
        )
    ).scalar_one()
    lines: dict[uuid.UUID, list[str]] = {}
    if key == "won":
        for did, pname in (
            await session.execute(
                select(DealProduct.deal_id, Product.name)
                .join(Product, Product.id == DealProduct.product_id)
                .where(DealProduct.deal_id.in_(ids), DealProduct.deleted_at.is_(None))
            )
        ).all():
            lines.setdefault(did, []).append(pname)
    entry_reason: dict[uuid.UUID, str | None] = {}
    if key.startswith("revision"):
        for e in (
            await session.execute(
                select(DealStageEntry).where(
                    DealStageEntry.deal_id.in_(ids), DealStageEntry.exited_at.is_(None)
                )
            )
        ).scalars():
            entry_reason[e.deal_id] = e.reason

    def latest(did: uuid.UUID, *types: str) -> Activity | None:
        # a postponed / cancelled / no-show meeting is history, not "the meeting"
        return next(
            (
                a
                for a in acts.get(did, [])
                if a.type in types and a.meeting_status not in DEAD_MEETING_STATUSES
            ),
            None,
        )

    def doc(did: uuid.UUID, kind: str) -> Document | None:
        return next((d for d in docs.get(did, []) if d.kind == kind), None)

    for d in deals:
        acc, data, row = accounts[d.account_id], d.data or {}, out[d.id]
        meeting = latest(d.id, "meeting")
        if key == "lead":
            row.update(
                generated_by=people.get(d.generated_by, "System") if d.generated_by else "System",
                assigned_by=people.get(d.assigned_by)
                if d.assigned_by
                else ("System" if d.assignment_method else None),
                contact_verified=any(
                    a.two_way and a.type in ("call", "email", "meeting") for a in acts.get(d.id, [])
                ),
                company_identified=bool(acc.name and (acc.website or acc.industry)),
                fit_check=f"{sum(1 for v in (d.fit_checklist or {}).values() if v)}/{fit_total}",
            )
        elif key == "qualification":
            row.update(
                pain_point=d.pain_point,
                meeting_date=meeting.occurred_at if meeting else None,
                meeting_status=meeting.meeting_status if meeting else None,
            )
        elif key == "meeting_scheduled":
            row.update(
                meeting_date=meeting.occurred_at if meeting else None,
                attendees=", ".join(meeting.attendees) if meeting else None,
                meeting_status=meeting.meeting_status if meeting else None,
                completed=bool(meeting and meeting.meeting_status == "held"),
                use_case=d.use_case,
            )
        elif key == "prospecting":
            dm = next((c for c in contacts.get(d.account_id, []) if c.is_decision_maker), None)
            row.update(
                budget=data.get("budget_amount_paise"),
                decision_maker=dm.name if dm else None,
                timeline=d.expected_close_date,
                pricing_model=d.pricing_model,
                plan=d.plan,
                expected_minutes=d.expected_minutes_per_month,
            )
        elif key == "proposal":
            p = doc(d.id, "proposal")
            wt = latest(d.id, "walkthrough")
            row.update(
                proposal_version=p.version_no if p else None,
                sent_date=p.sent_at if p else None,
                walkthrough_date=wt.occurred_at if wt else None,
                feedback=data.get("feedback"),
                decision_date=data.get("decision_date"),
            )
        elif key in ("revision", "revision_2"):
            ag = doc(d.id, "agreement")
            asked = latest(d.id, "call", "email", "meeting")
            row.update(
                round=d.revision_round,
                round_reason=entry_reason.get(d.id),
                requested_changes=data.get("requested_changes")
                or (asked.discussed if asked else None),
                final_price=data.get("final_price_paise"),
                agreement_status=ag.status if ag else None,
            )
        elif key == "closure":
            ag = doc(d.id, "agreement")
            row.update(
                agreement_status=ag.status if ag else None,
                onboarding_info=all(
                    (data.get(f) if f in data else getattr(d, f, None)) not in (None, "")
                    for f in ONBOARDING_FIELDS
                ),
            )
        elif key == "onboarding":
            ko = latest(d.id, "kickoff")
            inv = doc(d.id, "invoice")
            row.update(
                kickoff_date=ko.occurred_at if ko else None,
                go_live_date=data.get("go_live_date"),
                live_date=data.get("live_date"),
                go_live_delayed=bool(data.get("go_live_delayed")),
                first_invoice=(inv.number or inv.status) if inv else None,
            )
        elif key == "won":
            row.update(
                won_at=d.won_at,
                product=", ".join(sorted(set(lines.get(d.id, [])))) or None,
                value=d.value_paise,
                days_taken=(d.won_at - d.created_at).days if d.won_at else None,
            )
        elif key == "lost":
            row.update(
                lost_reason=d.lost_reason,
                lost_notes=d.lost_notes,
                lost_from=stage_names.get(d.lost_from_stage_key or "", d.lost_from_stage_key),
                lost_at=d.lost_at,
            )
    return out


async def stage_page(
    session: AsyncSession,
    actor: Actor,
    key: str,
    *,
    pipeline: str = PIPELINE,
    page: int = 1,
    page_size: int = 25,
    sort: str = "age",
    desc: bool = False,
    team_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    source: str | None = None,
    health_: str | None = None,
    min_age_days: int | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    if page_size not in PAGE_SIZES:
        raise ValidationFailed("Page size must be 10, 25 or 50", {"field": "page_size"})
    if sort not in SORTS:
        raise ValidationFailed("Unknown sort", {"field": "sort"})
    version = await published_version(session, actor.org_id, pipeline)
    stages = await stages_of(session, version.id)
    where = [
        *await _scope(session, actor, pipeline),
        *_filters(team_id=team_id, owner_id=owner_id, source=source, health_=health_,
                  min_age_days=min_age_days, q=q),
    ]  # fmt: skip
    if key in ("won", "lost"):
        where.append(Deal.status == key)
        name = key.title()
        keys: list[str] = []
        probability = 100 if key == "won" else 0
    else:
        st = stages.get(key)
        if st is None:
            raise NotFound("Stage not found")
        keys = (
            [s.key for s in stages.values() if s.display_group == st.display_group]
            if key == st.display_group
            else [key]
        )  # noqa: E501
        # a deal entering a "won on entry" stage (Onboarding) is Won but still worked on there
        where += [Deal.status == ("won" if st.won_on_entry else "open"), Deal.stage_key.in_(keys)]
        name, probability = st.name, st.probability_pct
    order_col = {
        "age": Deal.stage_entered_at,
        "value": Deal.value_paise,
        "company": Account.name,
        "due": NextStep.due_date,
    }[sort]
    base = (
        select(Deal, Account.name, NextStep.text, NextStep.due_date)
        .join(Account, Account.id == Deal.account_id)
        .outerjoin(NextStep, and_(NextStep.deal_id == Deal.id, NextStep.done_at.is_(None)))
        .where(*where)
    )
    total, value = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Deal.value_paise), 0))
            .select_from(Deal)
            .where(*where)
        )
    ).one()
    rows = (
        await session.execute(
            base.order_by(order_col.desc() if desc else order_col.asc(), Deal.id)
            .offset((max(page, 1) - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    deals = [r[0] for r in rows]
    stage_names = {s.key: s.name for s in stages.values()}
    extras = await _extras(session, actor.org_id, key, deals, stage_names)
    criteria = (
        await session.execute(
            select(StageCriterion.key, StageCriterion.label)
            .where(StageCriterion.version_id == version.id, StageCriterion.stage_key.in_(keys[:1]))
            .order_by(StageCriterion.position)
        )
    ).all()
    who = await names(session, {d.owner_id for d in deals})
    teams = await team_names(session, {d.team_id for d in deals})
    today = health.ist_date(utc_now())
    return {
        "key": key,
        "name": name,
        "probability_pct": probability,
        "total": total,
        "value_paise": int(value),
        "page": page,
        "page_size": page_size,
        "columns": [{"key": k, "label": lbl} for k, lbl in EXTRA_COLUMNS.get(key, [])],
        "criteria": [{"key": k, "label": lbl} for k, lbl in criteria],
        "rows": [
            {
                "id": d.id,
                "company": company,
                "owner": {"id": d.owner_id, "name": who[d.owner_id]} if d.owner_id else None,
                "team_name": teams.get(d.team_id) if d.team_id else None,
                "lead_source": d.lead_source,
                "value_paise": d.value_paise,
                "health": d.health,
                "flags": d.flags or {},
                "stage_key": d.stage_key,
                "revision_round": d.revision_round if d.stage_key.startswith("revision") else None,
                "age_days": (utc_now() - d.stage_entered_at).days,
                "stage_entered_at": d.stage_entered_at,
                "next_step_text": step_text,
                "next_step_due": step_due,
                "overdue": bool(step_due and step_due < today),
                "extra": extras.get(d.id, {}),
            }
            for d, company, step_text, step_due in rows
        ],
    }


# ---------------------------------------------------------------------------- metrics
async def stage_metrics(
    session: AsyncSession, actor: Actor, key: str, pipeline: str = PIPELINE
) -> dict[str, Any]:
    """Win rate, average time in stage and conversion to the next stage, from real history.
    The configured probability is returned beside them so it can be tuned with evidence."""
    where = await _scope(session, actor, pipeline)
    version = await published_version(session, actor.org_id, pipeline)
    stages = await stages_of(session, version.id)
    st: Stage | None = stages.get(key)
    if st is None:
        raise NotFound("Stage not found")
    group = [s for s in stages.values() if s.display_group == st.display_group]
    keys = [s.key for s in group]
    top = max(s.position for s in group)
    later = [s.key for s in stages.values() if s.position > top]
    scoped = select(Deal.id).where(*where).scalar_subquery()
    open_now = (
        await session.execute(
            select(func.count())
            .select_from(Deal)
            .where(*where, Deal.status == "open", Deal.stage_key.in_(keys))
        )
    ).scalar_one()
    entered = (
        await session.execute(
            select(func.count(func.distinct(DealStageEntry.deal_id))).where(
                DealStageEntry.stage_key.in_(keys), DealStageEntry.deal_id.in_(scoped)
            )
        )
    ).scalar_one()
    outcome = dict(
        (
            await session.execute(
                select(Deal.status, func.count(func.distinct(Deal.id)))
                .join(DealStageEntry, DealStageEntry.deal_id == Deal.id)
                .where(
                    DealStageEntry.stage_key.in_(keys),
                    Deal.id.in_(scoped),
                    Deal.status.in_(("won", "lost")),
                )
                .group_by(Deal.status)
            )
        ).all()
    )
    won, lost = outcome.get("won", 0), outcome.get("lost", 0)
    avg_secs = (
        await session.execute(
            select(
                func.avg(
                    func.extract("epoch", DealStageEntry.exited_at - DealStageEntry.entered_at)
                )
            ).where(
                DealStageEntry.stage_key.in_(keys),
                DealStageEntry.exited_at.is_not(None),
                DealStageEntry.deal_id.in_(scoped),
            )
        )
    ).scalar_one()
    moved = 0
    if later and entered:
        moved = (
            await session.execute(
                select(func.count(func.distinct(DealStageEntry.deal_id)))
                .where(DealStageEntry.stage_key.in_(later), DealStageEntry.deal_id.in_(scoped))
                .where(
                    DealStageEntry.deal_id.in_(
                        select(DealStageEntry.deal_id).where(DealStageEntry.stage_key.in_(keys))
                    )
                )
            )
        ).scalar_one()
    return {
        "key": key,
        "name": st.name,
        "configured_probability_pct": st.probability_pct,
        "open_deals": open_now,
        "deals_entered": entered,
        "won": won,
        "lost": lost,
        "win_rate_pct": round(100 * won / (won + lost), 1) if won + lost else None,
        "avg_days_in_stage": round(float(avg_secs) / 86400, 1) if avg_secs is not None else None,
        "conversion_to_next_pct": round(100 * moved / entered, 1) if entered and later else None,
    }


async def all_stage_metrics(
    session: AsyncSession, actor: Actor, pipeline: str = PIPELINE
) -> list[dict[str, Any]]:
    version = await published_version(session, actor.org_id, pipeline)
    out = []
    seen: set[str] = set()
    for st in (await stages_of(session, version.id)).values():
        if st.display_group in seen:
            continue
        seen.add(st.display_group)
        out.append(await stage_metrics(session, actor, st.key, pipeline))
    return out
