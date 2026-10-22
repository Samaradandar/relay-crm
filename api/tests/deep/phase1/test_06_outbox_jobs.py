# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 6: outbox, retries, failed jobs, dispatchers and the job schedules."""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.platform import registry
from app.platform.approvals import service as approvals
from app.platform.jobs import app as jobs
from app.platform.jobs.schema import apply_schema
from app.platform.notifications import service as notifications
from app.platform.outbox import service as outbox
from app.platform.outbox.models import OutboxEvent
from tests.deep.phase1.kit import audit_rows, h
from tests.deep.phase1.test_03_isolation import REQ, make_request
from tests.helpers import dispatch_all, notifications_for

registry.setup()  # outbox handlers are registered here, not on import


@pytest.fixture(autouse=True)
async def _quiet_outbox(admin_engine):
    """Events left pending by other tests must not eat this test's dispatch budget."""
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE outbox_events SET status='done' WHERE status='pending'"))


def etype() -> str:
    return "deep." + uuid.uuid4().hex[:10]


@contextlib.contextmanager
def handler(type_: str, fn):
    outbox.HANDLERS.setdefault(type_, []).append(fn)
    try:
        yield
    finally:
        outbox.HANDLERS[type_].remove(fn)


async def enqueue(org_id, type_, payload=None, n=1):
    ids = []
    async with get_sessionmaker()() as s, s.begin():
        for _ in range(n):
            ids.append((await outbox.enqueue(s, org_id, type_, payload or {})).id)
    return ids


async def event(eid) -> OutboxEvent:
    async with get_sessionmaker()() as s:
        return (await s.execute(select(OutboxEvent).where(OutboxEvent.id == eid))).scalar_one()


async def make_due(eid):
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            text("UPDATE outbox_events SET available_at = now() - interval '1 second' WHERE id=:i"),
            {"i": eid},
        )


# ------------------------------------------------------------------ at-least-once
async def test_handler_that_fails_halfway_leaves_no_partial_effect_and_succeeds_on_retry(world):
    t = etype()
    calls = {"n": 0}

    async def flaky(session, ev):
        calls["n"] += 1
        await notifications.notify(
            session,
            ev.org_id,
            [world.north1.id],
            "approval.reminder",
            "T",
            "B",
            dedupe_key=f"deep:{ev.id}",
        )
        if calls["n"] == 1:
            raise RuntimeError("crash after the side effect")

    (eid,) = await enqueue(world.org_id, t)
    with handler(t, flaky):
        assert await outbox.dispatch_batch(get_sessionmaker()) >= 1
        ev = await event(eid)
        assert ev.status == "pending" and ev.attempts == 1 and "crash" in ev.last_error
        # the half-done effect was rolled back with the failed attempt
        assert [
            n for n in await notifications_for(world.north1.id) if n.type == "approval.reminder"
        ] == []
        await make_due(eid)
        await dispatch_all()
    ev = await event(eid)
    assert ev.status == "done" and ev.processed_at is not None
    assert (
        len([n for n in await notifications_for(world.north1.id) if n.type == "approval.reminder"])
        == 1
    )


