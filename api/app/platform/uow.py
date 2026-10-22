# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Unit of work: the one pipeline every state-changing service function runs inside.

    async with UnitOfWork(actor, req) as uow:
        decision = await uow.authorize("user.manage", entity=("user", str(user_id)))
        reason = uow.reason("user.manage", body.reason)
        ... change rows via uow.session ...
        await uow.audit("user.manage", "user", user_id, old=..., new=..., reason=reason)
        await uow.emit("user.role_changed", {...})

The change, its audit row and its outbox event commit together or not at all. If authorisation,
scope or the reason check fails, everything rolls back and a `denied` audit row is written in a
separate transaction (PRD 4: denied actions are logged).
"""

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.errors import OutOfScope, PermissionDenied, ReasonRequired
from app.platform.audit import service as audit_service
from app.platform.context import Actor, RequestInfo
from app.platform.outbox import service as outbox
from app.platform.permissions import loader
from app.platform.permissions.guard import Decision, Effect, evaluate
from app.platform.permissions.roles import Role, Scope
from app.platform.permissions.scope import in_scope
from app.platform.reasons import policy

log = logging.getLogger("relay.uow")

_DENIED = (PermissionDenied, OutOfScope, ReasonRequired)


class UnitOfWork:
    def __init__(
        self,
        actor: Actor,
        request: RequestInfo | None = None,
        *,
        on_behalf_of: uuid.UUID | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> None:
        self.actor = actor
        self.request = request or RequestInfo()
        self.on_behalf_of = on_behalf_of
        self.approval_id = approval_id
        self.options: dict[str, Any] = {}  # per-operation switches read by hooks
        self.session: AsyncSession = None  # type: ignore[assignment]
        self._attempt: tuple[str, str, str] | None = None  # (key, entity_type, entity_id)

    async def __aenter__(self) -> "UnitOfWork":
        self.session = get_sessionmaker()()
        await self.session.begin()
        return self

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        try:
            if exc is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
        if exc is not None and isinstance(exc, _DENIED):
            await self._log_denied(exc)

    async def _log_denied(self, exc: BaseException) -> None:
        key, etype, eid = self._attempt or ("unknown", "unknown", "-")
        try:
            async with get_sessionmaker()() as s, s.begin():
                await audit_service.append(
                    s,
                    self.actor,
                    key,
                    etype,
                    eid,
                    outcome="denied",
                    note=f"{type(exc).__name__}: {exc}",
                    request=self.request,
                )
        except Exception:  # noqa: BLE001 - never mask the original denial
            log.exception("could not write denied audit row")

    async def advisory_lock(self, name: str) -> None:
        """Per-org transaction lock, so rare admin operations that touch overlapping rows
        (for example last-Super-admin checks) run one at a time instead of deadlocking."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"{name}:{self.actor.org_id}"},
        )

    # -- authorisation -------------------------------------------------------------------
    async def authorize(
        self,
        key: str,
        *,
        entity: tuple[str, str | uuid.UUID] | None = None,
        owner_id: uuid.UUID | None = None,
        check_scope: bool = True,
    ) -> Decision:
        """Permission check for the actor's role, plus the scope check on `entity`.

        `owner_id` (if given) says who owns the record, for own_deals_only / not_own_deals rules.
        The system actor skips the matrix (jobs run on behalf of rules, not users).
        """
        etype, eid = (entity[0], str(entity[1])) if entity else ("-", "-")
        self._attempt = (key, etype, eid)
        if self.actor.is_system:
            return Decision(Effect.ALLOW, Scope.ALL)
        table = await loader.get_table(self.session)
        is_own = None if owner_id is None else owner_id == self.actor.id
        decision = evaluate(table, self.actor.role_enum, key, is_own_record=is_own)
        if decision.effect == Effect.DENY:
            raise PermissionDenied("You are not allowed to do this", {"key": key})
        if check_scope and entity is not None and decision.scope != Scope.NONE:
            if not await in_scope(self.session, self.actor, decision.scope, entity[0], entity[1]):
                raise OutOfScope("This record is outside your scope", {"key": key})
        return decision

    def reason(self, key: str, reason: str | None) -> str | None:
        """Apply the reason policy for `key`; returns the cleaned reason to store."""
        if self.actor.is_system:
            return policy.normalise_reason(reason)
        return policy.check(self.actor.role_enum, loader.action_class(key), reason, key)

    # -- writes --------------------------------------------------------------------------
    async def audit(
        self,
        key: str,
        entity_type: str,
        entity_id: str | uuid.UUID,
        *,
        old: dict[str, Any] | None = None,
        new: dict[str, Any] | None = None,
        reason: str | None = None,
        outcome: str = "ok",
        note: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Write an audit row; returns its id (Undo refers to it)."""
        row = await audit_service.append(
            self.session,
            self.actor,
            key,
            entity_type,
            entity_id,
            outcome=outcome,
            old=old,
            new=new,
            reason=reason,
            note=note,
            request=self.request,
            on_behalf_of=self.on_behalf_of,
            approval_id=approval_id or self.approval_id,
        )
        return row.id

    async def emit(
        self,
        type_: str,
        payload: dict[str, Any] | None = None,
        *,
        rt: dict[str, Any] | None = None,
    ) -> None:
        """Queue an outbox event. `rt` makes it a live update: {entity_type, entity_id,
        owner_id, team_id, scope_key, user_ids}. It is scope-filtered per connected user."""
        body = dict(payload or {})
        if rt is not None:
            body["rt"] = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in rt.items()}
        await outbox.enqueue(self.session, self.actor.org_id, type_, body)


def is_admin_role(role: str) -> bool:
    return role in (Role.ADMIN, Role.SUPER_ADMIN)
