# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Scope-aware duplicate check (PRD 4.6).

The check runs against ALL records in the org but only reveals what the caller's scope allows:

  exact domain / phone / email   inside scope: block, "Open existing" (with the deal id)
                                 outside scope: block, company name and owner only
  similar company name           inside scope: warn, show matches
                                 outside scope: warn, company name and owner only

Outside scope nothing else leaks: no deal id, stage, value or contact. Deals owned by a Super
admin are hidden from Team Leaders and Users, so even the owner name is withheld for them.
"""

import asyncio
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trigram import grams_similarity, name_trigrams, prefilter_grams
from app.modules.accounts.models import Account, Contact
from app.modules.customers.models import Customer
from app.modules.leads.models import Deal
from app.modules.users.models import Team, User
from app.platform.context import Actor
from app.platform.permissions import loader
from app.platform.permissions.roles import Role, Scope
from app.platform.permissions.scope import select_scoped

NAME_THRESHOLD = 0.5  # trigram similarity; tune against real company names

_trgm_available: bool | None = None


async def trgm_available(session: AsyncSession) -> bool:
    global _trgm_available
    if _trgm_available is None:
        row = await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
        _trgm_available = row.first() is not None
    return _trgm_available


@dataclass
class Match:
    match_type: str  # domain | phone | email | name
    account_id: uuid.UUID
    company: str
    owner_name: str | None
    team_name: str | None
    in_scope: bool
    deal_id: uuid.UUID | None  # only ever set when in_scope
    similarity: float | None = None
    # Phase 5 (P5-01): this account already has an active Customer record. The caller should be
    # steered to "create a request" instead of the normal duplicate-lead warning.
    is_existing_customer: bool = False
    customer_id: uuid.UUID | None = None  # only ever set when in_scope

    def public(self) -> dict[str, Any]:
        """What may be sent to the caller. Out-of-scope matches carry no ids at all."""
        out: dict[str, Any] = {
            "match_type": self.match_type,
            "company": self.company,
            "owner_name": self.owner_name,
            "in_scope": self.in_scope,
        }
        if self.in_scope:
            out["team_name"] = self.team_name  # PRD 4.6: outside scope only company and owner
            out["account_id"] = str(self.account_id)
            out["deal_id"] = str(self.deal_id) if self.deal_id else None
            out["similarity"] = self.similarity
            out["is_existing_customer"] = self.is_existing_customer
            out["customer_id"] = str(self.customer_id) if self.customer_id else None
        return out


async def _deal_scope(session: AsyncSession, actor: Actor) -> Scope:
    table = await loader.get_table(session)
    return table[(actor.role_enum, "deal.view")].scope


_common_cache: tuple[float, frozenset[str]] | None = None


async def _common_grams(session: AsyncSession) -> frozenset[str]:
    """Grams PostgreSQL's statistics list as the most common in `accounts.name_grams` (cached for
    10 minutes). They make poor probes, so rarer grams are tried first. Empty when there are no
    statistics yet (a fresh table): the prefilter is still exact, only less selective."""
    global _common_cache
    now = time.monotonic()
    if _common_cache is None or now - _common_cache[0] > 600:
        try:
            rows = await session.execute(
                text(
                    "SELECT unnest(most_common_elems::text::text[]) FROM pg_stats "
                    "WHERE schemaname = 'public' AND tablename = 'accounts' "
                    "AND attname = 'name_grams'"
                )
            )
            _common_cache = (now, frozenset(r[0] for r in rows))
        except Exception:  # noqa: BLE001 - statistics are an optimisation only
            _common_cache = (now, frozenset())
    return _common_cache[1]


async def similar_accounts(
    session: AsyncSession, org_id: uuid.UUID, name: str, threshold: float
) -> list[tuple[uuid.UUID, float]]:
    """Accounts whose name reaches `threshold`. The GIN index on `accounts.name_grams` fetches only
    candidates that share enough trigrams with the query (prefix filtering, exact: no match is
    lost); rows without grams (raw SQL inserts) are always candidates. The exact score is then
    computed off the event loop, so a big table never stalls other requests (RISK-P2-01)."""
    query = name_trigrams(name)
    if not query:
        return []
    probe = prefilter_grams(query, threshold, await _common_grams(session))
    lo, hi = math.ceil(threshold * len(query) - 1e-9), math.floor(len(query) / threshold + 1e-9)
    live = [
        Account.org_id == org_id,
        Account.deleted_at.is_(None),
        Account.merged_into_id.is_(None),
    ]
    # shares one of the probe grams AND has a size that can still reach the score
    # (score <= min(|A|,|B|) / max(|A|,|B|)); a separate query for rows without grams (raw SQL
    # inserts) keeps the GIN index usable for the main one
    indexed = select(Account.id, Account.name).where(
        *live,
        Account.name_grams.overlap(probe),
        func.cardinality(Account.name_grams).between(lo, hi),
    )
    ungrammed = select(Account.id, Account.name).where(*live, Account.name_grams.is_(None))
    # The planner under-prices array operators and prefers a sequential scan of every account;
    # the GIN index is far cheaper, so steer it for this one statement only.
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    try:
        rows = [*(await session.execute(indexed)).all(), *(await session.execute(ungrammed)).all()]
    finally:
        await session.execute(text("SET LOCAL enable_seqscan = on"))
    if not rows:
        return []
    pairs = [(rid, rname) for rid, rname in rows]
    if len(pairs) <= 200:
        return _score(query, pairs, threshold)
    return await asyncio.to_thread(_score, query, pairs, threshold)


def _score(
    query: set[str], pairs: list[tuple[uuid.UUID, str]], threshold: float
) -> list[tuple[uuid.UUID, float]]:
    scored = [(rid, grams_similarity(query, name_trigrams(rname))) for rid, rname in pairs]
    return [(rid, s) for rid, s in scored if s >= threshold]


async def find_matches(
    session: AsyncSession,
    actor: Actor,
    *,
    company: str,
    domain: str | None,
    phone_e164: str | None,
    email: str | None,
    exclude_account_id: uuid.UUID | None = None,
) -> list[Match]:
    org = actor.org_id
    live = [Account.org_id == org, Account.deleted_at.is_(None), Account.merged_into_id.is_(None)]
    found: dict[uuid.UUID, tuple[str, float | None]] = {}  # account -> (match_type, similarity)

    if domain:
        # a merged account keeps its website when the survivor has its own: that website still
        # points at the company it was merged into (BUG-P2-13)
        for aid, merged_into in await session.execute(
            select(Account.id, Account.merged_into_id).where(
                Account.org_id == org, Account.deleted_at.is_(None), Account.domain == domain
            )
        ):
            target = aid
            for _ in range(10):
                if merged_into is None:
                    break
                target = merged_into
                merged_into = (
                    await session.execute(
                        select(Account.merged_into_id).where(Account.id == target)
                    )
                ).scalar_one_or_none()
            found[target] = ("domain", None)
    contact_filters = []
    if phone_e164:
        contact_filters.append(Contact.phone_e164 == phone_e164)
    if email:
        contact_filters.append(func.lower(Contact.email) == email.lower())
    if contact_filters:
        rows = await session.execute(
            select(Contact.account_id, Contact.phone_e164, Contact.email)
            .join(Account, Account.id == Contact.account_id)
            .where(*live, Contact.deleted_at.is_(None), or_(*contact_filters))
        )
        for aid, ph, _em in rows:
            kind = "phone" if phone_e164 and ph == phone_e164 else "email"
            found.setdefault(aid, (kind, None))
    for aid, score in await similar_accounts(session, org, company, NAME_THRESHOLD):
        found.setdefault(aid, ("name", score))
    if exclude_account_id:
        found.pop(exclude_account_id, None)
    if not found:
        return []

    accounts = {
        a.id: a
        for a in (await session.execute(select(Account).where(Account.id.in_(found)))).scalars()
    }
    scope = await _deal_scope(session, actor)
    visible: dict[uuid.UUID, uuid.UUID] = {}  # account -> one visible deal id
    if scope != Scope.NONE:
        for did, aid in await session.execute(
            select_scoped(actor, Deal, scope, team_attr="team_id")
            .where(Deal.account_id.in_(found), Deal.deleted_at.is_(None))
            .with_only_columns(Deal.id, Deal.account_id)
            .order_by(Deal.created_at)
        ):
            visible.setdefault(aid, did)

    # who owns it / which team (for the "owner: [name]" message), computed for every match
    all_deals = (
        await session.execute(
            select(Deal.account_id, Deal.owner_id, Deal.team_id)
            .where(Deal.account_id.in_(found), Deal.deleted_at.is_(None))
            .order_by(Deal.created_at.desc())
        )
    ).all()
    owner_of: dict[uuid.UUID, uuid.UUID | None] = {}
    team_of: dict[uuid.UUID, uuid.UUID | None] = {}
    for aid, deal_owner, deal_team in all_deals:
        owner_of.setdefault(aid, deal_owner)
        team_of.setdefault(aid, deal_team)
    owner_ids = {o for o in owner_of.values() if o}
    users = (
        {
            u.id: u
            for u in (await session.execute(select(User).where(User.id.in_(owner_ids)))).scalars()
        }
        if owner_ids
        else {}
    )
    team_ids = {t for t in team_of.values() if t}
    teams: dict[uuid.UUID, str] = {}
    if team_ids:
        teams = {
            t.id: t.name
            for t in (await session.execute(select(Team).where(Team.id.in_(team_ids)))).scalars()
        }
    customer_of: dict[uuid.UUID, uuid.UUID] = {
        aid: cid
        for aid, cid in (
            await session.execute(
                select(Customer.account_id, Customer.id).where(
                    Customer.account_id.in_(found),
                    Customer.org_id == org,
                    Customer.status == "active",
                    Customer.deleted_at.is_(None),
                )
            )
        ).all()
    }

    out: list[Match] = []
    privileged = actor.role in (Role.ADMIN, Role.SUPER_ADMIN)
    for aid, (kind, sim) in found.items():
        acc = accounts[aid]
        in_scope = (scope == Scope.ALL and privileged) or aid in visible
        owner_id = owner_of.get(aid)
        owner = users.get(owner_id) if owner_id else None
        hide_owner = owner is not None and owner.role == Role.SUPER_ADMIN and not privileged
        out.append(
            Match(
                match_type=kind,
                account_id=aid,
                company=acc.name,
                owner_name=None if hide_owner else (owner.name if owner else None),
                team_name=None if hide_owner else teams.get(team_of.get(aid) or uuid.UUID(int=0)),
                in_scope=in_scope,
                deal_id=visible.get(aid) if in_scope else None,
                similarity=sim,
                is_existing_customer=aid in customer_of,
                customer_id=customer_of.get(aid) if in_scope else None,
            )
        )
    order = {"domain": 0, "phone": 1, "email": 2, "name": 3}
    return sorted(out, key=lambda m: (order[m.match_type], -(m.similarity or 0)))


def split(matches: list[Match]) -> tuple[list[Match], list[Match]]:
    """(exact matches that block, similar-name matches that warn)."""
    return [m for m in matches if m.match_type != "name"], [
        m for m in matches if m.match_type == "name"
    ]
