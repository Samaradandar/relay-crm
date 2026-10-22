# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Accounts and contacts: scope-aware reads, the duplicate decision, edits and merge.

A person can see an account header (company, website, industry, city) if they can see ANY deal
on it. Deals outside their scope appear only as a locked row, "Deal owned by another team".
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    Conflict,
    DuplicateBlocked,
    DuplicateWarning,
    NotFound,
    ValidationFailed,
)
from app.core.normalize import clean_text, normalize_domain, normalize_email, normalize_phone
from app.core.time import utc_now
from app.modules.accounts import events
from app.modules.accounts.duplicates import Match, find_matches, split
from app.modules.accounts.models import Account, Contact
from app.modules.leads.models import Deal
from app.modules.pipeline.engine.service import recheck_proofs
from app.modules.users.models import Team, User
from app.platform.context import Actor, RequestInfo
from app.platform.permissions import loader
from app.platform.permissions.roles import Scope
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork


def _snap(a: Account) -> dict[str, Any]:
    return {
        "name": a.name,
        "website": a.website,
        "domain": a.domain,
        "industry": a.industry,
        "city": a.city,
    }


def _csnap(c: Contact) -> dict[str, Any]:
    return {
        "name": c.name,
        "role": c.role,
        "phone": c.phone_e164,
        "email": c.email,
        "is_decision_maker": c.is_decision_maker,
    }


async def _deal_scope(session: AsyncSession, actor: Actor) -> Scope:
    return (await loader.get_table(session))[(actor.role_enum, "deal.view")].scope


def _visible_condition(actor: Actor, scope: Scope) -> ColumnElement[bool]:
    if scope == Scope.ALL and actor.is_privileged:
        return true()
    # PRD 4.6: access follows the deals. The creator keeps an account only while it has no live
    # deal at all (a company added on its own); once it has deals only their scope counts, so a
    # transferred lead's old creator loses it at once (BUG-P2-27).
    no_live_deal = ~Account.id.in_(select(Deal.account_id).where(Deal.deleted_at.is_(None)))
    conds: list[ColumnElement[bool]] = [and_(Account.created_by == actor.id, no_live_deal)]
    if scope != Scope.NONE:
        conds.append(
            Account.id.in_(
                select(Deal.account_id).where(
                    *scope_conditions(actor, Deal, scope, team_attr="team_id"),
                    Deal.deleted_at.is_(None),
                )
            )
        )
    return or_(*conds)


async def _load_visible(session: AsyncSession, actor: Actor, account_id: uuid.UUID) -> Account:
    scope = await _deal_scope(session, actor)
    acc = (
        await session.execute(
            select(Account).where(
                Account.id == account_id,
                Account.org_id == actor.org_id,
                Account.deleted_at.is_(None),
                _visible_condition(actor, scope),
            )
        )
    ).scalar_one_or_none()
    if acc is None:
        raise NotFound("Account not found")
    return acc


def _header(a: Account) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "website": a.website,
        "domain": a.domain,
        "industry": a.industry,
        "city": a.city,
        "account_owner_id": a.account_owner_id,
    }


def _contact_out(c: Contact) -> dict[str, Any]:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "name": c.name,
        "role": c.role,
        "phone": c.phone_e164,
        "email": c.email,
        "is_decision_maker": c.is_decision_maker,
    }


# ---------------------------------------------------------------------------- reads
async def list_accounts(
    session: AsyncSession, actor: Actor, q: str | None, limit: int = 25
) -> list[dict[str, Any]]:
    await authorize_read(session, actor, "deal.view")
    scope = await _deal_scope(session, actor)
    stmt = select(Account).where(
        Account.org_id == actor.org_id,
        Account.deleted_at.is_(None),
        Account.merged_into_id.is_(None),
        _visible_condition(actor, scope),
    )
    if q and q.strip():
        from app.modules.accounts.duplicates import similar_accounts

        ids = {i for i, _ in await similar_accounts(session, actor.org_id, q.strip(), 0.3)}
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(Account.id.in_(ids), func.lower(Account.name).like(like)))
    rows = (await session.execute(stmt.order_by(Account.name).limit(limit))).scalars()
    return [_header(a) for a in rows]


