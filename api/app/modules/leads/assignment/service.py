# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Lead assignment engine and its settings (PRD 6).

1. Rules, in priority order: the FIRST matching rule decides the target (a team, a rep group
   or one rep). No match: the default team.
2. Round-robin inside the target, among ACTIVE sales reps only. Team Leaders, Admins and Super
   admins never receive round-robin leads. Reps at their cap are skipped.
3. Nobody eligible: the lead stays in the Pool with the reason, and the routed team's Team
   Leader is told once. Waiting leads are retried (oldest first) when anything that could free
   a slot changes, and every 15 minutes as a safety net.

Concurrency: every assignment for an org runs under one advisory lock, and the rep's active-lead
count is read inside that lock, so 50 leads arriving together are each assigned once and no rep
ever ends up over cap. The engine is idempotent per deal.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_sessionmaker
from app.core.errors import Conflict, NotFound
from app.core.time import utc_now
from app.modules.accounts.models import Account
from app.modules.leads import events
from app.modules.leads.assignment.models import (
    AssignmentRule,
    AssignmentSettings,
    RepCap,
    RepGroup,
    RepGroupMember,
    RrPointer,
)
from app.modules.leads.models import Deal, LeadPoolEntry
from app.modules.leads.ownership import (
    active_lead_counts,
    apply_owner,
    open_pool_entry,
    pool_rt,
    release_rep_hold,
)
from app.modules.users.models import Team, User
from app.platform.context import Actor, RequestInfo, system_actor
from app.platform.permissions.roles import Role
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

MATCH_KEYS = ("lead_source", "industry", "city", "category", "lead_type")
DEFAULT_CAP = 25
DEFAULT_HOLD_MINUTES = 120
REP_ENTRY = "rep entry"  # LeadPoolEntry.entered_source of a lead a sales rep created
log = logging.getLogger("relay.assignment")


@dataclass
class Outcome:
    status: str  # assigned | waiting | skipped
    owner_id: uuid.UUID | None = None
    reason: str | None = None
    rule_name: str | None = None


