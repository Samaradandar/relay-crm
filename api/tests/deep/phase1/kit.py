# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Small helpers for the Phase 1 deep tests (copied or written here; shared test code is not edited)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.platform.context import Actor
from app.platform.permissions.roles import Role
from tests.helpers import headers

ROLES = ("user", "team_leader", "admin", "super_admin")


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, int] = {}

    def presign_put(self, key: str, content_type: str) -> str:
        return f"https://files.test/put/{key}?ct={content_type}&exp=600"

    def presign_get(self, key: str, filename: str) -> str:
        return f"https://files.test/get/{key}?exp=300"

    async def size_of(self, key: str) -> int | None:
        return self.objects.get(key)

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = len(data)


def token_for(
    email: str,
    *,
    secret: str | None = None,
    hours: float = 1,
    extra: dict[str, Any] | None = None,
    sub: str | None = None,
    alg: str = "HS256",
) -> str:
    """Hand-made dev token (so tests can forge, expire or tamper with claims)."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": sub or f"dev:{email.lower()}",
        "email": email.lower(),
        "iat": now,
        "exp": now + timedelta(hours=hours),
        **(extra or {}),
    }
    return jwt.encode(claims, secret or get_settings().dev_jwt_secret, algorithm=alg)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def h(actor: Actor) -> dict[str, str]:
    return headers(actor)


def actor_by_role(world: Any, role: str, team: str = "north") -> Actor:
    return {
        "user": world.north1 if team == "north" else world.south1,
        "team_leader": world.tl_north if team == "north" else world.tl_south,
        "admin": world.admin,
        "super_admin": world.sa,
    }[role]


def new_email(prefix: str = "qa") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@example.com"


async def audit_rows(org_id: uuid.UUID, **where: Any) -> list[dict[str, Any]]:
    """Audit rows of an org (optionally filtered by column=value), oldest first."""
    clauses = ["org_id = :org"] + [f"{k} = :{k}" for k in where]
    sql = f"SELECT * FROM audit_log WHERE {' AND '.join(clauses)} ORDER BY seq"
    async with get_sessionmaker()() as s:
        res = await s.execute(text(sql), {"org": org_id, **where})
        return [dict(r) for r in res.mappings()]


async def audit_count(org_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as s:
        return int(
            (
                await s.execute(
                    text("SELECT count(*) FROM audit_log WHERE org_id=:o"), {"o": org_id}
                )
            ).scalar_one()
        )


async def count_where(table: str, org_id: uuid.UUID, where: str = "true") -> int:
    async with get_sessionmaker()() as s:
        return int(
            (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE org_id=:o AND ({where})"),  # noqa: S608
                    {"o": org_id},
                )
            ).scalar_one()
        )


def role_of(actor: Actor) -> Role:
    return Role(actor.role)