async def get_account(session: AsyncSession, actor: Actor, account_id: uuid.UUID) -> dict[str, Any]:
    await authorize_read(session, actor, "deal.view")
    acc = await _load_visible(session, actor, account_id)
    scope = await _deal_scope(session, actor)
    deals = (
        (
            await session.execute(
                select(Deal)
                .where(Deal.account_id == acc.id, Deal.deleted_at.is_(None))
                .order_by(Deal.created_at)
            )
        )
        .scalars()
        .all()
    )
    visible_ids = {
        r[0]
        for r in (
            await session.execute(
                select(Deal.id).where(
                    Deal.account_id == acc.id,
                    Deal.deleted_at.is_(None),
                    *scope_conditions(actor, Deal, scope, team_attr="team_id"),
                )
            )
        ).all()
    }
    people = (
        {
            u.id: u.name
            for u in (
                await session.execute(
                    select(User).where(User.id.in_({d.owner_id for d in deals if d.owner_id}))
                )
            ).scalars()
        }
        if any(d.owner_id for d in deals)
        else {}
    )
    teams = {
        t.id: t.name
        for t in (await session.execute(select(Team).where(Team.org_id == actor.org_id))).scalars()
    }
    visible = [d for d in deals if d.id in visible_ids]
    contacts: list[Contact] = []
    if visible or actor.is_privileged or not deals:
        contacts = list(
            (
                await session.execute(
                    select(Contact).where(
                        Contact.account_id == acc.id, Contact.deleted_at.is_(None)
                    )
                )
            ).scalars()
        )
        if not actor.is_privileged:
            # a contact that came with another team's (locked) deal stays hidden (BUG-P2-41)
            locked_only = {
                d.primary_contact_id
                for d in deals
                if d.id not in visible_ids and d.primary_contact_id
            } - {d.primary_contact_id for d in visible if d.primary_contact_id}
            contacts = [c for c in contacts if c.id not in locked_only]
    return {
        **_header(acc),
        "deals": [
            {
                "id": d.id,
                "status": d.status,
                "stage_key": d.stage_key,
                "lead_source": d.lead_source,
                "owner_name": people.get(d.owner_id) if d.owner_id else None,
                "team_name": teams.get(d.team_id) if d.team_id else None,
            }
            for d in visible
        ],
        "locked_deals": [
            {"label": "Deal owned by another team"} for d in deals if d.id not in visible_ids
        ],
        "contacts": [_contact_out(c) for c in contacts],
    }


# ---------------------------------------------------------------------------- duplicate decision
@dataclass
class DuplicateDecision:
    attach_to: Account | None = None
    overrides: list[Match] = field(default_factory=list)
    reason: str | None = None
    kind: str | None = None  # "override_block" | "create_anyway"
    outside_scope: bool = False


async def resolve_duplicates(
    uow: UnitOfWork,
    *,
    company: str,
    domain: str | None,
    phone: str | None,
    email: str | None,
    create_anyway: bool,
    override_block: bool,
    reason: str | None,
) -> DuplicateDecision:
    """Apply the duplicate rule for a new lead or account (PRD 4.6)."""
    matches = await find_matches(
        uow.session, uow.actor, company=company, domain=domain, phone_e164=phone, email=email
    )
    block, warn = split(matches)
    if block:
        if not override_block:
            raise DuplicateBlocked(
                "This company already exists", {"matches": [m.public() for m in block]}
            )
        await uow.authorize("duplicate.override_or_merge")
        why = uow.reason("duplicate.override_or_merge", reason)
        target = (
            await uow.session.execute(select(Account).where(Account.id == block[0].account_id))
        ).scalar_one()
        return DuplicateDecision(
            target, [*block, *warn], why, "override_block", any(not m.in_scope for m in matches)
        )
    if warn:
        if not create_anyway:
            raise DuplicateWarning(
                "A similar company already exists", {"matches": [m.public() for m in warn]}
            )
        await uow.authorize("duplicate.create_anyway")
        why = uow.reason("duplicate.create_anyway", reason)
        return DuplicateDecision(
            None, warn, why, "create_anyway", any(not m.in_scope for m in warn)
        )
    return DuplicateDecision()


# ---------------------------------------------------------------------------- writes
async def create_account_row(
    uow: UnitOfWork,
    *,
    name: str,
    website: str | None,
    industry: str | None,
    city: str | None,
    reason: str | None = None,
) -> Account:
    domain = normalize_domain(website)
    acc = Account(
        org_id=uow.actor.org_id,
        name=clean_text(name) or name,
        website=clean_text(website),
        domain=domain,
        industry=clean_text(industry),
        city=clean_text(city),
        created_by=uow.actor.id,
    )
    uow.session.add(acc)
    await uow.session.flush()
    await uow.audit("lead.create", "account", acc.id, new=_snap(acc), reason=reason)
    await uow.emit(events.ACCOUNT_CREATED, {"account_id": str(acc.id)})
    return acc


