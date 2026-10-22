# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Approvals, notifications, outbox, history, files and live updates against a real database."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.core.errors import Conflict, PermissionDenied, ReasonRequired
from app.platform.approvals import service as approvals
from app.platform.approvals.models import ApprovalRequest
from app.platform.audit.models import AuditLog
from app.platform.context import Actor, RequestInfo
from app.platform.files import storage
from app.platform.notifications import email
from app.platform.notifications.models import NotificationSetting
from app.platform.outbox import service as outbox
from app.platform.outbox.models import OutboxEvent
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL, ROUTE_TEAM_LEADER
from app.platform.realtime import service as realtime
from app.platform.uow import UnitOfWork
from tests.helpers import dispatch_all, headers, notifications_for

REQ = RequestInfo()
RAN: list[str] = []


async def _noop_executor(uow, req):
    RAN.append(str(req.id))
    await uow.audit("test.executed", "approval", str(req.id), new={"ran": True})


approvals.register_type("test.noop", _noop_executor)


@pytest.fixture(autouse=True)
def memory_email():
    backend = email.MemoryBackend()
    email.set_backend(backend)
    RAN.clear()
    yield backend
    email.set_backend(None)


async def make_request(actor, *, routed_to=ROUTE_ADMIN_POOL, team_id=None, record_owner_id=None):
    async with UnitOfWork(actor, REQ) as uow:
        req = await approvals.create_request(
            uow, type_="test.noop", action_key="user.manage", summary=f"{actor.name} asks",
            payload={"x": 1}, reason="because", routed_to=routed_to, team_id=team_id,
            record_owner_id=record_owner_id,
        )  # fmt: skip
        return req.id


# ------------------------------------------------------------------------------ approvals
async def test_requester_reason_is_mandatory(world, database):
    with pytest.raises(ReasonRequired):
        async with UnitOfWork(world.tl_north, REQ) as uow:
            await approvals.create_request(
                uow, type_="test.noop", action_key="user.manage", summary="s", payload={},
                reason=" ", routed_to=ROUTE_ADMIN_POOL,
            )  # fmt: skip


async def test_admin_cannot_approve_own_request_but_super_admin_can(world, database):
    rid = await make_request(world.admin)
    with pytest.raises(PermissionDenied):
        await approvals.decide(world.admin, REQ, rid, approve=True, reason="me")
    rid_owner = await make_request(world.tl_north, record_owner_id=world.admin.id)
    with pytest.raises(PermissionDenied):  # about a record the Admin owns
        await approvals.decide(world.admin, REQ, rid_owner, approve=True, reason="mine")
    done = await approvals.decide(world.sa, REQ, rid_owner, approve=True, reason=None)
    assert done.status == "approved"
    sa_rid = await make_request(world.sa)
    assert (
        await approvals.decide(world.sa, REQ, sa_rid, approve=True, reason=None)
    ).status == "approved"
    # nobody else can approve their own request
    with pytest.raises(PermissionDenied):
        await approvals.decide(
            world.north1, REQ, await make_request(world.north1), approve=True, reason="x"
        )


async def test_double_approval_executes_exactly_once(world, database):
    rid = await make_request(world.tl_north)
    results = await asyncio.gather(
        approvals.decide(world.admin, REQ, rid, approve=True, reason="a"),
        approvals.decide(world.sa, REQ, rid, approve=True, reason="b"),
        return_exceptions=True,
    )
    oks = [r for r in results if isinstance(r, ApprovalRequest)]
    errs = [r for r in results if isinstance(r, Conflict)]
    assert len(oks) == 1 and len(errs) == 1
    assert RAN.count(str(rid)) == 1
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action_key == "test.executed",
                                                   AuditLog.entity_id == str(rid)))
        ).scalars().all()  # fmt: skip
    assert (
        len(rows) == 1 and rows[0].on_behalf_of == world.tl_north.id and rows[0].approval_id == rid
    )


