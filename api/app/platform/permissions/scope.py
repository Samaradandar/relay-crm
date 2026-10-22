# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Own / Team / All filter added to every query, search, export and socket.

One reusable filter for any model with `org_id` and an owner column. Every list, search, export
and WebSocket fan-out goes through here so nobody sees rows outside their scope.

Rules (PRD 4.3):
  Own  = records owned by the caller.
  Team = records owned by any member of the caller's team (including the TL's own).
  All  = every record in the org.
  Records owned by a Super admin are visible only to Admins and Super admins.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.users.models import User
from app.platform.context import Actor
from app.platform.permissions.roles import Role, Scope


@dataclass(frozen=True)
class ScopedEntity:
    model: Any
    owner_attr: str = "owner_id"
    id_attr: str = "id"
    hide_sa_owned: bool = True
    # Records that carry their own team (deals: routed team while pooled, owner's team after)
    # use it for Team scope instead of 'owner is in my team'.
    team_attr: str | None = None


# entity_type -> how to scope it. Modules register their entities at import time.
ENTITIES: dict[str, ScopedEntity] = {}


def register_entity(name: str, entity: ScopedEntity) -> None:
    ENTITIES[name] = entity


def scope_conditions(
    actor: Actor,
    model: Any,
    scope: Scope,
    *,
    owner_attr: str = "owner_id",
    hide_sa_owned: bool = True,
    team_attr: str | None = None,
) -> list[ColumnElement[bool]]:
    """WHERE conditions restricting `model` to what `actor` may see at `scope`."""
    conds: list[ColumnElement[bool]] = [model.org_id == actor.org_id]
    owner = getattr(model, owner_attr)
    if scope == Scope.NONE:
        return [*conds, false()]
    if scope == Scope.OWN:
        conds.append(owner == actor.id)
    elif scope == Scope.TEAM:
        if actor.team_id is None:
            return [*conds, false()]
        if team_attr:
            conds.append(getattr(model, team_attr) == actor.team_id)
        else:
            member = aliased(User)  # aliased so SQLAlchemy never auto-correlates it away
            team_members = select(member.id).where(
                member.org_id == actor.org_id, member.team_id == actor.team_id
            )
            conds.append(owner.in_(team_members))
    # Scope.ALL: org filter only
    if hide_sa_owned and actor.role not in (Role.ADMIN, Role.SUPER_ADMIN):
        sa = aliased(User)
        sa_ids = select(sa.id).where(sa.org_id == actor.org_id, sa.role == Role.SUPER_ADMIN)
        conds.append(or_(owner.is_(None), owner.not_in(sa_ids)))
    return conds


def select_scoped(
    actor: Actor,
    model: Any,
    scope: Scope,
    *,
    owner_attr: str = "owner_id",
    hide_sa_owned: bool = True,
    team_attr: str | None = None,
) -> Select[Any]:
    return select(model).where(
        and_(
            *scope_conditions(
                actor,
                model,
                scope,
                owner_attr=owner_attr,
                hide_sa_owned=hide_sa_owned,
                team_attr=team_attr,
            )
        )
    )


async def in_scope(
    session: AsyncSession,
    actor: Actor,
    scope: Scope,
    entity_type: str,
    entity_id: str | uuid.UUID,
) -> bool:
    """Is this one record inside the actor's scope? Unknown entity types are never in scope."""
    ent = ENTITIES.get(entity_type)
    if ent is None:
        return False
    try:
        rid: Any = entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id))
    except ValueError:
        return False
    model = ent.model
    stmt = (
        select(getattr(model, ent.id_attr))
        .where(
            getattr(model, ent.id_attr) == rid,
            *scope_conditions(
                actor,
                model,
                scope,
                owner_attr=ent.owner_attr,
                hide_sa_owned=ent.hide_sa_owned,
                team_attr=ent.team_attr,
            ),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None
