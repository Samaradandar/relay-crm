# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Users and teams: the only place their writes happen.

Rules (PRD 4.3, 4.7): a team has exactly one Team Leader; a User is in exactly one team and
cannot log in without one; only a Super admin manages Admins and Super admins; the last active
Super admin can never be deactivated or demoted.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, OutOfScope, PermissionDenied
from app.core.security import get_auth_provider
from app.core.time import utc_now
from app.modules.users import events
from app.modules.users.models import Team, TeamMembership, User
from app.platform.approvals import service as approvals
from app.platform.approvals.models import ApprovalRequest
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.guard import Decision, Effect
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL
from app.platform.permissions.roles import Role
from app.platform.uow import UnitOfWork

PRIVILEGED = (Role.ADMIN, Role.SUPER_ADMIN)
TEAM_ROLES = (Role.USER, Role.TEAM_LEADER)

# Phase 2 plugs in here: "their open deals move with them unless reassigned".
TeamMoveHook = Callable[[UnitOfWork, User, uuid.UUID | None, uuid.UUID | None], Awaitable[None]]
TEAM_MOVE_HOOKS: list[TeamMoveHook] = []


@dataclass
class Outcome:
    """Either the change was made (`user`/`team`) or it became an approval request."""

    user: User | None = None
    team: Team | None = None
    approval: ApprovalRequest | None = None


def _snap(u: User) -> dict[str, Any]:
    return {
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "team_id": str(u.team_id) if u.team_id else None,
        "is_active": u.is_active,
    }


def _rt(u: User) -> dict[str, Any]:
    return {
        "entity_type": "user",
        "entity_id": str(u.id),
        "owner_id": u.id,
        "team_id": u.team_id,
        "scope_key": "audit.view",
        "user_ids": [str(u.id)],
    }


def _must_act_directly(decision: Decision, key: str) -> None:
    """A request-only role never performs the action itself (PRD 4.4 L)."""
    if decision.effect in (Effect.REQUEST_ONLY, Effect.NEEDS_APPROVAL):
        raise PermissionDenied("This needs a request to an Admin", {"key": key})


async def _load_user(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False
) -> User:
    stmt = select(User).where(User.id == user_id, User.org_id == org_id, User.deleted_at.is_(None))
    if lock:
        stmt = stmt.with_for_update()
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise NotFound("User not found")
    return user