async def create_contact_row(
    uow: UnitOfWork,
    *,
    account_id: uuid.UUID,
    name: str,
    role: str | None,
    phone: str | None,
    email: str | None,
    is_decision_maker: bool = False,
) -> Contact:
    c = Contact(
        org_id=uow.actor.org_id,
        account_id=account_id,
        name=clean_text(name) or name,
        role=clean_text(role),
        phone_e164=normalize_phone(phone),
        email=normalize_email(email),
        is_decision_maker=is_decision_maker,
        created_by=uow.actor.id,
    )
    uow.session.add(c)
    await uow.session.flush()
    await uow.audit("lead.create", "contact", c.id, new=_csnap(c))
    return c


async def create_account(actor: Actor, request: RequestInfo, body: Any) -> Account:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("lead.create")
        if body.website and not normalize_domain(body.website):
            raise ValidationFailed("That website is not a valid address", {"field": "website"})
        # Admin: the reason is always required, also with create_anyway (BUG-P2-32)
        routine_reason = uow.reason("lead.create", body.reason)
        await uow.advisory_lock("lead_create")
        decision = await resolve_duplicates(
            uow,
            company=body.name,
            domain=normalize_domain(body.website),
            phone=None,
            email=None,
            create_anyway=body.create_anyway,
            override_block=False,
            reason=body.reason,
        )
        acc = await create_account_row(
            uow,
            name=body.name,
            website=body.website,
            industry=body.industry,
            city=body.city,
            reason=routine_reason,  # the Admin's reason is part of the trail (BUG-P2-34)
        )
        if decision.kind:
            await uow.audit(
                "duplicate.create_anyway",
                "account",
                acc.id,
                new={"matches": [m.company for m in decision.overrides]},
                reason=decision.reason,
            )
        return acc


async def update_account(
    actor: Actor,
    request: RequestInfo,
    account_id: uuid.UUID,
    patch: dict[str, Any],
    reason: str | None,
) -> Account:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("deal.edit")
        cleaned = uow.reason("deal.edit", reason)
        acc = await _load_visible(uow.session, actor, account_id)
        old = _snap(acc)
        if "name" in patch and patch["name"] is not None:
            acc.name = clean_text(patch["name"]) or acc.name
        if "website" in patch:
            if patch["website"] and not normalize_domain(patch["website"]):
                raise ValidationFailed("That website is not a valid address", {"field": "website"})
            acc.website = clean_text(patch["website"])
            new_domain = normalize_domain(acc.website)
            if new_domain != acc.domain:
                if new_domain:
                    clash = (
                        await uow.session.execute(
                            select(Account.id).where(
                                Account.org_id == actor.org_id,
                                Account.domain == new_domain,
                                Account.deleted_at.is_(None),
                                Account.merged_into_id.is_(None),
                                Account.id != acc.id,
                            )
                        )
                    ).first()
                    if clash:
                        raise Conflict("Another account already uses this website")
                acc.domain = new_domain
        if "industry" in patch:
            acc.industry = clean_text(patch["industry"])
        if "city" in patch:
            acc.city = clean_text(patch["city"])
        acc.updated_at = utc_now()
        await uow.session.flush()
        await uow.audit("deal.edit", "account", acc.id, old=old, new=_snap(acc), reason=cleaned)
        await uow.emit(events.ACCOUNT_UPDATED, {"account_id": str(acc.id)})
        return acc


async def create_contact(actor: Actor, request: RequestInfo, body: Any) -> Contact:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("deal.edit")
        cleaned = uow.reason("deal.edit", body.reason)
        acc = await _load_visible(uow.session, actor, body.account_id)
        if body.phone and not normalize_phone(body.phone):
            raise ValidationFailed("That phone number is not valid", {"field": "phone"})
        if body.email and not normalize_email(body.email):
            raise ValidationFailed("That email address is not valid", {"field": "email"})
        if not (normalize_phone(body.phone) or normalize_email(body.email)):
            raise Conflict("Enter a valid phone number or email")
        c = await create_contact_row(
            uow,
            account_id=acc.id,
            name=body.name,
            role=body.role,
            phone=body.phone,
            email=body.email,
            is_decision_maker=body.is_decision_maker,
        )
        if cleaned:
            await uow.audit(
                "deal.edit", "account", acc.id, new={"contact_added": c.name}, reason=cleaned
            )
        await uow.emit(events.CONTACT_CREATED, {"contact_id": str(c.id)})
        return c