async def test_team_level_request_routing_and_rejection(world, database):
    rid = await make_request(world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team)
    with pytest.raises(PermissionDenied):  # the other team's leader
        await approvals.decide(world.tl_south, REQ, rid, approve=True, reason="x")
    with pytest.raises(PermissionDenied):  # a rep
        await approvals.decide(world.north2, REQ, rid, approve=True, reason="x")
    with pytest.raises(ReasonRequired):  # a TL gives a reason when rejecting
        await approvals.decide(world.tl_north, REQ, rid, approve=False, reason=None)
    done = await approvals.decide(world.tl_north, REQ, rid, approve=False, reason="not now")
    assert done.status == "rejected" and done.decision_reason == "not now"
    assert RAN == []  # rejected: nothing executed
    async with get_sessionmaker()() as s:  # rejections are recorded too
        rows = (await s.execute(select(AuditLog).where(AuditLog.action_key == "approval.decide",
                                                       AuditLog.entity_id == str(rid)))).scalars().all()  # fmt: skip
    assert [r.outcome for r in rows].count("denied") == 3  # the three refused attempts are logged
    ok = [r for r in rows if r.outcome == "ok"]
    assert len(ok) == 1 and ok[0].new_value["status"] == "rejected"
    # a TL approves without needing a reason
    rid2 = await make_request(world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team)
    assert (
        await approvals.decide(world.tl_north, REQ, rid2, approve=True, reason=None)
    ).status == "approved"
    # an Admin must give a reason, a Super admin need not
    rid3 = await make_request(world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team)
    with pytest.raises(ReasonRequired):
        await approvals.decide(world.admin, REQ, rid3, approve=True, reason="")
    assert (
        await approvals.decide(world.sa, REQ, rid3, approve=True, reason=None)
    ).status == "approved"
    # already decided
    with pytest.raises(Conflict):
        await approvals.decide(world.sa, REQ, rid3, approve=True, reason=None)


async def test_cancel_and_visibility(client, world, database):
    rid = await make_request(world.north1)
    assert (
        await client.post(f"/approvals/{rid}/cancel", headers=headers(world.north2))
    ).status_code == 404
    assert (await client.get(f"/approvals/{rid}", headers=headers(world.north2))).status_code == 404
    assert (await client.get(f"/approvals/{rid}", headers=headers(world.north1))).status_code == 200
    assert (await client.get(f"/approvals/{rid}", headers=headers(world.admin))).status_code == 200
    r = await client.get("/approvals?tab=mine", headers=headers(world.north1))
    assert [a["id"] for a in r.json()] == [str(rid)]
    assert (await client.get("/approvals?tab=pending", headers=headers(world.north1))).json() == []
    pending = (await client.get("/approvals?tab=pending", headers=headers(world.admin))).json()
    assert str(rid) in [a["id"] for a in pending]
    r = await client.post(f"/approvals/{rid}/cancel", headers=headers(world.north1))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (
        await client.post(f"/approvals/{rid}/cancel", headers=headers(world.north1))
    ).status_code == 409
    r = await client.post(f"/approvals/{rid}/decide", json={"decision": "approve", "reason": "x"},
                          headers=headers(world.admin))  # fmt: skip
    assert r.status_code == 409


async def test_users_cannot_decide_via_api(client, world, database):
    rid = await make_request(world.tl_north)
    r = await client.post(f"/approvals/{rid}/decide", json={"decision": "approve", "reason": "x"},
                          headers=headers(world.north1))  # fmt: skip
    assert r.status_code == 403


# ------------------------------------------------------------------------------ notifications
async def test_approval_notifications_and_email(world, database, memory_email):
    rid = await make_request(world.tl_north)
    await dispatch_all()
    for approver in (world.admin, world.sa):
        got = await notifications_for(approver.id)
        assert [n.type for n in got] == ["approval.requested"]
        assert got[0].link == f"/approvals/{rid}"
    assert await notifications_for(world.tl_north.id) == []  # the requester is not told to approve
    assert {to for to, _, _ in memory_email.sent} >= {world.admin.email, world.sa.email}
    await approvals.decide(world.admin, REQ, rid, approve=True, reason="fine")
    await dispatch_all()
    mine = await notifications_for(world.tl_north.id)
    assert [n.type for n in mine] == ["approval.decided"] and "approved" in mine[0].body

    # running the dispatcher again does nothing new (idempotent)
    async def total() -> int:
        return sum([len(await notifications_for(a.id)) for a in world.all_actors])

    before = await total()
    await dispatch_all()
    assert await total() == before


