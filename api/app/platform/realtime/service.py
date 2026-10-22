# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Live updates: Postgres LISTEN/NOTIFY to WebSockets, filtered by each user's scope.

Flow: a service emits an outbox event carrying `rt` metadata. The dispatcher forwards it with
pg_notify on channel `relay_events` (inside its transaction, so it is sent only on commit).
Each API process listens and fans the event out to connected users whose scope includes it.
NOTIFY is only a wake-up: clients that were offline catch up through GET /events?since=<id>.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.users.models import User
from app.platform.context import Actor
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import notify_listeners, on_event
from app.platform.permissions import loader
from app.platform.permissions.roles import Role, Scope

log = logging.getLogger("relay.realtime")
CHANNEL = "relay_events"


@on_event("*")
async def forward_to_listeners(session: AsyncSession, event: OutboxEvent) -> None:
    rt = event.payload.get("rt")
    if not rt:
        return
    body = {"id": event.id, "type": event.type, "org_id": str(event.org_id), "rt": rt}
    await notify_listeners(session, CHANNEL, json.dumps(body))


def visible_to(
    actor: Actor, scope: Scope, event: dict[str, Any], team_of: dict[str, str | None]
) -> bool:
    """Is this event in `actor`'s scope? `team_of` maps owner id -> owner's current team."""
    rt = event["rt"]
    if actor.id is not None and str(actor.id) in (rt.get("user_ids") or []):
        return True
    if scope == Scope.NONE:
        return False
    if scope == Scope.ALL:
        owner_role = rt.get("owner_role")
        return not (
            owner_role == Role.SUPER_ADMIN and actor.role not in (Role.ADMIN, Role.SUPER_ADMIN)
        )
    owner = rt.get("owner_id")
    if scope == Scope.OWN:
        return owner is not None and owner == str(actor.id)
    # TEAM
    if actor.team_id is None:
        return False
    owner_team = team_of.get(owner) if owner else rt.get("team_id")
    return owner_team is not None and str(owner_team) == str(actor.team_id)


