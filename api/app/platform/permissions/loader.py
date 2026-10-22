# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Loads the permission matrix from the database (it is data, PRD 4.8), cached briefly."""

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.permissions.guard import PermissionTable
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import Rule
from app.platform.permissions.models import RolePermissionRow
from app.platform.permissions.roles import ActionClass, Mode, Role, Scope

_TTL_SECONDS = 30.0
_cache: tuple[float, dict[tuple[Role, str], Rule]] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


async def get_table(session: AsyncSession) -> PermissionTable:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL_SECONDS:
        return _cache[1]
    rows = (await session.execute(select(RolePermissionRow))).scalars().all()
    table = {
        (Role(r.role), r.key): Rule(
            Scope(r.scope), Mode(r.mode), r.own_deals_only, r.not_own_deals, r.routed_to
        )
        for r in rows
    }
    _cache = (now, table)
    return table


def action_class(key: str) -> ActionClass:
    return KEYS[key].action_class