async def _load_team(session: AsyncSession, org_id: uuid.UUID, team_id: uuid.UUID) -> Team:
    team = (
        await session.execute(
            select(Team).where(Team.id == team_id, Team.org_id == org_id, Team.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if team is None:
        raise NotFound("Team not found")
    return team


async def _active_super_admins(session: AsyncSession, org_id: uuid.UUID) -> list[User]:
    """Locks every active Super admin row so concurrent demotions serialise (last-SA rule)."""
    return list(
        (
            await session.execute(
                select(User)
                .where(
                    User.org_id == org_id,
                    User.role == Role.SUPER_ADMIN,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
                .order_by(User.id)
                .with_for_update()
            )
        ).scalars()
    )


# ----------------------------------------------------------------------------------- reads
async def list_users(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    """Admins and Super admins see everyone; Team Leaders and Users see their own team."""
    stmt = (
        select(User, Team.name)
        .outerjoin(Team, Team.id == User.team_id)
        .where(User.org_id == actor.org_id, User.deleted_at.is_(None))
    )
    if actor.role not in PRIVILEGED:
        if actor.team_id is None:
            return []
        stmt = stmt.where(User.team_id == actor.team_id)
    rows = (await session.execute(stmt.order_by(User.name))).all()
    return [{**_snap(u), "id": u.id, "team_name": tname} for u, tname in rows]


async def get_user(session: AsyncSession, actor: Actor, user_id: uuid.UUID) -> dict[str, Any]:
    for u in await list_users(session, actor):
        if u["id"] == user_id:
            return u
    raise NotFound("User not found")


async def list_teams(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    stmt = select(Team).where(Team.org_id == actor.org_id, Team.deleted_at.is_(None))
    # A Team Leader sees every team's name (to give a deal to another team, BUG-P2-40) but the
    # leader and head-count of their own team only.
    if actor.role not in PRIVILEGED and actor.role != Role.TEAM_LEADER:
        if actor.team_id is None:
            return []
        stmt = stmt.where(Team.id == actor.team_id)
    teams = (await session.execute(stmt.order_by(Team.name))).scalars().all()
    out = []
    for t in teams:
        if actor.role == Role.TEAM_LEADER and t.id != actor.team_id:
            out.append(
                {
                    "id": t.id,
                    "name": t.name,
                    "leader_id": None,
                    "leader_name": None,
                    "member_count": 0,
                }
            )
            continue
        leader = await session.get(User, t.leader_id) if t.leader_id else None
        count = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.team_id == t.id, User.deleted_at.is_(None), User.is_active.is_(True))
            )
        ).scalar_one()
        out.append(
            {
                "id": t.id,
                "name": t.name,
                "leader_id": t.leader_id,
                "leader_name": leader.name if leader else None,
                "member_count": count,
            }
        )
    return out


# ----------------------------------------------------------------------------------- create
async def _apply_create(uow: UnitOfWork, p: dict[str, Any], reason: str | None) -> User:
    s, actor = uow.session, uow.actor
    role = Role(p["role"])
    email = str(p["email"]).strip().lower()
    team_id = uuid.UUID(p["team_id"]) if p.get("team_id") else None
    if (
        await s.execute(
            select(User.id).where(
                User.org_id == actor.org_id,
                func.lower(User.email) == email,
                User.deleted_at.is_(None),
            )
        )
    ).first():
        raise Conflict("A user with this email already exists")
    if role in PRIVILEGED:
        team_id = None  # Admins and Super admins are not on a team
    elif role == Role.TEAM_LEADER and team_id is None:
        raise Conflict("A Team Leader needs a team")
    team = await _load_team(s, actor.org_id, team_id) if team_id else None
    if role == Role.TEAM_LEADER and team and team.leader_id is not None:
        raise Conflict("This team already has a Team Leader")
    user = User(
        org_id=actor.org_id,
        email=email,
        name=p["name"].strip(),
        role=role.value,
        team_id=team_id,
        created_by=actor.id,
    )
    s.add(user)
    await s.flush()
    if team:
        s.add(
            TeamMembership(org_id=actor.org_id, user_id=user.id, team_id=team.id, moved_by=actor.id)
        )
        if role == Role.TEAM_LEADER:
            team.leader_id = user.id
    await uow.audit("user.manage", "user", user.id, new=_snap(user), reason=reason)
    await uow.emit(events.USER_CREATED, {"user_id": str(user.id)}, rt=_rt(user))
    await uow.emit(events.USER_INVITED, {"user_id": str(user.id)})
    return user


async def create_user(
    actor: Actor,
    request: RequestInfo,
    *,
    name: str,
    email: str,
    role: str,
    team_id: uuid.UUID | None,
    reason: str | None,
) -> Outcome:
    payload = {
        "name": name,
        "email": email,
        "role": role,
        "team_id": str(team_id) if team_id else None,
    }
    async with UnitOfWork(actor, request) as uow:
        # serialize against concurrent creates/updates (update_user takes the same lock) so the
        # email-uniqueness check below can't race past another create for the same address
        await uow.advisory_lock("user-admin")
        key = "admin.manage" if Role(role) in PRIVILEGED else "user.manage"
        decision = await uow.authorize(key)
        if decision.effect in (Effect.REQUEST_ONLY, Effect.NEEDS_APPROVAL):
            if Role(role) == Role.TEAM_LEADER:
                raise PermissionDenied("Only an Admin or Super admin can appoint a Team Leader")
            if team_id != actor.team_id:
                raise OutOfScope("Team Leaders can only request users for their own team")
            if (
                await uow.session.execute(
                    select(User.id).where(
                        User.org_id == actor.org_id,
                        func.lower(User.email) == email.lower(),
                        User.deleted_at.is_(None),
                    )
                )
            ).first():
                raise Conflict("A user with this email already exists")
            req = await approvals.create_request(
                uow,
                type_="user.create",
                action_key="user.manage",
                summary=f"{actor.name} asks to add {name} ({email}) as {role.replace('_', ' ')}",
                payload=payload,
                reason=reason,
                routed_to=ROUTE_ADMIN_POOL,
                team_id=actor.team_id,
            )
            return Outcome(approval=req)
        user = await _apply_create(uow, payload, uow.reason(key, reason))
        return Outcome(user=user)


async def _exec_user_create(uow: UnitOfWork, req: ApprovalRequest) -> None:
    await _apply_create(uow, req.payload, req.request_reason)


# ----------------------------------------------------------------------------------- update
async def _apply_role_change(
    uow: UnitOfWork,
    user: User,
    new_role: Role,
    reason: str | None,
    join_team: Team | None = None,
) -> None:
    s, actor = uow.session, uow.actor
    old = Role(user.role)
    if old == new_role:
        return
    if Role.SUPER_ADMIN == old:
        sas = await _active_super_admins(s, actor.org_id)
        if user.is_active and len(sas) <= 1:
            raise Conflict("The last active Super admin cannot be demoted")
    before = _snap(user)
    if join_team is not None and user.team_id is None and new_role in TEAM_ROLES:
        # Admin / Super admin becoming a rep or leader must land in a team (PRD 4.3).
        s.add(
            TeamMembership(
                org_id=actor.org_id,
                user_id=user.id,
                team_id=join_team.id,
                moved_by=actor.id,
                reason=reason,
            )
        )
        user.team_id = join_team.id
    team = await s.get(Team, user.team_id) if user.team_id else None
    if old == Role.TEAM_LEADER and team and team.leader_id == user.id:
        team.leader_id = None
    if new_role in PRIVILEGED:
        if user.team_id is not None:
            await _end_membership(s, user.id, actor.id, reason)
            user.team_id = None
    elif new_role == Role.TEAM_LEADER:
        if team is None:
            raise Conflict("Put the user in a team before making them its Team Leader")
        if team.leader_id is not None:
            raise Conflict("This team already has a Team Leader")
        team.leader_id = user.id
    user.role, user.updated_at = new_role.value, utc_now()
    await uow.audit("user.manage", "user", user.id, old=before, new=_snap(user), reason=reason)
    await uow.emit(
        events.USER_ROLE_CHANGED,
        {"user_id": str(user.id), "text": f"Your role is now {new_role.value.replace('_', ' ')}."},
        rt=_rt(user),
    )


async def _end_membership(
    s: AsyncSession, user_id: uuid.UUID, by: uuid.UUID | None, reason: str | None
) -> None:
    await s.execute(
        update(TeamMembership)
        .where(TeamMembership.user_id == user_id, TeamMembership.ended_at.is_(None))
        .values(ended_at=utc_now(), moved_by=by, reason=reason)
    )


async def _apply_team_move(
    uow: UnitOfWork, user: User, new_team_id: uuid.UUID, reason: str | None
) -> None:
    s, actor = uow.session, uow.actor
    if user.role in PRIVILEGED:
        raise Conflict("Admins and Super admins are not on a team")
    if user.role == Role.TEAM_LEADER:
        raise Conflict("Change the Team Leader's role or appoint a new leader before moving them")
    new_team = await _load_team(s, actor.org_id, new_team_id)
    old_team_id = user.team_id
    if old_team_id == new_team.id:
        return
    before = _snap(user)
    await _end_membership(s, user.id, actor.id, reason)
    s.add(
        TeamMembership(
            org_id=actor.org_id,
            user_id=user.id,
            team_id=new_team.id,
            moved_by=actor.id,
            reason=reason,
        )
    )
    user.team_id, user.updated_at = new_team.id, utc_now()
    await s.flush()
    await uow.audit("team.manage", "user", user.id, old=before, new=_snap(user), reason=reason)
    for hook in TEAM_MOVE_HOOKS:
        await hook(uow, user, old_team_id, new_team.id)
    await uow.emit(
        events.USER_TEAM_CHANGED,
        {"user_id": str(user.id), "text": f"You were moved to {new_team.name}."},
        rt=_rt(user),
    )


async def _apply_active(uow: UnitOfWork, user: User, active: bool, reason: str | None) -> None:
    s, actor = uow.session, uow.actor
    if user.is_active == active:
        return
    if not active and user.role == Role.SUPER_ADMIN:
        sas = await _active_super_admins(s, actor.org_id)
        if len(sas) <= 1:
            raise Conflict("The last active Super admin cannot be deactivated")
    before = _snap(user)
    if not active and user.role == Role.TEAM_LEADER and user.team_id:
        team = await s.get(Team, user.team_id)
        if team and team.leader_id == user.id:
            team.leader_id = None  # an Admin appoints the new Team Leader
    user.is_active, user.updated_at = active, utc_now()
    await uow.audit("user.manage", "user", user.id, old=before, new=_snap(user), reason=reason)
    await uow.emit(
        events.USER_DEACTIVATED if not active else events.USER_REACTIVATED,
        {"user_id": str(user.id)},
        rt=_rt(user),
    )


async def update_user(
    actor: Actor,
    request: RequestInfo,
    user_id: uuid.UUID,
    *,
    name: str | None = None,
    role: str | None = None,
    team_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    reason: str | None = None,
    fields_set: set[str] | None = None,
    open_deals: str = "move",
) -> Outcome:
    sent = fields_set or set()
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        uow.options["open_deals"] = open_deals
        await uow.advisory_lock("user-admin")
        user = await _load_user(s, actor.org_id, user_id, lock=True)
        entity = ("user", user.id)
        target_privileged = Role(user.role) in PRIVILEGED
        manage_key = "admin.manage" if target_privileged else "user.manage"
        approval: ApprovalRequest | None = None
        made_change = False

        if role is not None and role != user.role:
            key = (
                "admin.manage" if (target_privileged or Role(role) in PRIVILEGED) else "user.manage"
            )
            decision = await uow.authorize(key, entity=entity)
            join_team: Team | None = None
            if (
                decision.effect not in (Effect.REQUEST_ONLY, Effect.NEEDS_APPROVAL)
                and Role(role) in TEAM_ROLES
                and user.team_id is None
            ):
                if team_id is None:
                    raise Conflict("Choose a team for this person in the same change")
                join_team = await _load_team(s, actor.org_id, team_id)
            if decision.effect in (Effect.REQUEST_ONLY, Effect.NEEDS_APPROVAL):
                if Role(role) not in TEAM_ROLES or Role(role) == Role.TEAM_LEADER:
                    raise PermissionDenied("Only an Admin or Super admin can set that role")
                approval = await approvals.create_request(
                    uow,
                    type_="user.change_role",
                    action_key="user.manage",
                    entity=("user", str(user.id)),
                    summary=f"{actor.name} asks to change {user.name} from "
                    f"{user.role.replace('_', ' ')} to {role.replace('_', ' ')}",
                    payload={"user_id": str(user.id), "role": role},
                    reason=reason,
                    routed_to=ROUTE_ADMIN_POOL,
                    team_id=actor.team_id,
                )
            else:
                await _apply_role_change(uow, user, Role(role), uow.reason(key, reason), join_team)
                made_change = True
        if is_active is not None and is_active != user.is_active:
            decision = await uow.authorize(manage_key, entity=entity)
            if decision.effect in (Effect.REQUEST_ONLY, Effect.NEEDS_APPROVAL):
                if is_active or target_privileged:
                    raise PermissionDenied("Only an Admin can do that", {"key": manage_key})
                approval = approval or await approvals.create_request(
                    uow,
                    type_="user.deactivate",
                    action_key="user.manage",
                    entity=("user", str(user.id)),
                    summary=f"{actor.name} asks to deactivate {user.name}",
                    payload={"user_id": str(user.id)},
                    reason=reason,
                    routed_to=ROUTE_ADMIN_POOL,
                    team_id=actor.team_id,
                )
            else:
                await _apply_active(uow, user, is_active, uow.reason(manage_key, reason))
                made_change = True
        if "team_id" in sent and team_id != user.team_id:
            _must_act_directly(await uow.authorize("team.manage", entity=entity), "team.manage")
            if team_id is None:
                raise Conflict("A user must belong to a team")
            await _apply_team_move(uow, user, team_id, uow.reason("team.manage", reason))
            made_change = True
        if name is not None and name != user.name:
            _must_act_directly(await uow.authorize(manage_key, entity=entity), manage_key)
            reason_clean = uow.reason(manage_key, reason)
            before = _snap(user)
            user.name, user.updated_at = name.strip(), utc_now()
            await uow.audit(
                manage_key, "user", user.id, old=before, new=_snap(user), reason=reason_clean
            )
            await uow.emit("user.updated", {"user_id": str(user.id)}, rt=_rt(user))
            made_change = True
        if approval is not None and not made_change:
            return Outcome(approval=approval)
        return Outcome(user=user)


async def _exec_change_role(uow: UnitOfWork, req: ApprovalRequest) -> None:
    await uow.advisory_lock("user-admin")
    user = await _load_user(
        uow.session, uow.actor.org_id, uuid.UUID(req.payload["user_id"]), lock=True
    )
    new_role = Role(req.payload["role"])
    if new_role not in TEAM_ROLES or Role(user.role) not in TEAM_ROLES:
        raise Conflict("This request can no longer be applied")
    await _apply_role_change(uow, user, new_role, req.request_reason)


async def _exec_deactivate(uow: UnitOfWork, req: ApprovalRequest) -> None:
    await uow.advisory_lock("user-admin")
    user = await _load_user(
        uow.session, uow.actor.org_id, uuid.UUID(req.payload["user_id"]), lock=True
    )
    if Role(user.role) not in TEAM_ROLES:
        raise Conflict("This request can no longer be applied")
    await _apply_active(uow, user, False, req.request_reason)


# ----------------------------------------------------------------------------------- password
async def reset_password(
    actor: Actor, request: RequestInfo, user_id: uuid.UUID, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        user = await _load_user(uow.session, actor.org_id, user_id)
        provider = get_auth_provider()
        if user.id == actor.id:
            await uow.authorize("password.reset_own", entity=("user", user.id), check_scope=False)
            cleaned = uow.reason("password.reset_own", reason)
            await uow.audit("password.reset_own", "user", user.id, reason=cleaned)
            await provider.start_password_reset(user.email)
            return
        await uow.authorize("password.reset_others", entity=("user", user.id))
        cleaned = uow.reason("password.reset_others", reason)
        await uow.audit("password.reset_others", "user", user.id, reason=cleaned)
        await provider.admin_reset_password(user.email)


# ----------------------------------------------------------------------------------- teams
async def create_team(actor: Actor, request: RequestInfo, name: str, reason: str | None) -> Team:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("team.manage")
        cleaned = uow.reason("team.manage", reason)
        exists = (
            await uow.session.execute(
                select(Team.id).where(
                    Team.org_id == actor.org_id,
                    func.lower(Team.name) == name.strip().lower(),
                    Team.deleted_at.is_(None),
                )
            )
        ).first()
        if exists:
            raise Conflict("A team with this name already exists")
        team = Team(org_id=actor.org_id, name=name.strip(), created_by=actor.id)
        uow.session.add(team)
        await uow.session.flush()
        await uow.audit("team.manage", "team", team.id, new={"name": team.name}, reason=cleaned)
        await uow.emit(events.TEAM_CREATED, {"team_id": str(team.id)})
        return team


async def rename_team(
    actor: Actor, request: RequestInfo, team_id: uuid.UUID, name: str, reason: str | None
) -> Team:
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize("team.manage")
        cleaned = uow.reason("team.manage", reason)
        team = await _load_team(uow.session, actor.org_id, team_id)
        clash = (
            await uow.session.execute(
                select(Team.id).where(
                    Team.org_id == actor.org_id,
                    func.lower(Team.name) == name.strip().lower(),
                    Team.deleted_at.is_(None),
                    Team.id != team.id,
                )
            )
        ).first()
        if clash:
            raise Conflict("A team with this name already exists")
        old = {"name": team.name}
        team.name, team.updated_at = name.strip(), utc_now()
        await uow.audit(
            "team.manage", "team", team.id, old=old, new={"name": team.name}, reason=cleaned
        )
        return team


def register() -> None:
    approvals.register_type("user.create", _exec_user_create)
    approvals.register_type("user.change_role", _exec_change_role)
    approvals.register_type("user.deactivate", _exec_deactivate)