async def test_process_death_after_the_effect_committed_runs_the_handler_again_without_double_effects(
    world, memory_email
):
    """Simulate: the handler committed its work, the worker died before marking the event done.
    The built-in handlers must be idempotent (dedupe keys), also for the e-mail."""
    rid = await make_request(world.tl_north)
    async with get_sessionmaker()() as s:
        evs = (
            (
                await s.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.org_id == world.org_id, OutboxEvent.type == "approval.requested"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(evs) == 1
    await dispatch_all()
    first = len(await notifications_for(world.admin.id))
    mails = len([m for m in memory_email.sent if m[0] == world.admin.email])
    # "crash": put the event back to pending as if it was never marked done, run again, twice
    for _ in range(2):
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(
                text("UPDATE outbox_events SET status='pending', processed_at=NULL WHERE id=:i"),
                {"i": evs[0].id},
            )
        await dispatch_all()
    assert len(await notifications_for(world.admin.id)) == first == 1
    assert len([m for m in memory_email.sent if m[0] == world.admin.email]) == mails == 1
    _ = rid


async def test_duplicate_email_event_is_queued_once_per_notification(world):
    async with get_sessionmaker()() as s, s.begin():
        for _ in range(3):
            await notifications.notify(
                s, world.org_id, [world.admin.id], "approval.reminder", "T", "B", dedupe_key="same"
            )
    async with get_sessionmaker()() as s:
        n_mail = (
            await s.execute(
                text("SELECT count(*) FROM outbox_events WHERE org_id=:o AND type='email.send'"),
                {"o": world.org_id},
            )
        ).scalar_one()
    assert n_mail == 1


# ------------------------------------------------------------------ retry / backoff / failure
async def test_backoff_schedule_doubles_and_ten_failures_mark_the_event_failed(world):
    t = etype()

    async def boom(session, ev):
        raise ValueError("always")

    (eid,) = await enqueue(world.org_id, t)
    delays = []
    with handler(t, boom):
        for attempt in range(1, 11):
            await make_due(eid)
            await outbox.dispatch_batch(get_sessionmaker())
            ev = await event(eid)
            assert ev.attempts == attempt
            if attempt < 10:
                assert ev.status == "pending"
                delays.append((ev.available_at - datetime.now(UTC)).total_seconds())
        ev = await event(eid)
    assert (
        ev.status == "failed"
        and ev.attempts == 10
        and ev.last_error.startswith("ValueError: always")
    )
    # 2, 4, 8, ... seconds (computed at failure time; allow a small clock slack)
    for i, d in enumerate(delays, start=1):
        assert 2**i - 5 <= d <= 2**i + 1, (i, d)
    assert [round(outbox.backoff(i).total_seconds()) for i in (1, 2, 3, 9, 12, 20)] == [
        2,
        4,
        8,
        512,
        3600,
        3600,
    ]
    # a failed event is never picked up again
    with handler(t, boom):
        await make_due(eid)
        await dispatch_all()
    assert (await event(eid)).attempts == 10


async def test_an_event_that_is_not_due_yet_is_not_dispatched(world):
    t = etype()
    seen = []

    async def h1(session, ev):
        seen.append(ev.id)

    (eid,) = await enqueue(world.org_id, t)
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            text("UPDATE outbox_events SET available_at = now() + interval '1 hour' WHERE id=:i"),
            {"i": eid},
        )
    with handler(t, h1):
        await dispatch_all()
    assert seen == [] and (await event(eid)).status == "pending"


async def test_poison_event_does_not_block_the_others_and_order_is_kept(world):
    bad, good = etype(), etype()
    order = []

    async def boom(session, ev):
        raise RuntimeError("poison")

    async def ok(session, ev):
        order.append(ev.payload["n"])

    (poison,) = await enqueue(world.org_id, bad)
    for i in range(5):
        await enqueue(world.org_id, good, {"n": i})
    with handler(bad, boom), handler(good, ok):
        await dispatch_all()
    assert order == [0, 1, 2, 3, 4]  # created after the poison one, handled in order
    assert (await event(poison)).status == "pending" and (await event(poison)).attempts == 1


async def test_last_error_is_truncated_and_event_without_handler_is_marked_done(world):
    t = etype()

    async def boom(session, ev):
        raise RuntimeError("x" * 5000)

    (eid,) = await enqueue(world.org_id, t)
    with handler(t, boom):
        await dispatch_all()
    assert len((await event(eid)).last_error) <= 2000
    (nid,) = await enqueue(world.org_id, etype())
    await dispatch_all()
    assert (await event(nid)).status == "done"  # NOTE: an event nobody handles disappears silently


async def test_two_dispatchers_at_once_never_process_the_same_event_twice(world):
    t = etype()
    seen: list[int] = []

    async def slow(session, ev):
        seen.append(ev.id)
        await asyncio.sleep(0.01)

    ids = await enqueue(world.org_id, t, n=60)
    with handler(t, slow):
        counts = await asyncio.gather(
            *[outbox.dispatch_batch(get_sessionmaker(), limit=100) for _ in range(6)]
        )
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen)) == 60
    assert sum(counts) >= 60
    async with get_sessionmaker()() as s:
        st = (
            await s.execute(
                text("SELECT status, count(*) FROM outbox_events WHERE id = ANY(:i) GROUP BY 1"),
                {"i": ids},
            )
        ).all()
    assert st == [("done", 60)]


async def test_a_hanging_handler_does_not_stop_other_dispatchers(world):
    t, t2 = etype(), etype()
    gate = asyncio.Event()
    done = []

    async def hang(session, ev):
        await gate.wait()

    async def ok(session, ev):
        done.append(ev.id)

    (a,) = await enqueue(world.org_id, t)
    (b,) = await enqueue(world.org_id, t2)
    with handler(t, hang), handler(t2, ok):
        slow = asyncio.create_task(outbox.dispatch_batch(get_sessionmaker(), limit=1))
        await asyncio.sleep(0.3)
        await asyncio.wait_for(
            outbox.dispatch_batch(get_sessionmaker(), limit=5), 5
        )  # skips the locked row
        assert done == [b]
        gate.set()
        await asyncio.wait_for(slow, 5)
    assert (await event(a)).status == "done"


