# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Leads: the new-lead form, edits and Undo, the Lead Pool, manual assignment, reassignment,
bulk reassignment and direct team moves (PRD 6, 11 Feature 1, 4.4 B and F).

Every write runs in a UnitOfWork: permission, scope, reason, then the change, its audit row and
its outbox event in one transaction.
"""

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, OutOfScope, ValidationFailed
from app.core.normalize import clean_text, normalize_domain, normalize_email, normalize_phone
from app.core.time import to_ist, utc_now
from app.modules.accounts import service as accounts
from app.modules.accounts.models import Account, Contact
from app.modules.customers.models import Customer
from app.modules.deals.common import load_deal
from app.modules.leads import events
from app.modules.leads.assignment.models import AssignmentRule
from app.modules.leads.assignment.service import (
    REP_ENTRY,
    assign_pooled,
    candidates,
    caps_for,
    hold_minutes,
    team_for_rep_lead,
)
from app.modules.leads.models import (
    EXISTING_CUSTOMER_SOURCE,
    Deal,
    DuplicateOverride,
    LeadPoolEntry,
    NextStep,
)
from app.modules.leads.ownership import (
    active_lead_counts,
    apply_owner,
    deal_rt,
    names,
    open_pool_entry,
    pool_rt,
    release_rep_hold,
    return_to_pool,
    team_names,
)
from app.modules.pipeline.config.models import Stage
from app.modules.pipeline.engine.nextstep import suggest as suggest_next_step
from app.modules.pipeline.engine.nextstep import tomorrow_ist
from app.modules.pipeline.engine.service import start_deal
from app.modules.users.models import User
from app.platform.audit.models import AuditLog
from app.platform.context import Actor, RequestInfo
from app.platform.permissions import loader
from app.platform.permissions.roles import ActionClass, Role, Scope
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read
from app.platform.reasons import policy
from app.platform.uow import UnitOfWork

log = logging.getLogger("relay.leads")


def today_ist() -> date:
    return to_ist(utc_now()).date()


# ============================================================================= reads
async def build_deals(session: AsyncSession, deals: list[Deal]) -> list[dict[str, Any]]:
    if not deals:
        return []
    ids = [d.id for d in deals]
    accs = {
        a.id: a
        for a in (
            await session.execute(
                select(Account).where(Account.id.in_({d.account_id for d in deals}))
            )
        ).scalars()
    }
    contact_ids = {d.primary_contact_id for d in deals if d.primary_contact_id}
    contacts = (
        {
            c.id: c
            for c in (
                await session.execute(select(Contact).where(Contact.id.in_(contact_ids)))
            ).scalars()
        }
        if contact_ids
        else {}
    )
    steps: dict[uuid.UUID, NextStep] = {}
    for step_row in (
        await session.execute(
            select(NextStep)
            .where(NextStep.deal_id.in_(ids), NextStep.done_at.is_(None))
            .order_by(NextStep.created_at)
        )
    ).scalars():
        steps[step_row.deal_id] = step_row
    entries = {
        e.deal_id: e
        for e in (
            await session.execute(
                select(LeadPoolEntry).where(
                    LeadPoolEntry.deal_id.in_(ids), LeadPoolEntry.left_at.is_(None)
                )
            )
        ).scalars()
    }
    rule_ids = {d.assignment_rule_id for d in deals if d.assignment_rule_id}
    rules = (
        {
            r.id: r.name
            for r in (
                await session.execute(select(AssignmentRule).where(AssignmentRule.id.in_(rule_ids)))
            ).scalars()
        }
        if rule_ids
        else {}
    )
    people = await names(
        session,
        {d.owner_id for d in deals}
        | {d.generated_by for d in deals}
        | {d.assigned_by for d in deals}
        | {s.responsible_id for s in steps.values()},
    )
    teams = await team_names(session, {d.team_id for d in deals})
    version_ids = {d.pipeline_version_id for d in deals if d.pipeline_version_id}
    stage_names = (
        {
            (v, k): n
            for v, k, n in (
                await session.execute(
                    select(Stage.version_id, Stage.key, Stage.name).where(
                        Stage.version_id.in_(version_ids)
                    )
                )
            ).all()
        }
        if version_ids
        else {}
    )
    today = today_ist()
    out = []
    for d in deals:
        acc, c, st, ent = (
            accs[d.account_id],
            contacts.get(d.primary_contact_id or uuid.UUID(int=0)),
            steps.get(d.id),
            entries.get(d.id),
        )
        if d.assigned_by:
            assigned_by = people.get(d.assigned_by)
        else:
            assigned_by = "System" if d.assignment_method in ("rule", "round_robin") else None
        out.append(
            {
                "id": d.id,
                "account_id": d.account_id,
                "company": acc.name,
                "website": acc.website,
                "industry": acc.industry,
                "city": acc.city,
                "contact": {
                    "id": c.id if c else None,
                    "name": c.name if c else None,
                    "role": c.role if c else None,
                    "phone": c.phone_e164 if c else None,
                    "email": c.email if c else None,
                    "is_decision_maker": bool(c.is_decision_maker) if c else False,
                },
                "lead_source": d.lead_source,
                "status": d.status,
                "stage_key": d.stage_key,
                "pipeline": d.pipeline,
                "request_type": d.request_type,
                "owner": {"id": d.owner_id, "name": people[d.owner_id]} if d.owner_id else None,
                "team_id": d.team_id,
                "team_name": teams.get(d.team_id) if d.team_id else None,
                "generated_by": people.get(d.generated_by, "System")
                if d.generated_by
                else "System",
                "first_contact_date": d.first_contact_date,
                "assigned_at": d.assigned_at,
                "assignment_method": d.assignment_method,
                "assigned_by": assigned_by,
                "assignment_rule": rules.get(d.assignment_rule_id)
                if d.assignment_rule_id
                else None,
                "next_step": (
                    {
                        "id": st.id,
                        "text": st.text,
                        "due_date": st.due_date,
                        "responsible": {"id": st.responsible_id, "name": people[st.responsible_id]}
                        if st.responsible_id and st.responsible_id in people
                        else None,
                        "lane": st.lane,
                        "lane_since": st.lane_since,
                        "snooze_count": st.snooze_count,
                        "overdue": st.due_date < today,
                    }
                    if st
                    else None
                ),
                "waiting_reason": ent.waiting_reason if ent else None,
                "version": d.version,
                "created_at": d.created_at,
                "stage_name": stage_names.get(
                    (d.pipeline_version_id or uuid.UUID(int=0), d.stage_key)
                ),
                "value_paise": d.value_paise,
                "health": d.health,
                "flags": d.flags or {},
                "revision_round": d.revision_round,
                "expected_close_date": d.expected_close_date,
                "won_at": d.won_at,
                "lost_at": d.lost_at,
                "stage_entered_at": d.stage_entered_at,
            }
        )
    return out


async def _deal_scope(session: AsyncSession, actor: Actor, key: str = "deal.view") -> Scope:
    return (await loader.get_table(session))[(actor.role_enum, key)].scope


async def get_deal(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> dict[str, Any]:
    await authorize_read(session, actor, "deal.view", ("deal", deal_id))
    deal = await session.get(Deal, deal_id)
    if deal is None or deal.org_id != actor.org_id or deal.deleted_at is not None:
        raise NotFound("Not found")
    return (await build_deals(session, [deal]))[0]


async def created_deal(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> dict[str, Any]:
    """The lead the actor just created, whoever the engine gave it to. The creator may not own it
    (round-robin), so this is the one read that skips scope; it is only used on the response to
    that very create call."""
    deal = await session.get(Deal, deal_id)
    if deal is None or deal.org_id != actor.org_id or deal.generated_by != actor.id:
        raise NotFound("Not found")
    return (await build_deals(session, [deal]))[0]


async def list_deals(
    session: AsyncSession,
    actor: Actor,
    status: str | None = None,
    limit: int = 100,
    owner_id: uuid.UUID | None = None,
    stage_key: str | None = None,
    team_id: uuid.UUID | None = None,
    lead_source: str | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    decision = await authorize_read(session, actor, "deal.view")
    stmt = select(Deal).where(
        *scope_conditions(actor, Deal, decision.scope, team_attr="team_id"),
        Deal.deleted_at.is_(None),
    )
    if status:
        stmt = stmt.where(Deal.status == status)
    if owner_id:
        stmt = stmt.where(Deal.owner_id == owner_id)
    if stage_key:
        stmt = stmt.where(Deal.stage_key == stage_key)
    if team_id:
        stmt = stmt.where(Deal.team_id == team_id)
    if lead_source:
        stmt = stmt.where(Deal.lead_source == lead_source)
    deals = list(
        (
            await session.execute(
                stmt.order_by(Deal.created_at.desc(), Deal.id.desc()).offset(offset).limit(limit)
            )
        ).scalars()
    )
    return await build_deals(session, deals)


# ============================================================================= create
async def create_lead(
    actor: Actor, request: RequestInfo, body: Any
) -> tuple[uuid.UUID, str | None, bool]:
    """Create the lead. Returns (deal id, cap warning, skip_engine). With `body.owner_id` the
    lead is given to that person in this same transaction (one lead.create audit row plus one
    pool.assign row) and the engine is skipped. A lead created by a sales rep is NOT assigned: it
    is held in the Pool for the Team Leader of the routed team (engine skipped; the hold ends by
    the Team Leader's decision or, after the hold window, by automatic round robin). Anything
    else enters the Pool for the engine."""
    phone, email = normalize_phone(body.phone), normalize_email(body.email)
    if body.phone and not phone:
        raise ValidationFailed("That phone number is not valid", {"field": "phone"})
    if body.email and not email:
        raise ValidationFailed("That email address is not valid", {"field": "email"})
    if not (phone or email):
        raise ValidationFailed("Enter a phone number or an email", {"field": "phone"})
    if body.website and not normalize_domain(body.website):
        raise ValidationFailed("That website is not a valid address", {"field": "website"})
    if body.first_contact_date and body.first_contact_date > today_ist():
        raise ValidationFailed(
            "The first contact date cannot be in the future", {"field": "first_contact_date"}
        )
    if body.next_step_due_date is not None and body.next_step_due_date < today_ist():
        raise ValidationFailed(
            "The due date cannot be in the past", {"field": "next_step_due_date"}
        )

    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize("lead.create")
        routine_reason = uow.reason("lead.create", body.reason)  # Admin: required, SA: optional
        target: User | None = None
        assign_reason: str | None = None
        warn: str | None = None
        if body.owner_id is not None:  # choose-the-owner: the manual Pool-assign rules, up front
            await uow.advisory_lock("assign")
            await uow.authorize("pool.assign")  # reps: 403, logged as a denied pool.assign
            assign_reason = uow.reason("pool.assign", body.reason)
            target = await _target(uow, body.owner_id)  # inactive / other org 422, other team 403
        # one check-and-insert at a time per organisation, so a double click or two reps adding
        # the same company cannot both pass the duplicate check (BUG-P2-08, BUG-P2-09)
        await uow.advisory_lock("lead_create")
        decision = await accounts.resolve_duplicates(
            uow,
            company=body.company,
            domain=normalize_domain(body.website),
            phone=phone,
            email=email,
            create_anyway=body.create_anyway,
            override_block=body.override_block,
            reason=body.reason,
        )
        if decision.attach_to is not None:
            account = decision.attach_to
        else:
            account = await accounts.create_account_row(
                uow, name=body.company, website=body.website, industry=body.industry, city=body.city
            )
        contact = await accounts.create_contact_row(
            uow,
            account_id=account.id,
            name=body.contact_name,
            role=body.contact_role,
            phone=body.phone,
            email=body.email,
            is_decision_maker=body.contact_is_decision_maker,
        )
        rep_hold = target is None and actor.role == Role.USER
        hold_team: uuid.UUID | None = None
        if rep_hold:  # the rules still pick the team; its Team Leader decides who gets the lead
            hold_team = await team_for_rep_lead(
                s,
                actor.org_id,
                lead_source=body.lead_source,
                account=account,
                creator_team=actor.team_id,
            )
        deal = Deal(
            org_id=actor.org_id,
            account_id=account.id,
            primary_contact_id=contact.id,
            status="pooled",
            team_id=hold_team,
            lead_source=body.lead_source,
            generated_by=actor.id,
            first_contact_date=body.first_contact_date,
            created_by=actor.id,
        )
        s.add(deal)
        await s.flush()
        await start_deal(uow, deal)  # enters the Lead stage of the published pipeline
        # the first next step: what the person typed, else what the pipeline asks for (the first
        # unmet criterion of the entry stage), due tomorrow (IST) unless a date was given
        step_text = clean_text(body.next_step_text) if body.next_step_text else None
        if step_text is None:
            step_text = await suggest_next_step(s, deal) or "Follow up with the new lead"
        step_due = body.next_step_due_date or tomorrow_ist()
        s.add(
            NextStep(
                org_id=actor.org_id,
                deal_id=deal.id,
                text=step_text,
                due_date=step_due,
                created_by=actor.id,
                auto_generated=not body.next_step_text,
            )
        )
        s.add(
            LeadPoolEntry(
                org_id=actor.org_id,
                deal_id=deal.id,
                entered_by=actor.id,
                entered_source=REP_ENTRY if rep_hold else "manual entry",
                routed_team_id=hold_team,
                manual_hold=rep_hold,  # keeps the engine away
                rep_hold=rep_hold,
                tl_notified=rep_hold,  # the Team Leader is told once, by the event below
            )
        )
        await s.flush()
        await uow.audit(
            "lead.create",
            "deal",
            deal.id,
            new={
                "company": account.name,
                "lead_source": deal.lead_source,
                "contact": contact.name,
                "next_step": step_text,
                "due_date": step_due.isoformat(),
                "generated_by": actor.name,
                **({"held_for": "team_leader"} if rep_hold else {}),
            },
            reason=routine_reason,
        )
        for m in decision.overrides:
            s.add(
                DuplicateOverride(
                    org_id=actor.org_id,
                    deal_id=deal.id,
                    match_type=m.match_type,
                    matched_account_id=m.account_id,
                    in_scope=m.in_scope,
                    reason=decision.reason or "",
                    by_user_id=actor.id,
                )
            )
        if decision.kind:
            key = (
                "duplicate.override_or_merge"
                if decision.kind == "override_block"
                else "duplicate.create_anyway"
            )
            await uow.audit(
                key,
                "deal",
                deal.id,
                reason=decision.reason,
                new={
                    "matches": [
                        {"company": m.company, "type": m.match_type, "in_scope": m.in_scope}
                        for m in decision.overrides
                    ]
                },
            )
        if decision.kind == "create_anyway" and decision.outside_scope:
            await uow.emit(
                events.DUPLICATE_OUTSIDE_SCOPE,
                {"deal_id": str(deal.id), "company": account.name, "by": actor.name},
            )
        if target is not None:
            warn = await _over_cap_note(uow, target)
            await apply_owner(
                uow,
                deal,
                target,
                method="manual",
                action_key="pool.assign",
                by=actor.id,
                reason=assign_reason,
                note=warn or f"Manual by {actor.name}",
            )
            return deal.id, warn, True
        if rep_hold:
            await uow.emit(events.POOL_CHANGED, {"deal_id": str(deal.id)}, rt=pool_rt(hold_team))
            await uow.emit(
                events.REP_LEAD_WAITING,
                {
                    "deal_id": str(deal.id),
                    "team_id": str(hold_team) if hold_team else None,
                    "company": account.name,
                    "creator_id": str(actor.id),
                    "creator_name": actor.name,
                },
            )
            return deal.id, None, True
        await uow.emit(events.DEAL_POOLED, {"deal_id": str(deal.id)}, rt=pool_rt(None))
        return deal.id, None, False


# ============================================================================= requests (P5-01)
async def create_request(
    actor: Actor, request: RequestInfo, account_id: uuid.UUID, body: Any
) -> uuid.UUID:
    """A request raised on an existing customer's account (upsell/top_up, renewal, plan change,
    other product, increase, decrease): PRD Phase 5. Enters the `existing_customer` pipeline at
    its entry stage, owned by the account owner. Unlike `create_lead`, this skips the Lead Pool
    and the assignment engine entirely -- there is nothing to route, the account already has an
    owner."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        # Phase 5 P5-06: a churned customer coming back raises a "return" (or "other_product")
        # request on the same, still-churned account -- so both statuses are accepted here; only
        # a deleted customer record is rejected. customer.status flips back to "active" later, on
        # the deal's confirmed SubscriptionChange (billing/changes.py), not on request creation.
        customer = (
            await s.execute(
                select(Customer).where(
                    Customer.account_id == account_id,
                    Customer.org_id == actor.org_id,
                    Customer.status.in_(("active", "churned")),
                    Customer.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if customer is None:
            raise NotFound("This account has no active customer record")
        account = await s.get(Account, account_id)
        if account is None or account.org_id != actor.org_id or account.deleted_at is not None:
            raise NotFound("Account not found")
        decision = await uow.authorize("lead.create")  # same roles as a new lead (PRD data model)
        owner = await s.get(User, customer.account_owner_id)
        if owner is None or owner.deleted_at is not None:
            raise Conflict("The account owner is no longer active")
        if decision.scope == Scope.OWN and owner.id != actor.id:
            raise OutOfScope("Only the account owner can create a request for this account")
        if decision.scope == Scope.TEAM and owner.team_id != actor.team_id:
            raise OutOfScope("This account belongs to another team")
        cleaned = uow.reason("lead.create", body.reason)
        # the request is with the customer's own contact: the decision maker, else the oldest one
        contact = (
            await s.execute(
                select(Contact)
                .where(Contact.account_id == account.id, Contact.deleted_at.is_(None))
                .order_by(Contact.is_decision_maker.desc(), Contact.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        deal = Deal(
            org_id=actor.org_id,
            account_id=account.id,
            primary_contact_id=contact.id if contact else None,
            pipeline="existing_customer",
            status="open",
            owner_id=owner.id,
            team_id=owner.team_id,
            lead_source=EXISTING_CUSTOMER_SOURCE,
            generated_by=actor.id,
            request_type=body.request_type,
            data={"requirement": body.requirement},
            created_by=actor.id,
        )
        s.add(deal)
        await s.flush()
        await start_deal(uow, deal)  # enters the Request stage of the published pipeline
        # a deal never has no next step: same rule as a new lead (the first unmet criterion of
        # the entry stage, due tomorrow IST), owned by the account owner who works the request
        s.add(
            NextStep(
                org_id=actor.org_id,
                deal_id=deal.id,
                text=await suggest_next_step(s, deal) or "Contact the customer about the request",
                due_date=tomorrow_ist(),
                created_by=actor.id,
                auto_generated=True,
            )
        )
        if customer.status == "churned" and body.request_type in ("other_product", "return"):
            # auto-detect the returning-customer flow (P5-06): no separate endpoint/screen --
            # link the latest unreversed churn event so Churn History (P5-07) can show "Returned".
            from app.modules.churn.service import link_return_deal

            await link_return_deal(s, customer, deal.id)
        await uow.audit(
            "lead.create",
            "deal",
            deal.id,
            new={
                "pipeline": "existing_customer",
                "request_type": body.request_type,
                "requirement": body.requirement,
                "account": account.name,
            },
            reason=cleaned,
        )
        await uow.emit(events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
        return deal.id


# ============================================================================= the Pool
async def list_pool(
    session: AsyncSession,
    actor: Actor,
    *,
    source: str | None = None,
    team_id: uuid.UUID | None = None,
    waiting_reason: str | None = None,
    min_minutes: int | None = None,
    limit: int = 1000,
    offset: int = 0,
    total: list[int] | None = None,
) -> list[dict[str, Any]]:
    decision = await authorize_read(session, actor, "pool.view")
    stmt = (
        select(LeadPoolEntry, Deal)
        .join(Deal, Deal.id == LeadPoolEntry.deal_id)
        .where(
            LeadPoolEntry.org_id == actor.org_id,
            LeadPoolEntry.left_at.is_(None),
            Deal.status == "pooled",
            Deal.deleted_at.is_(None),
            *scope_conditions(actor, Deal, decision.scope, team_attr="team_id"),
        )
    )
    if source:
        stmt = stmt.where(Deal.lead_source == source)
    if team_id:
        stmt = stmt.where(Deal.team_id == team_id)
    if waiting_reason:
        stmt = stmt.where(LeadPoolEntry.waiting_reason == waiting_reason)
    if min_minutes and min_minutes > 0:
        minutes = min(min_minutes, 60 * 24 * 365 * 100)  # a huge value must not overflow dates
        stmt = stmt.where(LeadPoolEntry.entered_at <= utc_now() - timedelta(minutes=minutes))
    if total is not None:  # the full count, so the page can say "showing 1000 of 1500"
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total.append((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(
            stmt.order_by(LeadPoolEntry.entered_at, LeadPoolEntry.id)
            .limit(max(1, min(limit, 5000)))
            .offset(max(0, offset))
        )
    ).all()
    deals = [d for _, d in rows]
    accs = (
        {
            a.id: a.name
            for a in (
                await session.execute(
                    select(Account).where(Account.id.in_({d.account_id for d in deals}))
                )
            ).scalars()
        }
        if deals
        else {}
    )
    cids = {d.primary_contact_id for d in deals if d.primary_contact_id}
    contacts = (
        {
            c.id: c.name
            for c in (await session.execute(select(Contact).where(Contact.id.in_(cids)))).scalars()
        }
        if cids
        else {}
    )
    people = await names(session, {d.generated_by for d in deals})
    teams = await team_names(session, {e.routed_team_id for e, _ in rows})
    window = timedelta(minutes=await hold_minutes(session, actor.org_id))
    creator_ids = {d.generated_by for e, d in rows if e.rep_hold and d.generated_by}
    creators = (
        {
            u.id: u
            for u in (
                await session.execute(
                    select(User).where(User.id.in_(creator_ids), User.org_id == actor.org_id)
                )
            ).scalars()
        }
        if creator_ids
        else {}
    )

    def assignable(e: LeadPoolEntry, d: Deal) -> bool:
        # Same rule as decide(give_creator): giving a lead back to its own (active) creator is
        # always valid for whoever may decide it, even when a routing rule sent it to another
        # team's Team Leader than the creator's own (a TL only lists their team's share).
        c = creators.get(d.generated_by) if d.generated_by else None
        return bool(e.rep_hold and c is not None and c.is_active and c.deleted_at is None)

    return [
        {
            "deal_id": d.id,
            "company": accs.get(d.account_id, ""),
            "contact_name": contacts.get(d.primary_contact_id) if d.primary_contact_id else None,
            "lead_source": d.lead_source,
            "generated_by": people.get(d.generated_by, "System") if d.generated_by else "System",
            "entered_at": e.entered_at,
            "routed_team_id": e.routed_team_id,
            "routed_team_name": teams.get(e.routed_team_id) if e.routed_team_id else None,
            "waiting_reason": e.waiting_reason,
            "held_for_manual": e.manual_hold,
            "rep_hold": e.rep_hold,
            "creator_id": d.generated_by if e.rep_hold else None,
            "creator_assignable": assignable(e, d),
            "auto_assign_at": e.entered_at + window if e.rep_hold else None,
        }
        for e, d in rows
    ]


async def distribution(session: AsyncSession, actor: Actor, days: int = 30) -> dict[str, Any]:
    decision = await authorize_read(session, actor, "pool.view")
    since = utc_now() - timedelta(days=days)
    org = actor.org_id
    deal_conds = [
        *scope_conditions(actor, Deal, decision.scope, team_attr="team_id"),
        Deal.deleted_at.is_(None),
    ]
    pool_size = (
        await session.execute(
            select(func.count()).select_from(Deal).where(*deal_conds, Deal.status == "pooled")
        )
    ).scalar_one()
    reps_stmt = select(User).where(
        User.org_id == org,
        User.role == Role.USER,
        User.is_active.is_(True),
        User.deleted_at.is_(None),
    )
    if decision.scope == Scope.TEAM:
        reps_stmt = reps_stmt.where(User.team_id == actor.team_id)
    reps = list((await session.execute(reps_stmt.order_by(User.name))).scalars())
    ids = [r.id for r in reps]
    loads = await active_lead_counts(session, org, ids)
    caps = await caps_for(session, org, ids)
    assigned = (
        {
            uid: n
            for uid, n in (
                await session.execute(
                    select(Deal.owner_id, func.count())
                    .where(
                        Deal.owner_id.in_(ids), Deal.assigned_at >= since, Deal.deleted_at.is_(None)
                    )
                    .group_by(Deal.owner_id)
                )
            ).all()
        }
        if ids
        else {}
    )
    teams = await team_names(session, {r.team_id for r in reps})
    methods = {
        m or "unknown": n
        for m, n in (
            await session.execute(
                select(Deal.assignment_method, func.count())
                .where(*deal_conds, Deal.assigned_at >= since, Deal.assignment_method.is_not(None))
                .group_by(Deal.assignment_method)
            )
        ).all()
    }
    visible_deals = select(Deal.id).where(*deal_conds)
    hist = (
        (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.org_id == org,
                    AuditLog.entity_type == "deal",
                    AuditLog.action_key.in_(
                        ["lead.reassign", "pool.assign", "deal.owner.change", "deal.bulk_reassign"]
                    ),
                    AuditLog.outcome == "ok",
                    AuditLog.entity_id.in_(
                        select(func.cast(Deal.id, __import__("sqlalchemy").Text)).where(*deal_conds)
                    ),
                )
                .order_by(AuditLog.seq.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    _ = visible_deals
    actors = await names(session, {r.actor_id for r in hist})
    return {
        "pool_size": pool_size,
        "reps": [
            {
                "user_id": r.id,
                "name": r.name,
                "team_id": r.team_id,
                "team_name": teams.get(r.team_id) if r.team_id else None,
                "active_leads": loads[r.id],
                "cap": caps[r.id],
                "assigned_in_period": assigned.get(r.id, 0),
            }
            for r in reps
        ],
        "methods": methods,
        "reassignments": [
            {
                "at": r.occurred_at,
                "by": actors.get(r.actor_id, "System") if r.actor_id else "System",
                "deal_id": r.entity_id,
                "from": (r.old_value or {}).get("owner"),
                "to": (r.new_value or {}).get("owner"),
                "reason": r.reason,
            }
            for r in hist
        ],
    }


# ============================================================================= manual ownership
async def _target(uow: UnitOfWork, user_id: uuid.UUID) -> User:
    t = (
        await uow.session.execute(
            select(User).where(
                User.id == user_id, User.org_id == uow.actor.org_id, User.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if t is None or not t.is_active:
        raise ValidationFailed("Choose an active person", {"field": "user_id"})
    actor = uow.actor
    if actor.role == Role.TEAM_LEADER and t.team_id != actor.team_id:
        raise OutOfScope("Team Leaders can only assign inside their own team")
    return t


async def _over_cap_note(uow: UnitOfWork, target: User) -> str | None:
    load = (await active_lead_counts(uow.session, uow.actor.org_id, [target.id]))[target.id]
    cap = (await caps_for(uow.session, uow.actor.org_id, [target.id]))[target.id]
    return f"{target.name} is at their cap ({load}/{cap}); assigned anyway" if load >= cap else None


async def assign_from_pool(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, user_id: uuid.UUID, reason: str | None
) -> tuple[uuid.UUID, str | None]:
    async with UnitOfWork(actor, request) as uow:
        await uow.advisory_lock("assign")
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("pool.assign", entity=("deal", deal.id))
        cleaned = uow.reason("pool.assign", reason)
        if deal.status != "pooled":
            raise Conflict("This lead is no longer in the Pool")
        target = await _target(uow, user_id)
        warn = await _over_cap_note(uow, target)
        await apply_owner(
            uow,
            deal,
            target,
            method="manual",
            action_key="pool.assign",
            by=actor.id,
            reason=cleaned,
            note=warn or f"Manual by {actor.name}",
        )
        return deal.id, warn


async def decide_rep_lead(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    action: str,
    user_id: uuid.UUID | None,
    reason: str | None,
) -> dict[str, Any]:
    """The Team Leader's (or Admin's) decision on a lead a sales rep created: `give_creator`,
    `give_rep` (another rep) or `auto` (release the hold; the engine runs right after commit and
    the `deal.pooled` event is its safety net). One audit row. Triage of a waiting lead is routine
    work, so the reason follows the routine rule (none for a Team Leader, required for an Admin)."""
    async with UnitOfWork(actor, request) as uow:
        await uow.advisory_lock("assign")
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("pool.assign", entity=("deal", deal.id))
        cleaned = policy.check(actor.role_enum, ActionClass.ROUTINE, reason)
        if deal.status != "pooled":
            raise Conflict("This lead is no longer in the Pool")
        entry = await open_pool_entry(uow.session, deal.id, lock=True)
        if entry is None or not entry.rep_hold:
            raise Conflict("This lead is not waiting for a Team Leader decision")
        warn: str | None = None
        if action == "auto":
            await release_rep_hold(
                uow,
                deal,
                entry,
                action_key="pool.assign",
                note=f"{actor.name} chose automatic round robin",
                new={"decision": "auto", "by": actor.name},
                reason=cleaned,
            )
        else:
            if action == "give_creator":
                person = (
                    await uow.session.get(User, deal.generated_by) if deal.generated_by else None
                )
                if (
                    person is None
                    or person.org_id != actor.org_id
                    or not person.is_active
                    or person.deleted_at is not None
                ):
                    raise ValidationFailed(
                        "The rep who created this lead is no longer active. "
                        "Choose another rep or use automatic assignment",
                        {"field": "action"},
                    )
                # Giving it back to its own creator is always valid, even when a source/industry/
                # city routing rule sent the lead to a different team's Team Leader than the
                # creator's own team -- _target()'s "own team only" scope check doesn't apply here.
                target = person
            else:
                if user_id is None:
                    raise ValidationFailed("Choose a rep", {"field": "user_id"})
                target = await _target(uow, user_id)
            warn = await _over_cap_note(uow, target)
            await apply_owner(
                uow,
                deal,
                target,
                method="manual",
                action_key="pool.assign",
                by=actor.id,
                reason=cleaned,
                note=warn
                or (
                    f"Given to the rep who created it by {actor.name}"
                    if deal.generated_by == target.id
                    else f"Manual by {actor.name}"
                ),
            )
        deal_pk = deal.id
    if action == "auto":
        try:  # run the engine now; the outbox 'deal.pooled' event is the safety net if this fails
            await assign_pooled(actor.org_id, deal_pk)
        except Exception:  # noqa: BLE001
            log.exception("assignment after the Team Leader's choice failed for %s", deal_pk)
    return await _decision(deal_pk, warn)


async def _decision(deal_id: uuid.UUID, warn: str | None) -> dict[str, Any]:
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        deal = await s.get(Deal, deal_id)
        if deal is None:
            raise NotFound("Not found")
        people = await names(s, {deal.owner_id})
        return {
            "deal_id": deal.id,
            "status": deal.status,
            "owner": {"id": deal.owner_id, "name": people[deal.owner_id]}
            if deal.owner_id
            else None,
            "assignment_method": deal.assignment_method,
            "warning": warn,
        }


async def waiting_for_leader(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    """The caller's own leads that still wait for their Team Leader (company and times only):
    the creator cannot open a lead they do not own, so this is their 'waiting' list."""
    await authorize_read(session, actor, "lead.create")
    window = timedelta(minutes=await hold_minutes(session, actor.org_id))
    rows = (
        await session.execute(
            select(LeadPoolEntry, Deal, Account.name)
            .join(Deal, Deal.id == LeadPoolEntry.deal_id)
            .join(Account, Account.id == Deal.account_id)
            .where(
                LeadPoolEntry.org_id == actor.org_id,
                LeadPoolEntry.left_at.is_(None),
                LeadPoolEntry.rep_hold.is_(True),
                Deal.status == "pooled",
                Deal.deleted_at.is_(None),
                Deal.generated_by == actor.id,
            )
            .order_by(LeadPoolEntry.entered_at, LeadPoolEntry.id)
        )
    ).all()
    return [
        {
            "deal_id": d.id,
            "company": company,
            "created_at": e.entered_at,
            "auto_assign_at": e.entered_at + window,
        }
        for e, d, company in rows
    ]


async def reassign(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, user_id: uuid.UUID, reason: str | None
) -> tuple[uuid.UUID, str | None]:
    async with UnitOfWork(actor, request) as uow:
        await uow.advisory_lock("assign")
        deal = await load_deal(uow, deal_id, lock=True)
        key = "lead.reassign" if deal.stage_key == "lead" else "deal.owner.change"
        await uow.authorize(key, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(key, reason)
        if deal.status != "open":
            raise Conflict("Only open deals can be reassigned")
        target = await _target(uow, user_id)
        if target.id == deal.owner_id:
            raise Conflict("The deal already belongs to this person")
        warn = await _over_cap_note(uow, target)
        await apply_owner(
            uow,
            deal,
            target,
            method="manual",
            action_key=key,
            by=actor.id,
            reason=cleaned,
            note=warn,
        )
        return deal.id, warn


async def bulk_reassign(
    actor: Actor,
    request: RequestInfo,
    from_user_id: uuid.UUID,
    to_user_id: uuid.UUID | None,
    spread: bool,
    reason: str | None,
) -> dict[str, Any]:
    """Move a departing rep's open deals: all to one person, or round-robin across their team."""
    if not spread and to_user_id is None:
        raise ValidationFailed("Choose who receives the deals, or spread them across the team")
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.advisory_lock("assign")
        src = (
            await s.execute(
                select(User).where(User.id == from_user_id, User.org_id == actor.org_id)
            )
        ).scalar_one_or_none()
        if src is None:
            raise NotFound("Rep not found")
        await uow.authorize("deal.bulk_reassign", entity=("user", src.id))
        cleaned = uow.reason("deal.bulk_reassign", reason)
        if spread:
            pool = (
                [
                    r
                    for r in await candidates(s, actor.org_id, "team", src.team_id)
                    if r.id != src.id
                ]
                if src.team_id
                else []
            )
            if not pool:
                raise Conflict("There is no one else in that team to receive the deals")
        else:
            pool = [await _target(uow, to_user_id)]  # type: ignore[arg-type]
            if pool[0].id == src.id:
                raise Conflict("Choose someone other than the departing rep")
        deals = list(
            (
                await s.execute(
                    select(Deal)
                    .where(
                        Deal.owner_id == src.id,
                        Deal.status == "open",
                        Deal.deleted_at.is_(None),
                        Deal.org_id == actor.org_id,
                    )
                    .order_by(Deal.created_at)
                    .with_for_update()
                )
            ).scalars()
        )
        moved: list[uuid.UUID] = []
        for i, deal in enumerate(deals):
            await apply_owner(
                uow,
                deal,
                pool[i % len(pool)],
                method="manual",
                action_key="deal.bulk_reassign",
                by=actor.id,
                reason=cleaned,
                note=f"Bulk reassign from {src.name}",
            )
            moved.append(deal.id)
        await uow.audit(
            "deal.bulk_reassign",
            "user",
            src.id,
            new={"moved": len(moved), "to": [p.name for p in pool]},
            reason=cleaned,
        )
        return {"moved": len(moved), "skipped": 0, "deals": moved}


async def move_team_direct(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    to_team_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    reason: str | None,
) -> uuid.UUID:
    """Admin (with a reason) or Super admin moves a deal to another team without a request."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.advisory_lock("assign")
        deal = await load_deal(uow, deal_id, lock=True)
        await uow.authorize("deal.team_transfer.decide_or_move", entity=("deal", deal.id))
        cleaned = uow.reason("deal.team_transfer.decide_or_move", reason)
        await move_deal_to_team(
            uow, deal, to_team_id, owner_id, cleaned, action="deal.team_transfer.decide_or_move"
        )
        _ = s
        return deal.id


async def move_deal_to_team(
    uow: UnitOfWork,
    deal: Deal,
    to_team_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    reason: str | None,
    *,
    action: str,
) -> None:
    from app.modules.users.models import Team

    team = (
        await uow.session.execute(
            select(Team).where(
                Team.id == to_team_id, Team.org_id == uow.actor.org_id, Team.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if team is None:
        raise NotFound("Team not found")
    if deal.status not in ("open", "pooled") or deal.deleted_at is not None:
        raise Conflict("Only open or pooled deals can be moved to another team")
    if deal.team_id == team.id:
        raise Conflict("The deal is already in that team")
    if owner_id is not None:
        rep = (
            await uow.session.execute(
                select(User).where(User.id == owner_id, User.org_id == uow.actor.org_id)
            )
        ).scalar_one_or_none()
        if rep is None or not rep.is_active or rep.team_id != team.id:
            raise ValidationFailed("The chosen rep must be an active member of the receiving team")
        await apply_owner(
            uow,
            deal,
            rep,
            method="manual",
            action_key=action,
            by=uow.actor.id,
            reason=reason,
            note=f"Moved to {team.name}",
        )
    else:
        await return_to_pool(
            uow, deal, team.id, source="transferred to this team", hold=True, reason=reason
        )


# ============================================================================= team-move hook
async def on_user_team_moved(
    uow: UnitOfWork, user: User, old_team: uuid.UUID | None, new_team: uuid.UUID | None
) -> None:
    """Phase 1 hook. A rep's open deals move with them unless the Admin chose to send them back
    to the Pool (uow.options['open_deals'] == 'return_to_pool')."""
    s = uow.session
    deals = list(
        (
            await s.execute(
                select(Deal)
                .where(Deal.owner_id == user.id, Deal.status == "open", Deal.deleted_at.is_(None))
                .with_for_update()
            )
        ).scalars()
    )
    mode = uow.options.get("open_deals", "move")
    teams = await team_names(s, {old_team, new_team})
    for deal in deals:
        if mode == "return_to_pool":
            await return_to_pool(uow, deal, old_team, source=f"{user.name} left the team")
        else:
            before = {"team": teams.get(old_team) if old_team else None}
            deal.team_id, deal.version, deal.updated_at = new_team, deal.version + 1, utc_now()
            await uow.audit(
                "team.manage",
                "deal",
                deal.id,
                old=before,
                new={"team": teams.get(new_team) if new_team else None},
                note=f"Moved with {user.name}",
            )
            await uow.emit(events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
