# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Permission checks for read endpoints. Out-of-scope reads answer 404 so existence never leaks."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, PermissionDenied
from app.platform.context import Actor
from app.platform.permissions import loader
from app.platform.permissions.guard import Decision, Effect, evaluate
from app.platform.permissions.roles import Scope
from app.platform.permissions.scope import in_scope


async def authorize_read(
    session: AsyncSession,
    actor: Actor,
    key: str,
    entity: tuple[str, str | uuid.UUID] | None = None,
) -> Decision:
    if actor.is_system:  # jobs and scripts act for the platform, like UnitOfWork.authorize
        return Decision(Effect.ALLOW, Scope.ALL)
    table = await loader.get_table(session)
    decision = evaluate(table, actor.role_enum, key)
    if decision.effect == Effect.DENY:
        raise PermissionDenied("You are not allowed to see this", {"key": key})
    if entity is not None and decision.scope != Scope.NONE:
        if not await in_scope(session, actor, decision.scope, entity[0], entity[1]):
            raise NotFound("Not found")
    return decision