# ------------------------------------------------------------------ failed jobs list and retry
async def test_failed_jobs_list_and_retry_flow_over_http(client, world):
    t = etype()

    async def boom(session, ev):
        raise RuntimeError("boom")

    (eid,) = await enqueue(world.org_id, t)
    with handler(t, boom):
        for _ in range(10):
            await make_due(eid)
            await outbox.dispatch_batch(get_sessionmaker())
    for a in (world.north1, world.tl_north):
        assert (await client.get("/admin/failed-jobs", headers=h(a))).status_code == 403
        assert (
            await client.post(f"/admin/failed-jobs/{eid}/retry", json={"reason": "r"}, headers=h(a))
        ).status_code == 403
    for a in (world.admin, world.sa):
        r = (await client.get("/admin/failed-jobs", headers=h(a))).json()
        assert (
            [j["id"] for j in r] == [eid]
            and r[0]["attempts"] == 10
            and r[0]["type"] == t
            and "boom" in r[0]["last_error"]
        )
    # reason policy: Admin needs one, Super admin does not
    r = await client.post(f"/admin/failed-jobs/{eid}/retry", json={}, headers=h(world.admin))
    assert r.status_code == 422
    assert (await event(eid)).status == "failed"
    ok = []

    async def fine(session, ev):
        ok.append(ev.id)

    with handler(t, fine):
        r = await client.post(
            f"/admin/failed-jobs/{eid}/retry", json={"reason": "fixed"}, headers=h(world.admin)
        )
        assert r.status_code == 204
        ev = await event(eid)
        assert ev.status == "pending" and ev.attempts == 0
        await dispatch_all()
    assert ok == [eid] and (await event(eid)).status == "done"
    assert (await client.get("/admin/failed-jobs", headers=h(world.sa))).json() == []
    rows = [
        x for x in await audit_rows(world.org_id, action_key="jobs.retry") if x["outcome"] == "ok"
    ]
    assert len(rows) == 1 and rows[0]["reason"] == "fixed"


async def test_retry_of_an_unknown_or_not_failed_job_is_refused(client, world):
    (pending,) = await enqueue(world.org_id, etype())
    before = len([x for x in await audit_rows(world.org_id, action_key="jobs.retry")])
    for eid in (pending, 987654321):
        r = await client.post(
            f"/admin/failed-jobs/{eid}/retry", json={"reason": "r"}, headers=h(world.admin)
        )
        assert r.status_code in (404, 409), (eid, r.status_code)
    assert len(await audit_rows(world.org_id, action_key="jobs.retry")) == before


async def test_failed_jobs_list_is_newest_first(client, world, admin_engine):
    async with admin_engine.begin() as c:
        ids = [
            (
                await c.execute(
                    text(
                        "INSERT INTO outbox_events (org_id,type,payload,status,attempts,last_error) VALUES (:o,'x.f','{}','failed',10,'e') RETURNING id"
                    ),
                    {"o": world.org_id},
                )
            ).scalar_one()
            for _ in range(5)
        ]
    got = [j["id"] for j in (await client.get("/admin/failed-jobs", headers=h(world.sa))).json()]
    assert got == sorted(ids, reverse=True)


async def test_failed_jobs_list_is_capped_at_100(client, world, admin_engine):
    """RISK: only the newest 100 failed events are listed and there is no paging."""
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO outbox_events (org_id,type,payload,status,attempts,last_error) SELECT :o,'x.f','{}','failed',10,'e' FROM generate_series(1,130)"
            ),
            {"o": world.org_id},
        )
    got = (await client.get("/admin/failed-jobs", headers=h(world.sa))).json()
    assert len(got) == 100


# ------------------------------------------------------------------ jobs and schedules
async def test_procrastinate_schema_applies_twice_and_the_schedule_table_is_complete(database):
    await apply_schema()
    await apply_schema()
    crons = {p.task.name: p.cron for p in jobs.app.periodic_registry.periodic_tasks.values()}
    assert crons == {
        "outbox_dispatch": "* * * * *",
        "google_channel_renew": "0 */6 * * *",  # Phase 8: renew calendar watch channels
        "approval_reminders": "0 4 * * *",  # 09:30 IST
        "audit_chain_check": "0 19 * * *",  # 00:30 IST
        "pool_retry": "*/15 * * * *",
        "health_refresh": "0 19 * * *",
        "billing_alerts_daily": "0 4 * * *",  # 09:30 IST (Phase 4)
        "usage_sync": "0 * * * *",  # hourly (Phase 4)
        "phase5_billing_alerts_daily": "0 4 * * *",  # 09:30 IST (Phase 5)
        "followup_due_daily": "30 3 * * *",  # 09:00 IST (Phase 5)
        "proposals_pending_daily": "0 4 * * *",  # 09:30 IST (Phase 5)
        "performance_nightly": "30 19 * * *",  # 01:00 IST (Phase 6)
    }
    for name in crons:
        task = jobs.app.tasks[name]
        assert task.queueing_lock == name  # a slow run is never doubled up