# ----------------------------------------------------------------------------- settings access
async def settings_row(session: AsyncSession, org_id: uuid.UUID) -> AssignmentSettings:
    row = await session.get(AssignmentSettings, org_id)
    if row is None:
        # two first saves at once: the loser's insert is a no-op, then both read the one row
        await session.execute(
            pg_insert(AssignmentSettings)
            .values(org_id=org_id, default_cap=DEFAULT_CAP)
            .on_conflict_do_nothing()
        )
        row = (
            await session.execute(
                select(AssignmentSettings)
                .where(AssignmentSettings.org_id == org_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
    return row


def rule_matches(rule: AssignmentRule, facts: dict[str, str | None]) -> bool:
    """All listed conditions must match (case-insensitive). An empty match matches everything."""
    for key, want in rule.match.items():
        have = facts.get(key)
        if have is None or have.strip().lower() != str(want).strip().lower():
            return False
    return True


async def active_rules(session: AsyncSession, org_id: uuid.UUID) -> list[AssignmentRule]:
    return list(
        (
            await session.execute(
                select(AssignmentRule)
                .where(
                    AssignmentRule.org_id == org_id,
                    AssignmentRule.active.is_(True),
                    AssignmentRule.deleted_at.is_(None),
                )
                .order_by(AssignmentRule.priority, AssignmentRule.id)
            )
        ).scalars()
    )


async def resolve_target(
    session: AsyncSession, org_id: uuid.UUID, facts: dict[str, str | None]
) -> tuple[AssignmentRule | None, str | None, uuid.UUID | None]:
    for rule in await active_rules(session, org_id):
        if rule_matches(rule, facts):
            return rule, rule.target_type, rule.target_id
    default = (await settings_row(session, org_id)).default_team_id
    return (None, "team", default) if default else (None, None, None)


async def candidates(
    session: AsyncSession, org_id: uuid.UUID, ttype: str, tid: uuid.UUID
) -> list[User]:
    """Active sales reps (role 'user') the target covers, in a stable order."""
    base = select(User).where(
        User.org_id == org_id,
        User.role == Role.USER,
        User.is_active.is_(True),
        User.deleted_at.is_(None),
    )
    if ttype == "team":
        base = base.where(User.team_id == tid)
    elif ttype == "group":
        base = base.where(
            User.id.in_(select(RepGroupMember.user_id).where(RepGroupMember.group_id == tid))
        )
    else:
        base = base.where(User.id == tid)
    return list((await session.execute(base.order_by(User.id))).scalars())


async def caps_for(
    session: AsyncSession, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    default = (await settings_row(session, org_id)).default_cap
    rows = (
        {
            r.user_id: r.cap
            for r in (
                await session.execute(select(RepCap).where(RepCap.user_id.in_(user_ids)))
            ).scalars()
        }
        if user_ids
        else {}
    )
    return {uid: rows.get(uid, default) for uid in user_ids}


async def routed_team(
    session: AsyncSession, ttype: str | None, tid: uuid.UUID | None
) -> uuid.UUID | None:
    if ttype is None or tid is None:
        return None
    if ttype == "team":
        return tid
    if ttype == "group":
        g = await session.get(RepGroup, tid)
        if g and g.team_id:
            return g.team_id
        first = (
            await session.execute(
                select(User.team_id)
                .join(RepGroupMember, RepGroupMember.user_id == User.id)
                .where(RepGroupMember.group_id == tid, User.team_id.is_not(None))
                .limit(1)
            )
        ).scalar_one_or_none()
        return first
    u = await session.get(User, tid)
    return u.team_id if u else None


async def _pointer(
    session: AsyncSession, org_id: uuid.UUID, ttype: str, tid: uuid.UUID
) -> RrPointer:
    await session.execute(
        pg_insert(RrPointer)
        .values(org_id=org_id, scope_type=ttype, scope_id=tid)
        .on_conflict_do_nothing()
    )
    return (
        await session.execute(
            select(RrPointer)
            .where(
                RrPointer.org_id == org_id, RrPointer.scope_type == ttype, RrPointer.scope_id == tid
            )
            .with_for_update()
        )
    ).scalar_one()


def rotate(reps: list[User], last: uuid.UUID | None) -> list[User]:
    """The reps in round-robin order: starting right after the last one served."""
    if last is None:
        return reps
    ids = [r.id for r in reps]
    if last in ids:
        start = ids.index(last) + 1
    else:  # the rep served last left: carry on with the next rep after them in the stable order
        start = next((i for i, rid in enumerate(ids) if rid > last), 0)
    return reps[start:] + reps[:start]


# ----------------------------------------------------------------------------- rep-lead hold
async def hold_minutes(session: AsyncSession, org_id: uuid.UUID) -> int:
    """How long a rep-created lead waits for its Team Leader (org setting, default 120)."""
    value = (
        await session.execute(
            select(AssignmentSettings.rep_lead_hold_minutes).where(
                AssignmentSettings.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    return value if value is not None else DEFAULT_HOLD_MINUTES


async def team_for_rep_lead(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    lead_source: str,
    account: Account,
    creator_team: uuid.UUID | None,
) -> uuid.UUID | None:
    """The team whose Team Leader decides: the team a matching rule routes the lead to; with no
    matching rule the creating rep's own team; only a creator without a team falls back to the
    default team."""
    facts = {"lead_source": lead_source, "industry": account.industry, "city": account.city}
    rule, ttype, tid = await resolve_target(session, org_id, facts)
    if rule is not None:
        team = await routed_team(session, ttype, tid)
        if team is not None:
            return team
    return creator_team or await routed_team(session, ttype, tid)


async def expire_rep_holds(org_id: uuid.UUID, now: datetime | None = None) -> int:
    """Hold window over: give every rep-created lead whose Team Leader did not decide to the
    engine (automatic round robin). Returns how many holds were released. Safe to run twice:
    a released entry no longer has `rep_hold`. `now` is injectable so tests control the clock."""
    clock = now or utc_now()
    factory = get_sessionmaker()
    async with factory() as s:
        minutes = await hold_minutes(s, org_id)
        ids = list(
            (
                await s.execute(
                    select(LeadPoolEntry.deal_id)
                    .where(
                        LeadPoolEntry.org_id == org_id,
                        LeadPoolEntry.left_at.is_(None),
                        LeadPoolEntry.rep_hold.is_(True),
                        LeadPoolEntry.entered_at <= clock - timedelta(minutes=minutes),
                    )
                    .order_by(LeadPoolEntry.entered_at, LeadPoolEntry.id)
                )
            ).scalars()
        )
    released: list[uuid.UUID] = []
    for did in ids:
        if await _release_if_expired(org_id, did, clock, minutes):
            released.append(did)
    for did in released:
        try:
            await assign_pooled(org_id, did)
        except Exception:  # noqa: BLE001 - the deal.pooled event and the 15-minute job retry it
            log.exception("assignment after hold expiry failed for %s; it will be retried", did)
    return len(released)


async def _release_if_expired(
    org_id: uuid.UUID, deal_id: uuid.UUID, clock: datetime, minutes: int
) -> bool:
    actor = system_actor(org_id, "Assignment engine")
    async with UnitOfWork(actor, RequestInfo(origin="system_job")) as uow:
        s = uow.session
        await uow.advisory_lock("assign")
        deal = (
            await s.execute(
                select(Deal).where(Deal.id == deal_id, Deal.org_id == org_id).with_for_update()
            )
        ).scalar_one_or_none()
        if deal is None or deal.deleted_at is not None or deal.status != "pooled":
            return False
        entry = await open_pool_entry(s, deal.id, lock=True)
        if (
            entry is None
            or not entry.rep_hold
            or entry.entered_at > clock - timedelta(minutes=minutes)
        ):
            return False  # decided meanwhile (or the window was lengthened): nothing to do
        await release_rep_hold(
            uow,
            deal,
            entry,
            action_key="lead.hold_expired",
            note=f"The Team Leader did not decide within {minutes} minutes: automatic assignment",
            new={"decision": "auto", "by": "timeout"},
        )
        return True


# ----------------------------------------------------------------------------- the engine
async def assign_pooled(
    org_id: uuid.UUID, deal_id: uuid.UUID, *, origin: str = "system_job"
) -> Outcome:
    actor = system_actor(org_id, "Assignment engine")
    async with UnitOfWork(actor, RequestInfo(origin=origin)) as uow:
        s = uow.session
        await uow.advisory_lock("assign")
        deal = (
            await s.execute(
                select(Deal).where(Deal.id == deal_id, Deal.org_id == org_id).with_for_update()
            )
        ).scalar_one_or_none()
        if deal is None or deal.deleted_at is not None or deal.status != "pooled":
            return Outcome("skipped")  # already assigned (or gone): running again changes nothing
        entry = await open_pool_entry(s, deal.id, lock=True)
        if entry is None or entry.manual_hold:
            return Outcome("skipped", reason="held_for_manual_assignment")

        acc = await s.get(Account, deal.account_id)
        facts = {
            "lead_source": deal.lead_source,
            "industry": acc.industry if acc else None,
            "city": acc.city if acc else None,
        }
        rule, ttype, tid = await resolve_target(s, org_id, facts)
        if rule is None and entry.entered_source == REP_ENTRY and entry.routed_team_id:
            ttype, tid = "team", entry.routed_team_id  # a rep's lead with no rule: the rep's team
        team_id = await routed_team(s, ttype, tid)
        reason: str | None = None
        pick: User | None = None
        if ttype is None or tid is None:
            reason = "no_rule_match"
        else:
            reps = await candidates(s, org_id, ttype, tid)
            if not reps:
                reason = "no_active_reps"
            else:
                pointer = await _pointer(s, org_id, ttype, tid) if ttype != "user" else None
                order = rotate(reps, pointer.last_assigned_user_id if pointer else None)
                caps = await caps_for(s, org_id, [r.id for r in order])
                loads = await active_lead_counts(s, org_id, [r.id for r in order])
                pick = next((r for r in order if loads[r.id] < caps[r.id]), None)
                if pick is None:
                    reason = "all_reps_at_cap"
                elif pointer is not None:
                    pointer.last_assigned_user_id, pointer.updated_at = pick.id, utc_now()

        if pick is not None:
            await apply_owner(
                uow,
                deal,
                pick,
                method="rule" if rule else "round_robin",
                action_key="lead.assigned",
                by=None,
                rule_id=rule.id if rule else None,
                note=f"Rule: {rule.name}" if rule else "Round-robin",
            )
            return Outcome("assigned", pick.id, rule_name=rule.name if rule else None)

        changed = entry.waiting_reason != reason or entry.routed_team_id != team_id
        entry.waiting_reason, entry.routed_team_id = reason, team_id
        deal.team_id = team_id
        if changed:
            await uow.audit(
                "lead.waiting",
                "deal",
                deal.id,
                new={"waiting_reason": reason},
                note="No eligible rep: the lead stays in the Pool",
            )
            await uow.emit(events.POOL_CHANGED, {"deal_id": str(deal.id)}, rt=pool_rt(team_id))
        if not entry.tl_notified:  # the Team Leader hears about a waiting lead once, not per retry
            entry.tl_notified = True
            await uow.emit(
                events.LEAD_WAITING,
                {
                    "deal_id": str(deal.id),
                    "team_id": str(team_id) if team_id else None,
                    "reason": reason,
                    "company": acc.name if acc else "a lead",
                },
            )
        return Outcome("waiting", reason=reason)


async def retry_waiting(org_id: uuid.UUID, factory: async_sessionmaker[AsyncSession]) -> int:
    """Try every waiting lead again, oldest first. Returns how many got an owner."""
    async with factory() as s:
        ids = list(
            (
                await s.execute(
                    select(LeadPoolEntry.deal_id)
                    .where(
                        LeadPoolEntry.org_id == org_id,
                        LeadPoolEntry.left_at.is_(None),
                        LeadPoolEntry.manual_hold.is_(False),
                    )
                    .order_by(LeadPoolEntry.entered_at, LeadPoolEntry.id)
                )
            ).scalars()
        )
    assigned = 0
    for did in ids:
        if (await assign_pooled(org_id, did)).status == "assigned":
            assigned += 1
    return assigned


# ----------------------------------------------------------------------------- settings
async def read_settings(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    await authorize_read(session, actor, "assignment.config")
    org = actor.org_id
    st = await settings_row(session, org)
    rules = list(
        (
            await session.execute(
                select(AssignmentRule)
                .where(AssignmentRule.org_id == org, AssignmentRule.deleted_at.is_(None))
                .order_by(AssignmentRule.priority, AssignmentRule.id)
            )
        ).scalars()
    )
    groups = list(
        (
            await session.execute(
                select(RepGroup)
                .where(RepGroup.org_id == org, RepGroup.deleted_at.is_(None))
                .order_by(RepGroup.name)
            )
        ).scalars()
    )
    members: dict[uuid.UUID, list[uuid.UUID]] = {g.id: [] for g in groups}
    if members:
        member_rows = await session.execute(
            select(RepGroupMember.group_id, RepGroupMember.user_id).where(
                RepGroupMember.group_id.in_(list(members))
            )
        )
        for gid, uid in member_rows:
            members[gid].append(uid)
    teams = {
        t.id: t.name
        for t in (await session.execute(select(Team).where(Team.org_id == org))).scalars()
    }
    people = {
        u.id: u for u in (await session.execute(select(User).where(User.org_id == org))).scalars()
    }
    gnames = {g.id: g.name for g in groups}

    def tname(r: AssignmentRule) -> str | None:
        if r.target_type == "team":
            return teams.get(r.target_id)
        if r.target_type == "group":
            return gnames.get(r.target_id)
        u = people.get(r.target_id)
        return u.name if u else None

    reps = sorted(
        (
            u
            for u in people.values()
            if u.role == Role.USER and u.is_active and u.deleted_at is None
        ),
        key=lambda u: u.name,
    )
    own = {
        c.user_id: c.cap
        for c in (await session.execute(select(RepCap).where(RepCap.org_id == org))).scalars()
    }
    loads = await active_lead_counts(session, org, [u.id for u in reps])
    return {
        "default_team_id": st.default_team_id,
        "default_cap": st.default_cap,
        "rep_lead_hold_minutes": st.rep_lead_hold_minutes,
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "priority": r.priority,
                "active": r.active,
                "match": r.match,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "target_name": tname(r),
            }
            for r in rules
        ],
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "team_id": g.team_id,
                "member_ids": sorted(members[g.id], key=str),
            }
            for g in groups
        ],
        "caps": [
            {
                "user_id": u.id,
                "name": u.name,
                "team_id": u.team_id,
                "cap": own.get(u.id, st.default_cap),
                "is_default": u.id not in own,
                "active_leads": loads[u.id],
            }
            for u in reps
        ],
    }


async def write_settings(actor: Actor, request: RequestInfo, body: Any) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("assignment.config")
        reason = uow.reason("assignment.config", body.reason)
        s, org = uow.session, actor.org_id
        before = await _snapshot(s, org)

        team = (
            await s.execute(
                select(Team).where(
                    Team.id == body.default_team_id, Team.org_id == org, Team.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if team is None:
            raise Conflict(
                "Choose a valid default team: leads that match no rule need somewhere to go"
            )
        st = await settings_row(s, org)
        st.default_team_id, st.default_cap, st.updated_at = team.id, body.default_cap, utc_now()
        if body.rep_lead_hold_minutes is not None:  # omitted = keep the current window
            st.rep_lead_hold_minutes = body.rep_lead_hold_minutes

        users = {
            u.id: u for u in (await s.execute(select(User).where(User.org_id == org))).scalars()
        }

        # rep groups first (rules may point at them)
        existing_groups = {
            g.id: g
            for g in (
                await s.execute(
                    select(RepGroup).where(RepGroup.org_id == org, RepGroup.deleted_at.is_(None))
                )
            ).scalars()
        }
        keep_groups: set[uuid.UUID] = set()
        org_teams = set(
            (
                await s.execute(
                    select(Team.id).where(Team.org_id == org, Team.deleted_at.is_(None))
                )
            ).scalars()
        )
        # ids that exist in ANOTHER organisation are never reused for a new row
        taken_ids = {
            i
            for i in (
                await s.execute(
                    select(RepGroup.id).where(
                        RepGroup.id.in_([g.id for g in body.groups if g.id]), RepGroup.org_id != org
                    )
                )
            ).scalars()
        }
        for g in body.groups:
            for uid in g.member_ids:
                if uid not in users or users[uid].role != Role.USER:
                    raise Conflict("Rep groups can only contain sales reps")
            if g.team_id and g.team_id not in org_teams:
                raise NotFound("Team not found")
            row = existing_groups.get(g.id) if g.id else None
            if row is None and g.id:
                old_group = await s.get(RepGroup, g.id)  # removed earlier (stale tab): revive
                if old_group is not None and old_group.org_id == org:
                    row = old_group
                    row.deleted_at = None
            if row is None:
                row = RepGroup(
                    id=uuid.uuid4() if (g.id is None or g.id in taken_ids) else g.id,
                    org_id=org,
                    name=g.name.strip(),
                    team_id=g.team_id,
                )
                s.add(row)
            else:
                row.name, row.team_id = g.name.strip(), g.team_id
            await s.flush()
            keep_groups.add(row.id)
            await s.execute(delete(RepGroupMember).where(RepGroupMember.group_id == row.id))
            for uid in dict.fromkeys(g.member_ids):
                s.add(RepGroupMember(group_id=row.id, user_id=uid))
        for gid, row in existing_groups.items():
            if gid not in keep_groups:
                row.deleted_at = utc_now()
        await s.flush()

        # rules
        existing_rules = {
            r.id: r
            for r in (
                await s.execute(
                    select(AssignmentRule).where(
                        AssignmentRule.org_id == org, AssignmentRule.deleted_at.is_(None)
                    )
                )
            ).scalars()
        }
        keep_rules: set[uuid.UUID] = set()
        for r in body.rules:
            for k in r.match:
                if k not in MATCH_KEYS:
                    raise Conflict(
                        f"Unknown rule condition '{k}'. Use one of: {', '.join(MATCH_KEYS)}"
                    )
            if r.target_type == "team" and r.target_id not in org_teams:
                raise NotFound("Rule target team not found")
            if r.target_type == "group" and r.target_id not in keep_groups:
                raise NotFound("Rule target group not found")
            if r.target_type == "user" and (
                r.target_id not in users or users[r.target_id].role != Role.USER
            ):
                raise Conflict("A rule can target one sales rep")
            rrow = existing_rules.get(r.id) if r.id else None
            if rrow is None and r.id:
                old_rule_row = await s.get(AssignmentRule, r.id)  # removed earlier: revive
                if old_rule_row is not None and old_rule_row.org_id == org:
                    rrow = old_rule_row
                    rrow.deleted_at = None
                elif old_rule_row is not None:
                    r.id = None  # another organisation's id: never touch or reuse it
            if rrow is None:
                rrow = AssignmentRule(
                    id=r.id or uuid.uuid4(),
                    org_id=org,
                    name=r.name.strip(),
                    priority=r.priority,
                    active=r.active,
                    match=r.match,
                    target_type=r.target_type,
                    target_id=r.target_id,
                )
                s.add(rrow)
            else:
                rrow.name, rrow.priority, rrow.active = r.name.strip(), r.priority, r.active
                rrow.match, rrow.target_type, rrow.target_id = r.match, r.target_type, r.target_id
                rrow.updated_at = utc_now()
            keep_rules.add(rrow.id)
        for rid, old_rule in existing_rules.items():
            if rid not in keep_rules:
                old_rule.deleted_at = utc_now()

        # caps: PUT is a full replace, so an unlisted rep goes back to the default
        for uid in body.caps:
            if uid not in users:
                raise NotFound("Rep not found")
        await s.execute(delete(RepCap).where(RepCap.org_id == org))
        for uid, cap in body.caps.items():
            s.add(RepCap(user_id=uid, org_id=org, cap=cap))
        await s.flush()

        after = await _snapshot(s, org)
        await uow.audit(
            "assignment.config",
            "assignment_settings",
            str(org),
            old=before,
            new=after,
            reason=reason,
        )
        await uow.emit(events.ASSIGNMENT_RETRY, {"why": "settings changed"})
    async with get_sessionmaker()() as s2:
        return await read_settings(s2, actor)


async def _snapshot(s: AsyncSession, org: uuid.UUID) -> dict[str, Any]:
    st = await settings_row(s, org)
    rules = (
        (
            await s.execute(
                select(AssignmentRule)
                .where(AssignmentRule.org_id == org, AssignmentRule.deleted_at.is_(None))
                .order_by(AssignmentRule.priority, AssignmentRule.id)
            )
        )
        .scalars()
        .all()
    )
    caps = (await s.execute(select(RepCap).where(RepCap.org_id == org))).scalars().all()
    groups = (
        (
            await s.execute(
                select(RepGroup).where(RepGroup.org_id == org, RepGroup.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return {
        "default_team_id": str(st.default_team_id) if st.default_team_id else None,
        "default_cap": st.default_cap,
        "rep_lead_hold_minutes": st.rep_lead_hold_minutes,
        "rules": [
            {
                "name": r.name,
                "priority": r.priority,
                "active": r.active,
                "match": r.match,
                "target": f"{r.target_type}:{r.target_id}",
            }
            for r in rules
        ],
        "groups": sorted(g.name for g in groups),
        "caps": {str(c.user_id): c.cap for c in caps},
    }


async def test_rule(
    session: AsyncSession, actor: Actor, facts: dict[str, str | None]
) -> dict[str, Any]:
    """Read-only: which rule and target would a sample lead hit? Not logged as a change."""
    await authorize_read(session, actor, "assignment.config")
    org = actor.org_id
    rule, ttype, tid = await resolve_target(session, org, facts)
    if ttype is None or tid is None:
        return {
            "matched_rule": None,
            "target_type": None,
            "target_name": None,
            "eligible_reps": [],
            "at_cap": [],
            "outcome": "No rule matches and there is no default team",
        }
    if ttype == "team":
        t = await session.get(Team, tid)
        tname = t.name if t else None
    elif ttype == "group":
        g = await session.get(RepGroup, tid)
        tname = g.name if g else None
    else:
        u = await session.get(User, tid)
        tname = u.name if u else None
    reps = await candidates(session, org, ttype, tid)
    caps = await caps_for(session, org, [r.id for r in reps])
    loads = await active_lead_counts(session, org, [r.id for r in reps])
    eligible = [f"{r.name} ({loads[r.id]}/{caps[r.id]})" for r in reps if loads[r.id] < caps[r.id]]
    full = [f"{r.name} ({loads[r.id]}/{caps[r.id]})" for r in reps if loads[r.id] >= caps[r.id]]
    if eligible:
        outcome = (
            "Would be assigned round-robin among the eligible reps"
            if ttype != "user"
            else "Would go to this rep"
        )
    elif reps:
        outcome = "Every rep is at their cap: the lead would wait in the Pool"
    else:
        outcome = "No active reps in this target: the lead would wait in the Pool"
    return {
        "matched_rule": rule.name if rule else "(default team)",
        "target_type": ttype,
        "target_name": tname,
        "eligible_reps": eligible,
        "at_cap": full,
        "outcome": outcome,
    }
