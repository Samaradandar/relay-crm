# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for the usage feed: unmatched queue, manual sync, sync health. Phase 4."""

from typing import Any

from fastapi import APIRouter

from app.core.db import get_sessionmaker
from app.modules.usage_feed import client
from app.modules.usage_feed.models import UnmatchedUsage, UsageSyncState
from app.modules.usage_feed.schemas import SyncHealthOut, SyncResultOut, UnmatchedOut
from app.modules.usage_feed.service import run_sync
from app.platform.deps import ActorDep
from app.platform.permissions.roles import Role
from app.platform.reads import authorize_read

router = APIRouter(tags=["usage"])
VIEW_KEY = "customer.view"  # unmatched usage and sync health follow revenue-visibility scope


def _require_admin(actor: Any) -> None:
    from app.core.errors import PermissionDenied

    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN):
        raise PermissionDenied("Only an Admin or Super admin may do this")


@router.get("/usage/unmatched", response_model=list[UnmatchedOut])
async def unmatched(actor: ActorDep) -> list[Any]:
    async with get_sessionmaker()() as s:
        await authorize_read(s, actor, VIEW_KEY)
        _require_admin(actor)
        from sqlalchemy import select

        rows = await s.execute(
            select(UnmatchedUsage)
            .where(UnmatchedUsage.org_id == actor.org_id, UnmatchedUsage.resolved_at.is_(None))
            .order_by(UnmatchedUsage.received_at)
        )
        return list(rows.scalars())


@router.get("/usage/health", response_model=SyncHealthOut)
async def health(actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        await authorize_read(s, actor, VIEW_KEY)
        state = await s.get(UsageSyncState, actor.org_id)
        if state is None:
            return {
                "cursor": None,
                "last_success_at": None,
                "last_attempt_at": None,
                "last_error": None,
                "consecutive_failures": 0,
            }
        return {
            "cursor": state.cursor,
            "last_success_at": state.last_success_at,
            "last_attempt_at": state.last_attempt_at,
            "last_error": state.last_error,
            "consecutive_failures": state.consecutive_failures,
        }


@router.post("/usage/sync", response_model=SyncResultOut)
async def sync_now(actor: ActorDep) -> dict[str, Any]:
    _require_admin(actor)
    return await run_sync(actor.org_id, get_sessionmaker(), client.fetch_page)