def test_ist_times_in_the_schedules_are_right():
    """09:30 IST = 04:00 UTC and 00:30 IST = 19:00 UTC of the previous day."""
    ist = timedelta(hours=5, minutes=30)
    assert (datetime(2026, 1, 1, 4, 0, tzinfo=UTC) + ist).time().isoformat() == "09:30:00"
    assert (datetime(2026, 1, 1, 19, 0, tzinfo=UTC) + ist).time().isoformat() == "00:30:00"
    assert jobs.REMINDER_CRON == "0 4 * * *" and jobs.AUDIT_CHECK_CRON == "0 19 * * *"


async def test_reminder_job_twice_in_a_day_sends_once_and_decided_requests_get_none(world):
    rid = await make_request(world.tl_north)
    first = await approvals.send_pending_reminders(get_sessionmaker())
    assert first >= 2
    assert await approvals.send_pending_reminders(get_sessionmaker()) == 0
    assert await jobs.approval_reminders(0) == 0
    await approvals.decide(world.sa, REQ, rid, approve=True, reason=None)
    async with get_sessionmaker()() as s, s.begin():  # "tomorrow": forget today's dedupe keys
        await s.execute(
            text("DELETE FROM notifications WHERE org_id=:o AND type='approval.reminder'"),
            {"o": world.org_id},
        )
    assert (
        await approvals.send_pending_reminders(get_sessionmaker()) == 0
    )  # nothing pending any more


async def test_outbox_job_function_drains_the_queue(world):
    t = etype()
    seen = []

    async def ok(session, ev):
        seen.append(ev.id)

    ids = await enqueue(world.org_id, t, n=3)
    with handler(t, ok):
        n = await jobs.outbox_dispatch(0)
    assert n >= 3 and sorted(seen) == ids


async def test_a_dispatch_batch_of_one_handles_at_most_one(world):
    t = etype()

    async def ok(session, ev):
        return None

    await enqueue(world.org_id, t, n=3)
    with handler(t, ok):
        assert await outbox.dispatch_batch(get_sessionmaker(), limit=1) == 1


# ------------------------------------------------------------------ privileges after the schema step
async def test_app_role_privileges_after_running_the_documented_setup(database, admin_engine):
    await apply_schema()
    async with admin_engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee='relay_app' AND table_schema='public'"
                )
            )
        ).all()
        assert (
            await c.execute(text("SELECT has_schema_privilege('relay_app','public','CREATE')"))
        ).scalar_one() is False
        assert (
            await c.execute(
                text("SELECT has_database_privilege('relay_app', current_database(), 'CREATE')")
            )
        ).scalar_one() is False
    by = {}
    for t, p in rows:
        by.setdefault(t, set()).add(p)
    assert by["audit_log"] >= {"INSERT", "SELECT"}
    assert not (by["audit_log"] & {"UPDATE", "DELETE", "TRUNCATE"})


async def test_app_role_has_only_select_insert_update_delete_after_setup(database, admin_engine):
    await apply_schema()
    async with admin_engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee='relay_app' AND table_schema='public'"
                )
            )
        ).all()
    extra = {(t, p) for t, p in rows if p not in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    assert not extra, sorted(extra)[:6]


async def test_app_role_cannot_create_a_trigger_on_the_audit_log(database, admin_engine):
    await apply_schema()
    try:
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(
                text(
                    "CREATE TRIGGER deep_evil BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()"
                )
            )
        created = True
    except Exception:  # noqa: BLE001
        created = False
    finally:
        async with admin_engine.begin() as c:
            await c.execute(text("DROP TRIGGER IF EXISTS deep_evil ON audit_log"))
    assert not created


async def test_app_role_cannot_truncate_business_tables(database, admin_engine):
    await apply_schema()
    async with admin_engine.connect() as c:
        ok = (
            await c.execute(
                text(
                    "SELECT has_table_privilege('relay_app','users','TRUNCATE') OR has_table_privilege('relay_app','approval_requests','TRUNCATE')"
                )
            )
        ).scalar_one()
    assert ok is False
