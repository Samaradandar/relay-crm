# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Platform HTTP endpoints: /me, approvals, history, notifications, files, events, failed jobs,
dev login and the live-update WebSocket."""

import asyncio
import contextlib
import time
import uuid
from typing import Annotated, Any, Literal

import jwt
from fastapi import APIRouter, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import get_sessionmaker
from app.core.errors import NotFound, Unauthenticated
from app.core.security import DevAuthProvider, get_auth_provider
from app.modules.users.models import Team, User
from app.modules.users.schemas import Reason, ReasonBody
from app.platform.approvals import service as approvals
from app.platform.audit import service as audit_service
from app.platform.audit.models import AuditLog
from app.platform.deps import ActorDep, RequestDep, actor_for_identity
from app.platform.files import service as files
from app.platform.files.storage import LocalDiskStorage, get_storage
from app.platform.notifications import service as notifications
from app.platform.notifications.models import NotificationSetting
from app.platform.notifications.service import NOTIFICATION_TYPES, REQUIRED_TYPES
from app.platform.outbox import service as outbox
from app.platform.permissions import loader
from app.platform.permissions.keys import KEYS
from app.platform.permissions.roles import Scope
from app.platform.reads import authorize_read
from app.platform.realtime import service as realtime
from app.platform.reasons.policy import reason_rule
from app.platform.schemas import (
    ApprovalOut,
    DevUser,
    FailedJob,
    FileOut,
    HistoryItem,
    LiveEvent,
    MarkedOut,
    MeOut,
    NotificationList,
    NotificationPreferencesList,
    NotificationPreferencesPatch,
    TokenOut,
    UploadOut,
)
from app.platform.uow import UnitOfWork

router = APIRouter()
MAX_INT = 2**63 - 1  # PostgreSQL bigint; larger numbers are a 422, not a 500


# ---------------------------------------------------------------------------------- /me
@router.get("/me", tags=["me"], response_model=MeOut)
async def me(actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        user = await s.get(User, actor.id)
        table = await loader.get_table(s)
        team_brief = None
        if actor.team_id:
            team = await s.get(Team, actor.team_id)
            if team is not None and team.org_id == actor.org_id and team.deleted_at is None:
                leaders = (
                    await s.scalars(
                        select(User.name)
                        .where(
                            User.team_id == team.id,
                            User.org_id == actor.org_id,
                            User.role == "team_leader",
                            User.is_active.is_(True),
                            User.deleted_at.is_(None),
                        )
                        .order_by(User.name)
                    )
                ).all()
                team_brief = {"id": str(team.id), "name": team.name, "leaders": list(leaders)}
    perms = []
    for key, pk in KEYS.items():
        rule = table[(actor.role_enum, key)]
        perms.append(
            {
                "key": key,
                "class": pk.action_class.value,
                "scope": rule.scope.value,
                "mode": rule.mode.value,
                "own_deals_only": rule.own_deals_only,
                "not_own_deals": rule.not_own_deals,
                "reason": reason_rule(actor.role_enum, pk.action_class, key).value,
            }
        )
    return {
        "id": str(actor.id),
        "name": actor.name,
        "email": actor.email,
        "role": actor.role,
        "team_id": str(actor.team_id) if actor.team_id else None,
        "team": team_brief,
        "org_id": str(actor.org_id),
        "last_login_at": user.last_login_at if user else None,
        "permissions": perms,
    }


# --------------------------------------------------------------- /me/notification-preferences
@router.get(
    "/me/notification-preferences",
    tags=["me"],
    response_model=NotificationPreferencesList,
)
async def get_notification_preferences(actor: ActorDep) -> dict[str, Any]:
    """Every notification type, the caller's current e-mail switch (on by default when no row
    exists), and whether it is required (so the frontend can lock the toggle). The in-app bell is
    never suppressed by this screen; only the e-mail channel is user-configurable (PRD 11)."""
    async with get_sessionmaker()() as s:
        rows = {
            r.type: r.email
            for r in (
                await s.execute(
                    select(NotificationSetting).where(NotificationSetting.user_id == actor.id)
                )
            ).scalars()
        }
    return {
        "items": [
            {
                "type": type_,
                "label": label,
                "email_enabled": rows.get(type_, True),
                "required": type_ in REQUIRED_TYPES,
            }
            for type_, label in NOTIFICATION_TYPES.items()
        ]
    }


@router.put(
    "/me/notification-preferences",
    tags=["me"],
    response_model=NotificationPreferencesList,
)
async def put_notification_preferences(
    body: NotificationPreferencesPatch, actor: ActorDep
) -> dict[str, Any]:
    """Update one or more e-mail preferences. A required type cannot be disabled: that row is
    rejected with a 422 rather than silently kept, so the caller knows which one did not apply."""
    bad = [p.type for p in body.preferences if p.type in REQUIRED_TYPES and not p.email_enabled]
    if bad:
        raise HTTPException(422, f"Cannot disable required notification type(s): {', '.join(bad)}")
    async with get_sessionmaker()() as s, s.begin():
        for p in body.preferences:
            if p.type not in NOTIFICATION_TYPES:
                raise HTTPException(422, f"Unknown notification type: {p.type}")
            stmt = (
                pg_insert(NotificationSetting)
                .values(user_id=actor.id, type=p.type, in_app=True, email=p.email_enabled)
                .on_conflict_do_update(
                    index_elements=[NotificationSetting.user_id, NotificationSetting.type],
                    set_={"email": p.email_enabled},
                )
            )
            await s.execute(stmt)
    return await get_notification_preferences(actor)


# ---------------------------------------------------------------------------------- dev login
class DevLogin(BaseModel):
    email: str


@router.get("/auth/dev/users", tags=["auth"], response_model=list[DevUser])
async def dev_users() -> list[dict[str, Any]]:
    provider = get_auth_provider()
    if not isinstance(provider, DevAuthProvider):
        raise HTTPException(404)
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(User)
                .where(User.deleted_at.is_(None), User.is_active.is_(True))
                .order_by(User.name)
            )
        ).scalars()
        return [{"email": u.email, "name": u.name, "role": u.role} for u in rows]


@router.post("/auth/dev/login", tags=["auth"], response_model=TokenOut)
async def dev_login(body: DevLogin) -> dict[str, str]:
    provider = get_auth_provider()
    if not isinstance(provider, DevAuthProvider):
        raise HTTPException(404)
    from app.core.security import Identity

    actor = await actor_for_identity(Identity(email=body.email, sub=f"dev:{body.email.lower()}"))
    async with UnitOfWork(actor) as uow:
        await uow.session.execute(
            update(User).where(User.id == actor.id).values(last_login_at=func.now())
        )
        assert actor.id is not None
        await uow.audit("auth.login", "user", actor.id, note="dev login")
    return {"token": provider.issue(body.email)}


# ---------------------------------------------------------------------------------- approvals
class DecideBody(BaseModel):
    decision: Literal["approve", "reject"]
    reason: Reason | None = None


async def _approval_out(s, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    names = {
        u.id: u.name
        for u in (
            await s.execute(
                select(User).where(
                    User.id.in_([i for i in (req.requested_by, req.decided_by) if i])
                )
            )
        ).scalars()
    }
    return {
        "id": str(req.id),
        "type": req.type,
        "status": req.status,
        "summary": req.payload.get("summary"),
        "payload": req.payload,
        "requested_by": str(req.requested_by),
        "requested_by_name": names.get(req.requested_by),
        "request_reason": req.request_reason,
        "routed_to": req.routed_to,
        "decided_by": str(req.decided_by) if req.decided_by else None,
        "decided_by_name": names.get(req.decided_by) if req.decided_by else None,
        "decision_reason": req.decision_reason,
        "created_at": req.created_at,
        "decided_at": req.decided_at,
    }


@router.get("/approvals", tags=["approvals"], response_model=list[ApprovalOut])
async def list_approvals(
    actor: ActorDep,
    tab: Annotated[Literal["pending", "decided", "mine"], Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0, le=MAX_INT)] = 0,
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        rows = await approvals.list_requests(s, actor, tab, limit=limit, offset=offset)
        return [await _approval_out(s, r) for r in rows]


@router.get("/approvals/{approval_id}", tags=["approvals"], response_model=ApprovalOut)
async def get_approval(approval_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await _approval_out(s, await approvals.get_request(s, actor, approval_id))


@router.post("/approvals/{approval_id}/decide", tags=["approvals"], response_model=ApprovalOut)
async def decide_approval(
    approval_id: uuid.UUID, body: DecideBody, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    done = await approvals.decide(
        actor, req, approval_id, approve=body.decision == "approve", reason=body.reason
    )
    async with get_sessionmaker()() as s:
        return await _approval_out(s, done)


@router.post("/approvals/{approval_id}/cancel", tags=["approvals"], response_model=ApprovalOut)
async def cancel_approval(
    approval_id: uuid.UUID, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    done = await approvals.cancel(actor, req, approval_id)
    async with get_sessionmaker()() as s:
        return await _approval_out(s, done)


# ---------------------------------------------------------------------------------- history
@router.get(
    "/history/{entity_type}/{entity_id}", tags=["history"], response_model=list[HistoryItem]
)
async def history(
    entity_type: str,
    entity_id: str,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_seq: Annotated[int | None, Query(le=MAX_INT)] = None,
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        if entity_type == "outbox_event":  # failed-job retries: same right as the failed-jobs list
            await authorize_read(s, actor, "jobs.view_failed")
        else:
            await authorize_read(s, actor, "audit.view", (entity_type, entity_id))
        rows = await audit_service.history(
            s, actor.org_id, entity_type, entity_id, limit=limit, before_seq=before_seq
        )
        ids = {r.actor_id for r in rows if r.actor_id} | {
            r.on_behalf_of for r in rows if r.on_behalf_of
        }
        names = (
            {
                u.id: u.name
                for u in (await s.execute(select(User).where(User.id.in_(ids)))).scalars()
            }
            if ids
            else {}
        )
    return [
        {
            "id": str(r.id),
            "seq": r.seq,
            "at": r.occurred_at,
            "actor": names.get(r.actor_id, "System") if r.actor_id else "System",
            "actor_role": r.actor_role,
            "on_behalf_of": names.get(r.on_behalf_of) if r.on_behalf_of else None,
            "action": r.action_key,
            "outcome": r.outcome,
            "old": r.old_value,
            "new": r.new_value,
            "reason": r.reason,
            "approval_id": str(r.approval_id) if r.approval_id else None,
            "origin": r.origin,
            "note": r.note,
        }
        for r in rows
    ]


@router.get("/audit", tags=["history"])
async def audit_log(
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    before_seq: Annotated[int | None, Query(le=MAX_INT)] = None,
    entity_type: str | None = None,
) -> list[dict[str, Any]]:
    """The org-wide audit log, newest first (the Audit log page). Only for roles that see the
    whole org's history (Admin, Super admin); everyone else reads one record's history above."""
    async with get_sessionmaker()() as s:
        decision = await authorize_read(s, actor, "audit.view")
        if decision.scope != Scope.ALL:
            raise HTTPException(403, "Only Admins see the whole audit log")
        stmt = select(AuditLog).where(AuditLog.org_id == actor.org_id)
        if before_seq is not None:
            stmt = stmt.where(AuditLog.seq < before_seq)
        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        rows = list((await s.execute(stmt.order_by(AuditLog.seq.desc()).limit(limit))).scalars())
        from app.modules.accounts.models import Account
        from app.modules.leads.models import Deal

        user_ids = {r.actor_id for r in rows if r.actor_id} | _ids(rows, "user")
        lookups = {
            "user": select(User.id, User.name).where(User.id.in_(user_ids)),
            "team": select(Team.id, Team.name).where(Team.id.in_(_ids(rows, "team"))),
            "deal": select(Deal.id, Account.name)
            .join(Account, Account.id == Deal.account_id)
            .where(Deal.id.in_(_ids(rows, "deal"))),
        }
        names: dict[str, dict[uuid.UUID, str]] = {}
        for kind, query in lookups.items():
            names[kind] = {row_id: name for row_id, name in (await s.execute(query)).all()}
        users = names["user"]

    def target(r: AuditLog) -> str:
        key = _as_uuid(r.entity_id)
        name = names.get(r.entity_type, {}).get(key) if key else None
        return name or r.entity_type.replace("_", " ")

    return [
        {
            "id": str(r.id),
            "seq": r.seq,
            "at": r.occurred_at,
            "actor": users.get(r.actor_id, "System") if r.actor_id else "System",
            "actor_role": r.actor_role,
            "action": r.action_key,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "target": target(r),
            "old": r.old_value,
            "new": r.new_value,
            "reason": r.reason,
            "note": r.note,
        }
        for r in rows
    ]


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _ids(rows: list[AuditLog], entity_type: str) -> set[uuid.UUID]:
    found = (_as_uuid(r.entity_id) for r in rows if r.entity_type == entity_type)
    return {i for i in found if i}


# ---------------------------------------------------------------------------------- notifications
class MarkRead(BaseModel):
    ids: list[uuid.UUID] | None = Field(default=None, description="omit to mark all as read")


@router.get("/notifications", tags=["notifications"], response_model=NotificationList)
async def list_notifications(actor: ActorDep, unread_only: bool = False) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        items = await notifications.list_for_user(s, actor.id, unread_only=unread_only)  # type: ignore[arg-type]
        unread = await notifications.unread_count(s, actor.id)  # type: ignore[arg-type]
    return {
        "unread": unread,
        "items": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "read_at": n.read_at,
                "created_at": n.created_at,
            }
            for n in items
        ],
    }


@router.patch("/notifications", tags=["notifications"], response_model=MarkedOut)
async def mark_notifications(body: MarkRead, actor: ActorDep) -> dict[str, int]:
    async with get_sessionmaker()() as s, s.begin():
        n = await notifications.mark_read(s, actor.id, body.ids)  # type: ignore[arg-type]
    return {"marked": n}


# ---------------------------------------------------------------------------------- files
class UploadRequest(BaseModel):
    entity_type: str
    entity_id: str
    filename: str
    content_type: str = "application/octet-stream"
    size: int


@router.post("/files/upload-url", tags=["files"], response_model=UploadOut)
async def upload_url(body: UploadRequest, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    rec, url = await files.create_upload(
        actor,
        req,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        filename=body.filename,
        content_type=body.content_type,
        size=body.size,
    )
    return {
        "file_id": str(rec.id),
        "upload_url": url,
        "expires_in": files.upload_expiry_seconds(),
        "method": "PUT",
        "headers": {"Content-Type": rec.content_type},
    }


@router.post("/files/{file_id}/complete", tags=["files"], response_model=FileOut)
async def complete_file(file_id: uuid.UUID, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    rec = await files.complete_upload(actor, req, file_id)
    return {"file_id": str(rec.id), "status": rec.status, "size": rec.size}


@router.get("/files/{file_id}", tags=["files"])
async def download_file(file_id: uuid.UUID, actor: ActorDep) -> RedirectResponse:
    async with get_sessionmaker()() as s:
        url = await files.download_url(s, actor, file_id)
    return RedirectResponse(url, status_code=307)


def _local_storage(op: str, key: str, exp: int, sig: str) -> LocalDiskStorage:
    """The signed link is the permission here (as with S3): it was issued only after the
    upload/download permission check, and it expires in minutes."""
    store = get_storage()
    if not isinstance(store, LocalDiskStorage) or not store.check(op, key, exp, sig):
        raise NotFound("File not found")
    return store


@router.put("/files/local/put", include_in_schema=False)
async def local_put(request: Request, key: str, exp: int, sig: str) -> Response:
    store = _local_storage("put", key, exp, sig)
    try:
        store.path_for(key)  # refuses a key that would land outside the storage folder
    except ValueError as exc:
        raise NotFound("File not found") from exc
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > files.MAX_SIZE:
            raise HTTPException(413, "File is too large")
    await store.put_bytes(key, bytes(data), request.headers.get("content-type", ""))
    return Response(status_code=200)


@router.get("/files/local/get", include_in_schema=False)
async def local_get(key: str, exp: int, sig: str, name: str = "file") -> FileResponse:
    store = _local_storage("get", key, exp, sig)
    try:
        path = store.path_for(key)
    except ValueError as exc:
        raise NotFound("File not found") from exc
    if not path.is_file():
        raise NotFound("File not found")
    return FileResponse(path, filename=name)


# ---------------------------------------------------------------------------------- events / jobs
@router.get("/events", tags=["realtime"], response_model=list[LiveEvent])
async def catch_up(
    actor: ActorDep, since: Annotated[int, Query(ge=0, le=MAX_INT)] = 0
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await realtime.events_since(s, actor, since)


@router.get("/admin/failed-jobs", tags=["admin"], response_model=list[FailedJob])
async def failed_jobs(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        await authorize_read(s, actor, "jobs.view_failed")
        rows = await outbox.list_failed(s, actor.org_id)
    return [
        {
            "id": e.id,
            "type": e.type,
            "attempts": e.attempts,
            "last_error": e.last_error,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@router.post("/admin/failed-jobs/{event_id}/retry", tags=["admin"], status_code=204)
async def retry_failed_job(
    event_id: Annotated[int, Path(ge=0, le=MAX_INT)],
    body: ReasonBody,
    actor: ActorDep,
    req: RequestDep,
) -> None:
    async with UnitOfWork(actor, req) as uow:
        await uow.authorize("jobs.retry")
        reason = uow.reason("jobs.retry", body.reason)
        if not await outbox.retry_failed(uow.session, actor.org_id, event_id):
            raise NotFound("Failed job not found")
        await uow.audit("jobs.retry", "outbox_event", str(event_id), reason=reason)


# ---------------------------------------------------------------------------------- websocket
@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str) -> None:
    """Live updates. Authenticated with the same token; each event is scope-filtered."""
    try:
        identity = await get_auth_provider().verify(token)
        actor = await actor_for_identity(identity)
    except Unauthenticated:
        await ws.close(code=4401)
        return
    except Exception:  # noqa: BLE001
        await ws.close(code=4403)
        return
    await ws.accept()
    try:  # the signature was just checked; read the expiry to end the session with the token
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
    except jwt.PyJWTError:
        exp = None
    expires_at = float(exp) if isinstance(exp, int | float) else None
    conn = await realtime.manager.add(ws, actor, expires_at)
    try:
        while True:
            wait = None if expires_at is None else max(expires_at - time.time(), 0.0)
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=wait)
            except TimeoutError:
                await ws.close(code=4401)  # token expired
                break
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        realtime.manager.remove(conn)


async def start_realtime() -> tuple[asyncio.Event, asyncio.Task[None]]:
    stop = asyncio.Event()
    task = asyncio.create_task(realtime.listen_forever(stop))
    return stop, task


async def stop_realtime(stop: asyncio.Event, task: asyncio.Task[None]) -> None:
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
