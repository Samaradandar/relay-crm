# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Jobs and schedules, permission sync, S3 signing, email backends, and the real WebSocket."""

import asyncio
import json
import socket
import uuid

import pytest
import uvicorn
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.platform.context import RequestInfo
from app.platform.files.storage import GET_EXPIRES_SECONDS, PUT_EXPIRES_SECONDS, S3Storage
from app.platform.jobs import app as jobs
from app.platform.notifications import email
from app.platform.notifications.models import Notification
from app.platform.permissions import loader
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_table
from app.platform.permissions.sync import sync
from app.platform.uow import UnitOfWork
from tests.helpers import dispatch_all, headers


# ------------------------------------------------------------------ jobs and schedules
def test_schedules_are_registered_in_the_expected_times():
    crons = {p.task.name: p.cron for p in jobs.app.periodic_registry.periodic_tasks.values()}
    assert crons["approval_reminders"] == "0 4 * * *"  # 09:30 IST
    assert crons["audit_chain_check"] == "0 19 * * *"  # 00:30 IST
    assert crons["outbox_dispatch"] == "* * * * *"
    assert jobs.REMINDER_CRON == "0 4 * * *"


async def test_audit_chain_check_alerts_super_admins_when_broken(world, database, admin_engine):
    async with UnitOfWork(world.admin) as uow:
        await uow.audit("deal.edit", "widget", "j1", new={"a": 1}, reason="r")
        await uow.audit("deal.edit", "widget", "j2", new={"a": 2}, reason="r")
    assert (await jobs.check_audit_chains())[world.org_id] is True
    async with admin_engine.begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        await c.execute(
            text("DELETE FROM audit_log WHERE org_id=:o AND entity_id='j1'"), {"o": world.org_id}
        )
        await c.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))
    assert (await jobs.check_audit_chains())[world.org_id] is False
    async with get_sessionmaker()() as s:
        got = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == world.sa.id,
                        Notification.type == "audit.chain_broken",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(got) == 1
    await jobs.check_audit_chains()  # running again does not alert twice
    async with get_sessionmaker()() as s:
        again = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == world.sa.id,
                        Notification.type == "audit.chain_broken",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(again) == 1


async def test_procrastinate_runs_a_scheduled_task_end_to_end(database, world):
    from app.platform.jobs.schema import apply_schema

    await apply_schema()
    await apply_schema()  # idempotent
    import procrastinate

    # the module-level app was built before the test database existed: point it at this one
    connector = procrastinate.PsycopgConnector(conninfo=jobs.conninfo())
    with jobs.app.replace_connector(connector):
        async with jobs.app.open_async():
            await jobs.outbox_dispatch.defer_async(timestamp=0)
            await jobs.app.run_worker_async(wait=False)
            n = await connector.execute_query_one_async(
                "SELECT count(*) AS n FROM procrastinate_jobs WHERE status = 'succeeded'"
            )
    assert n["n"] >= 1  # our deferred job (the worker also queues its periodic ones)


# ------------------------------------------------------------------ permission sync
async def test_permission_sync_restores_changed_rows(database, admin_engine):
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE role_permissions SET scope='all' WHERE role='user' AND key='deal.view'")
        )
    loader.invalidate()
    async with get_sessionmaker()() as s:
        assert (await loader.get_table(s)) != seed_table()
    keys, rows = await sync()
    assert rows == len(seed_table()) and keys == len(KEYS)
    async with get_sessionmaker()() as s:
        assert (await loader.get_table(s)) == seed_table()


# ------------------------------------------------------------------ storage and email
def test_s3_links_are_short_lived_and_private(monkeypatch):
    monkeypatch.setenv("S3_ACCESS_KEY", "k")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_PUBLIC_ENDPOINT_URL", "http://localhost:9000")
    get_settings.cache_clear()
    try:
        st = S3Storage()
        put = st.presign_put("org/user/1/abc/x.pdf", "application/pdf")
        get = st.presign_get("org/user/1/abc/x.pdf", 'we"ird.pdf')
    finally:
        get_settings.cache_clear()
    assert put.startswith("http://localhost:9000/relay-files/org/user/1/abc/x.pdf?")
    assert f"X-Amz-Expires={PUT_EXPIRES_SECONDS}" in put and PUT_EXPIRES_SECONDS == 600
    assert f"X-Amz-Expires={GET_EXPIRES_SECONDS}" in get and GET_EXPIRES_SECONDS == 300
    assert "X-Amz-Signature=" in get and "attachment" in get


def test_email_backend_selection_and_template(monkeypatch):
    email.set_backend(None)
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    get_settings.cache_clear()
    assert isinstance(email.get_backend(), email.MemoryBackend)
    monkeypatch.setenv("SMTP_HOST", "mailpit")
    get_settings.cache_clear()
    assert isinstance(email.get_backend(), email.SmtpBackend)
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    get_settings.cache_clear()
    assert isinstance(email.get_backend(), email.SesBackend)
    get_settings.cache_clear()
    html, text_ = email.render("Hi <b>", "body & more", "/approvals/1")
    assert "&lt;b&gt;" in html and "&amp;" in html and "/approvals/1" in html
    assert "/approvals/1" in text_


# ------------------------------------------------------------------ real WebSocket + dev login
async def test_dev_login_listing(client, world):
    r = await client.get("/auth/dev/users")
    assert r.status_code == 200 and world.sa.email in {u["email"] for u in r.json()}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_websocket_end_to_end(world, database):
    import websockets

    from app.main import create_app

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        token = headers(world.tl_north)["Authorization"].split(" ", 1)[1]
        outsider = headers(world.tl_south)["Authorization"].split(" ", 1)[1]
        async with (
            websockets.connect(f"ws://127.0.0.1:{port}/ws?token={token}") as ws_n,
            websockets.connect(f"ws://127.0.0.1:{port}/ws?token={outsider}") as ws_s,
        ):
            await asyncio.sleep(0.8)  # let LISTEN attach and sockets register
            await ws_n.send("ping")
            assert await asyncio.wait_for(ws_n.recv(), 3) == "pong"
            async with UnitOfWork(world.admin, RequestInfo()) as uow:
                await uow.emit(
                    "user.updated",
                    {"user_id": str(world.north1.id)},
                    rt={
                        "entity_type": "user", "entity_id": str(world.north1.id),
                        "owner_id": world.north1.id, "team_id": world.north_team,
                        "scope_key": "audit.view",
                    },
                )  # fmt: skip
            await dispatch_all()
            msg = json.loads(await asyncio.wait_for(ws_n.recv(), 5))
            assert msg["type"] == "user.updated" and msg["entity_id"] == str(world.north1.id)
            with pytest.raises(TimeoutError):  # the other team's leader gets nothing
                await asyncio.wait_for(ws_s.recv(), 1.0)
        # a bad token is refused
        with pytest.raises(websockets.exceptions.InvalidStatus) as refused:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token=nope"):
                pass
        assert refused.value.response.status_code == 403
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


_ = uuid