@dataclass
class Connection:
    ws: WebSocket
    actor: Actor
    scopes: dict[str, Scope] = field(default_factory=dict)
    expires_at: float | None = None  # token `exp` (epoch seconds); the socket ends then


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: list[Connection] = []

    async def add(self, ws: WebSocket, actor: Actor, expires_at: float | None = None) -> Connection:
        conn = Connection(ws, actor, expires_at=expires_at)
        self.connections.append(conn)
        return conn

    def remove(self, conn: Connection) -> None:
        if conn in self.connections:
            self.connections.remove(conn)

    async def _refresh(self, conn: Connection) -> bool:
        """Reload the user's CURRENT role/team so a move takes effect on the very next event.
        Returns False when the user is gone or deactivated (socket gets closed)."""
        async with get_sessionmaker()() as s:
            user = await s.get(User, conn.actor.id)
            if user is None or not user.is_active or user.deleted_at is not None:
                return False
            conn.actor = Actor(
                id=user.id,
                org_id=user.org_id,
                role=user.role,
                team_id=user.team_id,
                name=user.name,
                email=user.email,
            )
            table = await loader.get_table(s)
        conn.scopes = {k: r.scope for (role, k), r in table.items() if role == Role(user.role)}
        return True

    async def handle(self, event: dict[str, Any]) -> None:
        rt = event["rt"]
        key = rt.get("scope_key") or "deal.view"
        owner = rt.get("owner_id")
        async with get_sessionmaker()() as s:
            team_of: dict[str, str | None] = {}
            if owner:
                row = (
                    await s.execute(select(User.team_id).where(User.id == uuid.UUID(owner)))
                ).first()
                team_of[owner] = str(row[0]) if row and row[0] else None
            owner_role = None
            if owner:
                owner_role = (
                    await s.execute(select(User.role).where(User.id == uuid.UUID(owner)))
                ).scalar_one_or_none()
        rt = {**rt, "owner_role": owner_role}
        event = {**event, "rt": rt}
        for conn in list(self.connections):
            if str(conn.actor.org_id) != event["org_id"]:
                continue
            if conn.expires_at is not None and time.time() >= conn.expires_at:
                await self._close(conn)  # the token has expired (BUG-P1-13)
                continue
            if not await self._refresh(conn):
                await self._close(conn)
                continue
            scope = conn.scopes.get(key, Scope.NONE)
            if visible_to(conn.actor, scope, event, team_of):
                await self._send(
                    conn,
                    {
                        "id": event["id"],
                        "type": event["type"],
                        "entity_type": rt.get("entity_type"),
                        "entity_id": rt.get("entity_id"),
                    },
                )

    async def _send(self, conn: Connection, msg: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(conn.ws.send_json(msg), timeout=5)
        except Exception:  # noqa: BLE001 - slow or dead clients are dropped
            await self._close(conn)

    async def _close(self, conn: Connection) -> None:
        self.remove(conn)
        with contextlib.suppress(Exception):
            await conn.ws.close(code=4401)


manager = ConnectionManager()


def _raw_dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def listen_forever(stop: asyncio.Event) -> None:
    """Dedicated LISTEN connection (not pooled). Reconnects with backoff."""
    delay = 1.0
    while not stop.is_set():
        try:
            conn = await asyncpg.connect(_raw_dsn())
            try:
                queue: asyncio.Queue[str] = asyncio.Queue()
                await conn.add_listener(
                    CHANNEL,
                    lambda _c, _pid, _ch, payload: queue.put_nowait(payload),  # noqa: B023
                )
                delay = 1.0
                while not stop.is_set():
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except TimeoutError:
                        continue
                    with contextlib.suppress(Exception):
                        await manager.handle(json.loads(payload))
            finally:
                await asyncio.shield(conn.close())  # also when the task is cancelled
        except Exception:  # noqa: BLE001
            log.exception("realtime listener lost its connection; retrying in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


async def events_since(
    session: AsyncSession, actor: Actor, since_id: int, limit: int = 200
) -> list[dict[str, Any]]:
    """Catch-up for reconnecting clients: events they are allowed to see, after `since_id`."""
    table = await loader.get_table(session)
    out: list[dict[str, Any]] = []
    cursor = since_id
    # Scan in batches until `limit` visible events are found, so a long run of events the caller
    # may not see cannot starve it (BUG-P1-14).
    for _ in range(MAX_SCAN_BATCHES):
        batch = list(
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.org_id == actor.org_id,
                        OutboxEvent.id > cursor,
                        OutboxEvent.payload.has_key("rt"),
                    )
                    .order_by(OutboxEvent.id)
                    .limit(SCAN_BATCH)
                )
            ).scalars()
        )
        for ev in batch:
            cursor = ev.id
            visible = await _visible_event(session, actor, table, ev)
            if visible is not None:
                out.append(visible)
                if len(out) >= limit:
                    return out
        if len(batch) < SCAN_BATCH:
            break
    return out


SCAN_BATCH = 500
MAX_SCAN_BATCHES = 100


async def _visible_event(
    session: AsyncSession, actor: Actor, table: Any, ev: OutboxEvent
) -> dict[str, Any] | None:
    rt = ev.payload.get("rt")
    if not rt:
        return None
    key = rt.get("scope_key") or "deal.view"
    rule = table.get((Role(actor.role), key))
    scope = rule.scope if rule else Scope.NONE
    owner = rt.get("owner_id")
    team_of: dict[str, str | None] = {}
    owner_role = None
    if owner:
        row = (
            await session.execute(
                select(User.team_id, User.role).where(User.id == uuid.UUID(owner))
            )
        ).first()
        if row:
            team_of[owner], owner_role = (str(row[0]) if row[0] else None), row[1]
    e = {
        "id": ev.id,
        "type": ev.type,
        "org_id": str(ev.org_id),
        "rt": {**rt, "owner_role": owner_role},
    }
    if visible_to(actor, scope, e, team_of):
        return {
            "id": ev.id,
            "type": ev.type,
            "entity_type": rt.get("entity_type"),
            "entity_id": rt.get("entity_id"),
        }
    return None
