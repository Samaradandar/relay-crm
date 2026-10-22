# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for Phase 8 (side track): connect/disconnect (ST-01), the push webhook and
calendar suggestions (ST-03), Gmail search/log/refresh (ST-04), and Admin sync health (ST-05).

Built in: Phase 8 (side track).
"""

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, Response
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.modules.google import gmail, service
from app.modules.google.incremental_sync import (
    ignore_suggestion,
    link_suggestion,
    run_incremental_sync,
)
from app.modules.google.models import CalendarSuggestion, CalendarSyncState
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["google"])


@router.get("/integrations/google/connect")
async def connect(actor: ActorDep) -> dict[str, str]:
    url, _verifier = service.start_connect(actor.id)  # type: ignore[arg-type]
    return {"url": url}


@router.get("/integrations/google/callback")
async def callback(code: str, state: str, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    account = await service.handle_callback(actor, req, code, state)
    return {"google_email": account.google_email, "status": account.status}


@router.delete("/integrations/google")
async def disconnect(actor: ActorDep, req: RequestDep) -> dict[str, bool]:
    await service.disconnect(actor, req)
    return {"ok": True}


@router.get("/me/integrations")
async def my_integrations(actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        google = await service.status(s, actor)
        return {"google": google}


# ------------------------------------------------------------------------- ST-03: push webhook
@router.post("/integrations/google/calendar/push", status_code=200)
async def calendar_push(
    background: BackgroundTasks,
    response: Response,
    x_goog_channel_id: str | None = Header(None),
    x_goog_channel_token: str | None = Header(None),
    x_goog_resource_id: str | None = Header(None),
) -> dict[str, bool]:
    """Validates the channel id/token/resource id; anything else gets 404 (section 5). Responds
    immediately and runs the actual sync afterwards (FastAPI BackgroundTasks) to stay well
    under Google's expected turnaround."""
    if not (x_goog_channel_id and x_goog_channel_token and x_goog_resource_id):
        response.status_code = 404
        return {"ok": False}
    async with get_sessionmaker()() as s:
        state = (
            await s.execute(
                select(CalendarSyncState).where(CalendarSyncState.channel_id == x_goog_channel_id)
            )
        ).scalar_one_or_none()
        if (
            state is None
            or state.channel_token != x_goog_channel_token
            or state.resource_id != x_goog_resource_id
        ):
            response.status_code = 404
            return {"ok": False}
        user_id, org_id = state.user_id, state.org_id

    background.add_task(_run_sync_task, user_id, org_id)
    return {"ok": True}


async def _run_sync_task(user_id: uuid.UUID, org_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await run_incremental_sync(s, user_id, org_id)


@router.get("/me/calendar/suggestions")
async def my_suggestions(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(CalendarSuggestion).where(
                    CalendarSuggestion.user_id == actor.id, CalendarSuggestion.status == "open"
                )
            )
        ).scalars().all()  # fmt: skip
        return [
            {
                "id": r.id,
                "summary": r.summary,
                "starts_at": r.starts_at,
                "contact_email": r.contact_email,
                "google_event_id": r.google_event_id,
            }  # fmt: skip
            for r in rows
        ]


@router.post("/me/calendar/suggestions/{suggestion_id}/link")
async def link_my_suggestion(
    suggestion_id: uuid.UUID, deal_id: uuid.UUID, actor: ActorDep
) -> dict[str, bool]:
    assert actor.id is not None
    async with get_sessionmaker()() as s, s.begin():
        await link_suggestion(s, actor.id, actor.org_id, suggestion_id, deal_id)
    return {"ok": True}


@router.post("/me/calendar/suggestions/{suggestion_id}/ignore")
async def ignore_my_suggestion(suggestion_id: uuid.UUID, actor: ActorDep) -> dict[str, bool]:
    assert actor.id is not None
    async with get_sessionmaker()() as s, s.begin():
        await ignore_suggestion(s, actor.id, suggestion_id)
    return {"ok": True}


# -------------------------------------------------------------------------------- ST-04: Gmail
@router.get("/deals/{deal_id}/gmail/threads")
async def gmail_threads(
    deal_id: uuid.UUID, actor: ActorDep, q: str | None = None
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await gmail.search_threads(s, actor, deal_id, q)


@router.post("/deals/{deal_id}/gmail/log")
async def gmail_log(
    deal_id: uuid.UUID, body: dict[str, Any], actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    activity_ids = await gmail.log_threads(actor, req, deal_id, body.get("thread_ids", []), body)
    return {"activity_ids": activity_ids}


@router.post("/deals/{deal_id}/gmail/threads/{thread_id}/refresh")
async def gmail_refresh(deal_id: uuid.UUID, thread_id: str, actor: ActorDep) -> dict[str, int]:
    async with get_sessionmaker()() as s:
        new_count = await gmail.refresh_thread(s, actor, deal_id, thread_id)
        return {"newer_messages": new_count}


@router.delete("/gmail/logged-threads/{thread_row_id}")
async def gmail_remove(
    thread_row_id: uuid.UUID, actor: ActorDep, req: RequestDep, reason: str | None = None
) -> dict[str, bool]:
    await gmail.remove_logged_thread(actor, req, thread_row_id, reason)
    return {"ok": True}


# --------------------------------------------------------------------------------- ST-05: admin
@router.get("/admin/integrations/google")
async def admin_google_health(actor: ActorDep) -> list[dict[str, Any]]:
    from app.modules.google.models import GoogleAccount
    from app.platform.reads import authorize_read

    async with get_sessionmaker()() as s:
        await authorize_read(s, actor, "google.admin.view")
        rows = (
            await s.execute(select(GoogleAccount).where(GoogleAccount.org_id == actor.org_id))
        ).scalars().all()  # fmt: skip
        states = {
            row.user_id: row
            for row in (
                await s.execute(
                    select(CalendarSyncState).where(CalendarSyncState.org_id == actor.org_id)
                )
            ).scalars()
        }
        out: list[dict[str, Any]] = []
        for a in rows:
            state = states.get(a.user_id)
            out.append(
                {
                    "user_id": a.user_id,
                    "google_email": a.google_email,
                    "status": a.status,
                    "last_success_at": a.last_success_at,
                    "last_error": a.last_error,
                    "channel_expiry": state.channel_expiry if state else None,
                }  # fmt: skip
            )
        return out


@router.post("/admin/integrations/google/{user_id}/retry")
async def admin_retry(
    user_id: uuid.UUID, actor: ActorDep, req: RequestDep, background: BackgroundTasks,
    reason: str | None = None,
) -> dict[str, bool]:  # fmt: skip
    from app.core.errors import NotFound
    from app.modules.google.models import GoogleAccount
    from app.platform.uow import UnitOfWork

    async with UnitOfWork(actor, req) as uow:
        account = (
            await uow.session.execute(
                select(GoogleAccount).where(
                    GoogleAccount.user_id == user_id, GoogleAccount.org_id == actor.org_id
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise NotFound("Not connected")
        await uow.authorize("google.admin.retry")
        cleaned = uow.reason("google.admin.retry", reason)
        await uow.audit(
            "google.admin.retry",
            "google_account",
            account.id,
            new={"action": "retry"},
            reason=cleaned,
        )
    background.add_task(_run_sync_task, user_id, actor.org_id)
    return {"ok": True}