async def test_required_notification_types_cannot_be_switched_off(world, database, admin_engine):
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO notification_settings (user_id, type, in_app, email) VALUES (:u,:t,false,false)"
            ),
            {"u": world.admin.id, "t": "approval.requested"},
        )
        await c.execute(
            text(
                "INSERT INTO notification_settings (user_id, type, in_app, email) VALUES (:u,:t,false,false)"
            ),
            {"u": world.admin.id, "t": "lead.assigned"},
        )
    from app.platform.notifications.service import notify

    async with get_sessionmaker()() as s, s.begin():
        await notify(s, world.org_id, [world.admin.id], "approval.requested", "t", "b")  # required
        await notify(s, world.org_id, [world.admin.id], "lead.assigned", "t", "b")  # optional, off
    assert [n.type for n in await notifications_for(world.admin.id)] == ["approval.requested"]
    _ = NotificationSetting


async def test_daily_reminder_notifies_every_eligible_approver_once_a_day(world, database):
    rid = await make_request(world.tl_north)
    await make_request(world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team)
    sent = await approvals.send_pending_reminders(get_sessionmaker())
    assert sent >= 4  # admin + SA for both requests, plus the north TL for the team request
    again = await approvals.send_pending_reminders(get_sessionmaker())
    assert again == 0
    for approver in (world.admin, world.sa):
        assert (
            len([n for n in await notifications_for(approver.id) if n.type == "approval.reminder"])
            == 2
        )
    tl = [n for n in await notifications_for(world.tl_north.id) if n.type == "approval.reminder"]
    assert len(tl) == 1  # only the team-level one
    await approvals.decide(world.sa, REQ, rid, approve=True, reason=None)
    assert (
        await approvals.send_pending_reminders(get_sessionmaker()) == 0
    )  # decided: no reminder...
    # (the remaining pending request was already reminded today)


async def test_notification_api_bell(client, world, database):
    await make_request(world.tl_north)
    await dispatch_all()
    r = await client.get("/notifications", headers=headers(world.admin))
    body = r.json()
    assert body["unread"] == 1 and body["items"][0]["type"] == "approval.requested"
    # another user never sees it
    assert (await client.get("/notifications", headers=headers(world.north1))).json()["items"] == []
    nid = body["items"][0]["id"]
    r = await client.patch("/notifications", json={"ids": [nid]}, headers=headers(world.admin))
    assert r.json() == {"marked": 1}
    assert (await client.get("/notifications", headers=headers(world.admin))).json()["unread"] == 0


# ------------------------------------------------------------------------------ outbox
async def test_failing_handler_retries_then_fails_and_can_be_retried(client, world, database):
    calls = []

    async def boom(session, event):
        calls.append(event.id)
        raise RuntimeError("downstream is down")

    outbox.HANDLERS.setdefault("test.boom", []).append(boom)
    try:
        async with UnitOfWork(world.admin, REQ) as uow:
            await uow.emit("test.boom", {"n": 1})
        for _attempt in range(outbox.MAX_ATTEMPTS):
            async with get_sessionmaker()() as s, s.begin():
                await s.execute(  # make it due now (skip real backoff)
                    text(
                        "update outbox_events set available_at=now() where type='test.boom' and status='pending'"
                    )
                )
            await outbox.dispatch_batch(get_sessionmaker())
        assert len(calls) == outbox.MAX_ATTEMPTS
        async with get_sessionmaker()() as s:
            ev = (
                await s.execute(select(OutboxEvent).where(OutboxEvent.type == "test.boom"))
            ).scalar_one()
        assert ev.status == "failed" and "downstream is down" in ev.last_error
        # Admin sees it on the failed-jobs page; a rep and a TL cannot
        r = await client.get("/admin/failed-jobs", headers=headers(world.admin))
        assert any(j["id"] == ev.id for j in r.json())
        assert (
            await client.get("/admin/failed-jobs", headers=headers(world.north1))
        ).status_code == 403
        assert (
            await client.get("/admin/failed-jobs", headers=headers(world.tl_north))
        ).status_code == 403
        outbox.HANDLERS["test.boom"].clear()
        r = await client.post(
            f"/admin/failed-jobs/{ev.id}/retry",
            json={"reason": "fixed"},
            headers=headers(world.admin),
        )
        assert r.status_code == 204
        await dispatch_all()
        async with get_sessionmaker()() as s:
            ev = (await s.execute(select(OutboxEvent).where(OutboxEvent.id == ev.id))).scalar_one()
        assert ev.status == "done"
    finally:
        outbox.HANDLERS.pop("test.boom", None)
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(text("update outbox_events set status='done' where type='test.boom'"))