async def update_contact(
    actor: Actor,
    request: RequestInfo,
    contact_id: uuid.UUID,
    patch: dict[str, Any],
    reason: str | None,
) -> Contact:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("deal.edit")
        cleaned = uow.reason("deal.edit", reason)
        c = (
            await uow.session.execute(
                select(Contact).where(
                    Contact.id == contact_id,
                    Contact.org_id == actor.org_id,
                    Contact.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if c is None:
            raise NotFound("Contact not found")
        await _load_visible(uow.session, actor, c.account_id)
        old = _csnap(c)
        if patch.get("name"):
            c.name = clean_text(patch["name"]) or c.name
        if "role" in patch:
            c.role = clean_text(patch["role"])
        if "phone" in patch:
            c.phone_e164 = normalize_phone(patch["phone"])
            if patch["phone"] and not c.phone_e164:
                raise ValidationFailed("That phone number is not valid", {"field": "phone"})
        if "email" in patch:
            c.email = normalize_email(patch["email"])
            if patch["email"] and not c.email:
                raise ValidationFailed("That email address is not valid", {"field": "email"})
        if patch.get("is_decision_maker") is not None:
            c.is_decision_maker = bool(patch["is_decision_maker"])
        if not (c.phone_e164 or c.email):
            raise Conflict("A contact needs a valid phone number or email")
        c.updated_at = utc_now()
        await uow.session.flush()
        # a contact is proof for 'decision maker identified' and 'basic info': re-check the ticks
        for deal in (
            await uow.session.execute(
                select(Deal)
                .where(
                    Deal.account_id == c.account_id,
                    Deal.status.in_(("open", "won")),
                    Deal.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).scalars():
            await recheck_proofs(uow, deal)
        await uow.audit("deal.edit", "contact", c.id, old=old, new=_csnap(c), reason=cleaned)
        await uow.emit(events.CONTACT_UPDATED, {"contact_id": str(c.id)})
        return c


async def merge_accounts(
    actor: Actor, request: RequestInfo, source_id: uuid.UUID, into_id: uuid.UUID, reason: str | None
) -> Account:
    """Merge `source` into `into`. Deals and contacts move over; the source row is kept with
    merged_into_id set and is never erased."""
    if source_id == into_id:
        raise Conflict("Choose a different account to merge into")
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("duplicate.override_or_merge")
        cleaned = uow.reason("duplicate.override_or_merge", reason)
        s = uow.session
        rows = {
            a.id: a
            for a in (
                await s.execute(
                    select(Account)
                    .where(Account.id.in_([source_id, into_id]), Account.org_id == actor.org_id)
                    .order_by(Account.id)
                    .with_for_update()
                )
            ).scalars()
        }
        src, dst = rows.get(source_id), rows.get(into_id)
        if (
            not src
            or not dst
            or src.merged_into_id
            or dst.merged_into_id
            or src.deleted_at
            or dst.deleted_at
        ):
            raise NotFound("Account not found")
        moved_deal_ids = list(
            (await s.execute(select(Deal.id).where(Deal.account_id == src.id))).scalars()
        )
        moved_deals = len(moved_deal_ids)
        await s.execute(update(Deal).where(Deal.account_id == src.id).values(account_id=dst.id))
        moved_contacts = getattr(
            await s.execute(
                update(Contact).where(Contact.account_id == src.id).values(account_id=dst.id)
            ),
            "rowcount",
            0,
        )
        old_src = _snap(src)
        # Write the source first: once merged it leaves the unique-domain index, so the
        # survivor may adopt the domain (BUG-P2-15). When the survivor has its own domain the
        # source keeps its domain, so that website still blocks new leads (BUG-P2-13).
        adopt = dst.domain is None and bool(old_src["domain"])
        src.merged_into_id, src.updated_at = dst.id, utc_now()
        if adopt:
            src.domain = None
        await s.flush()
        if adopt:
            dst.domain, dst.website = old_src["domain"], dst.website or src.website
            await s.flush()
        note = {
            "merged_account": src.name,
            "deals_moved": moved_deals,
            "contacts_moved": moved_contacts,
        }
        await uow.audit("duplicate.override_or_merge", "account", dst.id, new=note, reason=cleaned)
        for deal_id in moved_deal_ids:  # the deal's own history shows the move (BUG-P2-14)
            await uow.audit(
                "duplicate.override_or_merge",
                "deal",
                deal_id,
                old={"company": src.name},
                new={"company": dst.name},
                reason=cleaned,
                note="Moved by company merge",
            )
        await uow.audit(
            "duplicate.override_or_merge",
            "account",
            src.id,
            old=old_src,
            new={"merged_into": dst.name},
            reason=cleaned,
        )
        await uow.emit(events.ACCOUNT_MERGED, {"from": str(src.id), "into": str(dst.id)})
        return dst