async def test_two_dispatchers_never_process_the_same_event_twice(world, database):
    seen: list[int] = []

    async def slow(session, event):
        seen.append(event.id)
        await asyncio.sleep(0.05)

    outbox.HANDLERS.setdefault("test.slow", []).append(slow)
    try:
        async with UnitOfWork(world.admin, REQ) as uow:
            for i in range(10):
                await uow.emit("test.slow", {"i": i})
        await asyncio.gather(*(outbox.dispatch_batch(get_sessionmaker()) for _ in range(4)))
        assert len(seen) == len(set(seen)) == 10
    finally:
        outbox.HANDLERS.pop("test.slow", None)


# ------------------------------------------------------------------------------ history API
async def test_history_api_scope(client, world, database):
    r = await client.patch(
        f"/users/{world.north1.id}",
        json={"name": "Priya N", "reason": "typo"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    path = f"/history/user/{world.north1.id}"
    for ok in (world.north1, world.tl_north, world.admin, world.sa):
        r = await client.get(path, headers=headers(ok))
        assert r.status_code == 200, (ok.name, r.text)
        assert r.json()[0]["actor"] == "Anita" and r.json()[0]["reason"] == "typo"
    for hidden in (world.north2, world.tl_south, world.south1):
        assert (await client.get(path, headers=headers(hidden))).status_code == 404  # no leak
    assert (await client.get("/history/nope/1", headers=headers(world.admin))).status_code == 404


# ------------------------------------------------------------------------------ files
class FakeStorage:
    def __init__(self):
        self.objects: dict[str, int] = {}

    def presign_put(self, key, content_type):
        return f"https://files.test/put/{key}?ct={content_type}&exp=600"

    def presign_get(self, key, filename):
        return f"https://files.test/get/{key}?exp=300"

    async def size_of(self, key):
        return self.objects.get(key)


@pytest.fixture
def fake_storage():
    fs = FakeStorage()
    storage.set_storage(fs)
    yield fs
    storage.set_storage(None)


async def test_file_upload_and_scoped_download(client, world, database, fake_storage):
    target = world.north1  # files hang off the user record in Phase 1
    body = {
        "entity_type": "user",
        "entity_id": str(target.id),
        "filename": "../id card.pdf",
        "size": 1000,
    }
    assert (
        await client.post("/files/upload-url", json=body, headers=headers(world.north2))
    ).status_code == 403
    assert (
        await client.post(
            "/files/upload-url",
            json={**body, "size": 26 * 1024 * 1024},
            headers=headers(world.admin),
        )
    ).status_code == 409
    assert (
        await client.post(
            "/files/upload-url", json={**body, "entity_type": "nope"}, headers=headers(world.admin)
        )
    ).status_code == 404
    r = await client.post("/files/upload-url", json=body, headers=headers(world.admin))
    assert r.status_code == 200, r.text
    fid, url = r.json()["file_id"], r.json()["upload_url"]
    assert (
        r.json()["expires_in"] == 600 and "files.test/put/" in url and ".." not in url.split("?")[0]
    )
    # not downloadable until the upload completes
    assert (
        await client.get(f"/files/{fid}", headers=headers(world.admin), follow_redirects=False)
    ).status_code == 404
    assert (
        await client.post(f"/files/{fid}/complete", headers=headers(world.admin))
    ).status_code == 409
    key = url.split("/put/")[1].split("?")[0]
    fake_storage.objects[key] = 1000
    assert (
        await client.post(f"/files/{fid}/complete", headers=headers(world.admin))
    ).status_code == 200
    # download: scope of the owner record is checked every time
    r = await client.get(f"/files/{fid}", headers=headers(world.tl_north), follow_redirects=False)
    assert (
        r.status_code == 307
        and "files.test/get/" in r.headers["location"]
        and "exp=300" in r.headers["location"]
    )
    assert (
        await client.get(f"/files/{fid}", headers=headers(world.north1), follow_redirects=False)
    ).status_code == 307
    for hidden in (world.tl_south, world.south1, world.north2):
        r = await client.get(f"/files/{fid}", headers=headers(hidden), follow_redirects=False)
        assert r.status_code == 404, hidden.name
    assert (
        await client.get(
            f"/files/{uuid.uuid4()}", headers=headers(world.admin), follow_redirects=False
        )
    ).status_code == 404


# ------------------------------------------------------------------------------ live updates
class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self, code=1000):
        self.closed = True


async def emit_user_event(actor_world, owner: Actor):
    async with UnitOfWork(actor_world.admin, REQ) as uow:
        await uow.emit(
            "user.updated", {"user_id": str(owner.id)},
            rt={"entity_type": "user", "entity_id": str(owner.id), "owner_id": owner.id,
                "team_id": owner.team_id, "scope_key": "audit.view"},
        )  # fmt: skip


@pytest.fixture
async def listener(database):
    stop = asyncio.Event()
    task = asyncio.create_task(realtime.listen_forever(stop))
    await asyncio.sleep(0.5)  # let LISTEN attach
    yield
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    realtime.manager.connections.clear()


async def wait_for(pred, limit=5.0):
    end = asyncio.get_event_loop().time() + limit
    while asyncio.get_event_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_live_updates_are_scope_filtered_and_follow_team_moves(
    world, database, listener, admin_engine
):
    conns = {}
    for name, actor in {"tl_n": world.tl_north, "tl_s": world.tl_south, "n1": world.north1,
                        "n2": world.north2, "admin": world.admin}.items():  # fmt: skip
        ws = FakeWS()
        await realtime.manager.add(ws, actor)
        conns[name] = ws

    await emit_user_event(world, world.north1)
    await dispatch_all()
    assert await wait_for(lambda: conns["admin"].sent)
    await asyncio.sleep(0.3)
    got = {k: len(v.sent) for k, v in conns.items()}
    assert got == {"tl_n": 1, "tl_s": 0, "n1": 1, "n2": 0, "admin": 1}, got  # own/team/all scopes

    # move north1 to Team South: the old team's leader stops receiving their events immediately
    async with admin_engine.begin() as c:
        await c.execute(
            text("update users set team_id=:t where id=:u"),
            {"t": world.south_team, "u": world.north1.id},
        )
    for ws in conns.values():
        ws.sent.clear()
    await emit_user_event(world, world.north1)
    await dispatch_all()
    assert await wait_for(lambda: conns["tl_s"].sent)
    await asyncio.sleep(0.3)
    got = {k: len(v.sent) for k, v in conns.items()}
    assert got == {"tl_n": 0, "tl_s": 1, "n1": 1, "n2": 0, "admin": 1}, got

    # a deactivated user's socket is closed on the next event
    async with admin_engine.begin() as c:
        await c.execute(
            text("update users set is_active=false where id=:u"), {"u": world.north2.id}
        )
    await emit_user_event(world, world.south1)
    await dispatch_all()
    assert await wait_for(lambda: conns["n2"].closed)


async def test_catch_up_endpoint_returns_only_visible_events(client, world, database):
    async with get_sessionmaker()() as s:
        last = (await s.execute(text("select coalesce(max(id),0) from outbox_events"))).scalar_one()
    await emit_user_event(world, world.north1)
    await emit_user_event(world, world.south1)
    r = await client.get(f"/events?since={last}", headers=headers(world.tl_north))
    assert [e["entity_id"] for e in r.json()] == [str(world.north1.id)]
    r = await client.get(f"/events?since={last}", headers=headers(world.admin))
    assert len(r.json()) == 2
    r = await client.get(f"/events?since={last}", headers=headers(world.north2))
    assert r.json() == []
