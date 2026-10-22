# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 5: the audit trail, its hash chain and its immutability."""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_sessionmaker
from app.platform.approvals import service as approvals
from app.platform.audit import service as audit_service
from app.platform.audit.models import AuditLog
from app.platform.context import RequestInfo
from app.platform.jobs import app as jobs
from app.platform.notifications.models import Notification
from app.platform.uow import UnitOfWork
from tests.deep.phase1.kit import audit_count, audit_rows, h, new_email
from tests.deep.phase1.test_03_isolation import make_request

REQ = RequestInfo()


async def verify(org_id):
    async with get_sessionmaker()() as s:
        return await audit_service.verify_chain(s, org_id)


@contextlib.asynccontextmanager
async def trigger_off(engine):
    """Superuser tampering helper: the append-only trigger is switched off, then back on."""
    async with engine.begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
    try:
        yield
    finally:
        async with engine.begin() as c:
            await c.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))


async def seed_rows(world, n=6):
    async with UnitOfWork(world.admin, REQ) as uow:
        for i in range(n):
            await uow.audit(
                "deal.edit", "widget", f"w{i}", old={"v": i}, new={"v": i + 1}, reason=f"r{i}"
            )
    return [r["seq"] for r in await audit_rows(world.org_id)]


# ------------------------------------------------------------------ one row per successful write
async def test_every_phase1_write_path_writes_exactly_one_ok_row_with_full_detail(
    client, world, fake_storage
):
    org = world.org_id
    t0 = datetime.now(UTC) - timedelta(seconds=2)

    async def delta(coro_fn, expect_status):
        before = await audit_rows(org)
        r = await coro_fn()
        assert r.status_code == expect_status, (r.status_code, r.text)
        return r, (await audit_rows(org))[len(before) :]

    A, S = h(world.admin), h(world.sa)
    # create user
    r, new = await delta(
        lambda: client.post(
            "/users",
            json={
                "name": "A1",
                "email": new_email(),
                "role": "user",
                "team_id": str(world.north_team),
                "reason": "hire",
            },
            headers=A,
        ),
        201,
    )
    uid = r.json()["id"]
    assert len(new) == 1
    row = new[0]
    assert (
        row["outcome"] == "ok"
        and row["action_key"] == "user.manage"
        and row["entity_type"] == "user"
    )
    assert row["entity_id"] == uid and row["reason"] == "hire" and row["origin"] == "screen"
    assert str(row["actor_id"]) == str(world.admin.id) and row["actor_role"] == "admin"
    assert (
        row["old_value"] is None
        and row["new_value"]["email"]
        and row["new_value"]["role"] == "user"
    )
    assert row["occurred_at"] >= t0 and row["occurred_at"] <= datetime.now(UTC)
    assert row["request_id"] and row["user_agent"] and row["prev_hash"] and row["row_hash"]
    assert row["on_behalf_of"] is None and row["approval_id"] is None

    # rename / deactivate / reactivate / role change / team move: one ok row each
    steps = [
        ({"name": "A1 Renamed"}, "name"),
        ({"is_active": False}, "is_active"),
        ({"is_active": True}, "is_active"),
        ({"team_id": str(world.south_team)}, "team_id"),
        ({"role": "team_leader", "team_id": None}, None),
    ]
    for body, key in steps[:4]:
        r, new = await delta(
            lambda b=body: client.patch(f"/users/{uid}", json={**b, "reason": "why"}, headers=A),
            200,
        )
        assert len(new) == 1, (body, [x["action_key"] for x in new])
        n = new[0]
        assert (
            n["outcome"] == "ok"
            and n["reason"] == "why"
            and n["old_value"][key] != n["new_value"][key]
        )
        assert n["old_value"].keys() == n["new_value"].keys()
    # password resets
    r, new = await delta(
        lambda: client.post(
            f"/users/{uid}/password-reset", json={"reason": "locked out"}, headers=A
        ),
        204,
    )
    assert [(x["action_key"], x["outcome"], x["reason"]) for x in new] == [
        ("password.reset_others", "ok", "locked out")
    ]
    r, new = await delta(
        lambda: client.post(
            f"/users/{world.admin.id}/password-reset", json={"reason": "mine"}, headers=A
        ),
        204,
    )
    assert [x["action_key"] for x in new] == ["password.reset_own"]
    # teams
    r, new = await delta(
        lambda: client.post("/teams", json={"name": "Team Audit", "reason": "grow"}, headers=A), 201
    )
    tid = r.json()["id"]
    assert [(x["action_key"], x["entity_type"], x["entity_id"]) for x in new] == [
        ("team.manage", "team", tid)
    ]
    r, new = await delta(
        lambda: client.patch(
            f"/teams/{tid}", json={"name": "Team Audit 2", "reason": "rename"}, headers=A
        ),
        200,
    )
    assert (
        len(new) == 1
        and new[0]["old_value"] == {"name": "Team Audit"}
        and new[0]["new_value"] == {"name": "Team Audit 2"}
    )
    # login
    r, new = await delta(
        lambda: client.post("/auth/dev/login", json={"email": world.tl_south.email}), 200
    )
    assert [x["action_key"] for x in new] == ["auth.login"]
    # approval: request (TL), then cancel (requester), then request + decide (Admin approves)
    r, new = await delta(
        lambda: client.post(
            "/users",
            json={
                "name": "A2",
                "email": new_email(),
                "role": "user",
                "team_id": str(world.north_team),
                "reason": "need",
            },
            headers=h(world.tl_north),
        ),
        202,
    )
    aid = r.json()["approval_id"]
    assert [(x["outcome"], x["reason"], str(x["approval_id"])) for x in new] == [
        ("approval_requested", "need", aid)
    ]
    r, new = await delta(
        lambda: client.post(f"/approvals/{aid}/cancel", headers=h(world.tl_north)), 200
    )
    assert [x["action_key"] for x in new] == ["approval.cancel"] and new[0]["new_value"] == {
        "status": "cancelled"
    }
    r, new = await delta(
        lambda: client.post(
            "/users",
            json={
                "name": "A3",
                "email": new_email(),
                "role": "user",
                "team_id": str(world.north_team),
                "reason": "need",
            },
            headers=h(world.tl_north),
        ),
        202,
    )
    aid = r.json()["approval_id"]
    r, new = await delta(
        lambda: client.post(
            f"/approvals/{aid}/decide", json={"decision": "approve", "reason": "ok"}, headers=A
        ),
        200,
    )
    # the executed change and the decision: two writes, two rows, both on behalf of the requester
    assert sorted(x["action_key"] for x in new) == ["approval.decide", "user.manage"]
    for x in new:
        assert (
            x["outcome"] == "ok"
            and str(x["on_behalf_of"]) == str(world.tl_north.id)
            and str(x["approval_id"]) == aid
        )
        assert str(x["actor_id"]) == str(world.admin.id)
    # rejection is recorded
    r, new = await delta(
        lambda: client.post(
            "/users",
            json={
                "name": "A4",
                "email": new_email(),
                "role": "user",
                "team_id": str(world.north_team),
                "reason": "need",
            },
            headers=h(world.tl_north),
        ),
        202,
    )
    r, new = await delta(
        lambda: client.post(
            f"/approvals/{r.json()['approval_id']}/decide",
            json={"decision": "reject", "reason": "no"},
            headers=A,
        ),
        200,
    )
    assert [(x["action_key"], x["new_value"]["status"]) for x in new] == [
        ("approval.decide", "rejected")
    ]
    # failed job retry
    async with get_sessionmaker()() as s, s.begin():
        eid = (
            await s.execute(
                text(
                    "INSERT INTO outbox_events (org_id,type,payload,status,attempts) VALUES (:o,'x.f','{}','failed',10) RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()
    r, new = await delta(
        lambda: client.post(f"/admin/failed-jobs/{eid}/retry", json={"reason": "retry"}, headers=A),
        204,
    )
    assert [(x["action_key"], x["entity_type"], x["entity_id"], x["reason"]) for x in new] == [
        ("jobs.retry", "outbox_event", str(eid), "retry")
    ]
    # files
    r, new = await delta(
        lambda: client.post(
            "/files/upload-url",
            json={"entity_type": "user", "entity_id": uid, "filename": "a.pdf", "size": 10},
            headers=A,
        ),
        200,
    )
    fid, url = r.json()["file_id"], r.json()["upload_url"]
    assert [x["action_key"] for x in new] == ["file.upload"]
    fake_storage.objects[url.split("/put/")[1].split("?")[0]] = 10
    r, new = await delta(lambda: client.post(f"/files/{fid}/complete", headers=A), 200)
    assert [x["action_key"] for x in new] == ["file.complete"]
    # reads write nothing
    n = await audit_count(org)
    for p in (
        "/me",
        "/users",
        "/teams",
        "/approvals",
        "/notifications",
        f"/history/user/{uid}",
        "/events",
        "/admin/failed-jobs",
        f"/users/{uid}",
    ):
        assert (await client.get(p, headers=S)).status_code == 200
    await client.get(f"/files/{fid}", headers=S, follow_redirects=False)
    assert await audit_count(org) == n
    assert (await verify(org)).ok


async def test_refused_attempts_write_denied_rows_and_failed_validation_writes_nothing(
    client, world
):
    org = world.org_id
    n = len(await audit_rows(org))
    r = await client.patch(
        f"/users/{world.south1.id}", json={"name": "x", "reason": "r"}, headers=h(world.north1)
    )
    assert r.status_code == 403
    r = await client.post("/teams", json={"name": "Denied team"}, headers=h(world.tl_north))
    assert r.status_code == 403
    r = await client.post(
        "/admin/failed-jobs/1/retry", json={"reason": "r"}, headers=h(world.north1)
    )
    assert r.status_code == 403
    rows = (await audit_rows(org))[n:]
    assert [(x["outcome"], x["action_key"]) for x in rows] == [
        ("denied", "user.manage"),
        ("denied", "team.manage"),
        ("denied", "jobs.retry"),
    ]
    assert all(x["note"] for x in rows) and str(rows[0]["actor_id"]) == str(world.north1.id)
    # a reason-less Admin change is refused with a denied row too
    r = await client.post("/teams", json={"name": "No reason"}, headers=h(world.admin))
    assert r.status_code == 422
    rows = (await audit_rows(org))[n + 3 :]
    assert [(x["outcome"], x["action_key"]) for x in rows] == [("denied", "team.manage")]
    # pydantic validation failures happen before any service code: no row
    m = len(await audit_rows(org))
    assert (
        await client.post("/teams", json={"name": ""}, headers=h(world.admin))
    ).status_code == 422
    assert (
        await client.post("/users", json={"name": "x"}, headers=h(world.admin))
    ).status_code == 422
    assert len(await audit_rows(org)) == m
    assert (await verify(org)).ok


async def test_atomicity_when_the_outbox_insert_fails_nothing_is_saved(client, world, monkeypatch):
    from app.platform.outbox import service as outbox

    async def boom(*a, **k):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(outbox, "enqueue", boom)
    n = len(await audit_rows(world.org_id))
    email = new_email()
    r = await client.post(
        "/users",
        json={
            "name": "Ghost",
            "email": email,
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "r",
        },
        headers=h(world.admin),
    )
    assert r.status_code == 500
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(text("SELECT count(*) FROM users WHERE email=:e"), {"e": email})
        ).scalar_one() == 0
        assert (
            await s.execute(
                text(
                    "SELECT count(*) FROM team_memberships tm JOIN users u ON u.id=tm.user_id WHERE u.email=:e"
                ),
                {"e": email},
            )
        ).scalar_one() == 0
    assert len(await audit_rows(world.org_id)) == n  # no stray row
    # a 500 never leaks the exception text
    assert "outbox down" not in r.text and "Traceback" not in r.text


# ------------------------------------------------------------------ the hash chain
async def test_chain_is_valid_after_normal_use_and_links_every_row(client, world):
    await seed_rows(world)
    await client.post("/teams", json={"name": "Chain team", "reason": "r"}, headers=h(world.admin))
    await client.patch(
        f"/users/{world.north1.id}", json={"name": "N", "reason": "r"}, headers=h(world.admin)
    )
    res = await verify(world.org_id)
    assert res.ok and res.checked >= 8 and res.first_bad_seq is None
    rows = await audit_rows(world.org_id)
    assert rows[0]["prev_hash"] == "GENESIS"
    for a, b in zip(rows, rows[1:], strict=False):
        assert b["prev_hash"] == a["row_hash"] and b["seq"] > a["seq"]
    assert len({r["row_hash"] for r in rows}) == len(rows)


async def test_tamper_change_a_value_reports_that_row(world, admin_engine):
    seqs = await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE audit_log SET new_value='{\"v\": 999}' WHERE org_id=:o AND seq=:s"),
                {"o": world.org_id, "s": seqs[2]},
            )
    res = await verify(world.org_id)
    assert not res.ok and res.first_bad_seq == seqs[2] and "hash" in res.problem


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "'forged'"),
        ("actor_role", "'super_admin'"),
        ("old_value", "'{}'::jsonb"),
        ("action_key", "'x.y'"),
        ("origin", "'import'"),
        ("ip", "'1.2.3.4'"),
        ("note", "'n'"),
        ("occurred_at", "now() - interval '5 days'"),
        ("outcome", "'denied'"),
        ("entity_id", "'other'"),
    ],
)
async def test_tamper_any_hashed_field_is_detected(world, admin_engine, field, value):
    seqs = await seed_rows(world, 4)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text(f"UPDATE audit_log SET {field}={value} WHERE org_id=:o AND seq=:s"),
                {"o": world.org_id, "s": seqs[1]},
            )  # noqa: S608
    res = await verify(world.org_id)
    assert not res.ok and res.first_bad_seq == seqs[1], (field, res)


async def test_tamper_swap_two_rows_is_detected_at_the_first_of_them(world, admin_engine):
    seqs = await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:  # swap the sequence numbers of rows 2 and 3
            await c.execute(text("ALTER TABLE audit_log ALTER COLUMN seq SET GENERATED BY DEFAULT"))
        try:
            async with admin_engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE audit_log SET seq = -(CAST(:a AS bigint)) WHERE org_id=:o AND seq=:a"
                    ),
                    {"o": world.org_id, "a": seqs[2]},
                )
                await c.execute(
                    text("UPDATE audit_log SET seq = :a WHERE org_id=:o AND seq=:b"),
                    {"o": world.org_id, "a": seqs[2], "b": seqs[3]},
                )
                await c.execute(
                    text(
                        "UPDATE audit_log SET seq = :b WHERE org_id=:o AND seq=-(CAST(:a AS bigint))"
                    ),
                    {"o": world.org_id, "a": seqs[2], "b": seqs[3]},
                )
        finally:
            async with admin_engine.begin() as c:
                await c.execute(text("ALTER TABLE audit_log ALTER COLUMN seq SET GENERATED ALWAYS"))
    res = await verify(world.org_id)
    assert not res.ok and res.first_bad_seq == seqs[2]


async def test_tamper_delete_a_middle_row_is_detected_at_the_next_row(world, admin_engine):
    seqs = await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text("DELETE FROM audit_log WHERE org_id=:o AND seq=:s"),
                {"o": world.org_id, "s": seqs[2]},
            )
    res = await verify(world.org_id)
    assert not res.ok and res.first_bad_seq == seqs[3] and "link" in res.problem


async def test_tamper_delete_the_first_row_is_detected(world, admin_engine):
    seqs = await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text("DELETE FROM audit_log WHERE org_id=:o AND seq=:s"),
                {"o": world.org_id, "s": seqs[0]},
            )
    res = await verify(world.org_id)
    assert not res.ok and res.first_bad_seq == seqs[1]


async def test_tamper_insert_a_forged_row_is_detected(world, admin_engine):
    seqs = await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            # forged row at the end, with a made-up hash
            await c.execute(
                text(
                    "INSERT INTO audit_log (id,org_id,actor_role,action_key,entity_type,entity_id,outcome,origin,prev_hash,row_hash) VALUES (:i,:o,'admin','x','widget','z','ok','screen','abc','def')"
                ),
                {"i": uuid.uuid4(), "o": world.org_id},
            )
    res = await verify(world.org_id)
    assert not res.ok and res.first_bad_seq > seqs[-1]


async def test_tamper_insert_a_forged_row_in_the_middle_is_detected(world, admin_engine):
    seqs = await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(text("ALTER TABLE audit_log ALTER COLUMN seq SET GENERATED BY DEFAULT"))
        try:
            async with admin_engine.begin() as c:
                await c.execute(
                    text("UPDATE audit_log SET seq = seq + 1000000 WHERE org_id=:o AND seq > :s"),
                    {"o": world.org_id, "s": seqs[1]},
                )
                await c.execute(
                    text(
                        "INSERT INTO audit_log (id,seq,org_id,actor_role,action_key,entity_type,entity_id,outcome,origin,prev_hash,row_hash) VALUES (:i,:s,:o,'admin','x','widget','z','ok','screen',:p,'def')"
                    ),
                    {"i": uuid.uuid4(), "s": seqs[1] + 1, "o": world.org_id, "p": "wrong"},
                )
        finally:
            async with admin_engine.begin() as c:
                await c.execute(text("ALTER TABLE audit_log ALTER COLUMN seq SET GENERATED ALWAYS"))
    res = await verify(world.org_id)
    assert not res.ok and res.first_bad_seq == seqs[1] + 1


async def test_verify_is_per_org_a_tampered_org_does_not_flag_a_healthy_one(
    world, world_b, admin_engine
):
    seqs = await seed_rows(world)
    async with UnitOfWork(world_b.admin, REQ) as uow:
        await uow.audit("deal.edit", "widget", "b", new={"b": 1}, reason="r")
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE audit_log SET reason='x' WHERE org_id=:o AND seq=:s"),
                {"o": world.org_id, "s": seqs[0]},
            )
    assert not (await verify(world.org_id)).ok
    assert (await verify(world_b.org_id)).ok


async def test_tamper_truncating_the_tail_is_detected(world, admin_engine):
    seqs = await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text("DELETE FROM audit_log WHERE org_id=:o AND seq > :s"),
                {"o": world.org_id, "s": seqs[1]},
            )
    assert not (await verify(world.org_id)).ok


async def test_tamper_wiping_the_whole_trail_is_detected(world, admin_engine):
    await seed_rows(world)
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(text("DELETE FROM audit_log WHERE org_id=:o"), {"o": world.org_id})
    assert not (await verify(world.org_id)).ok


async def test_tamper_edit_and_recompute_the_rest_of_the_chain_is_detected(world, admin_engine):
    seqs = await seed_rows(world)
    async with get_sessionmaker()() as s:
        rows = list(
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.org_id == world.org_id).order_by(AuditLog.seq)
                )
            ).scalars()
        )
    rows[1].reason = "history rewritten"
    prev = rows[0].row_hash
    updates = []
    for r in rows[1:]:
        r.prev_hash = prev
        r.row_hash = audit_service.compute_hash(prev, r)
        prev = r.row_hash
        updates.append((r.seq, r.reason, r.prev_hash, r.row_hash))
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            for seq, reason, ph, rh in updates:
                await c.execute(
                    text(
                        "UPDATE audit_log SET reason=:r, prev_hash=:p, row_hash=:h WHERE org_id=:o AND seq=:s"
                    ),
                    {"r": reason, "p": ph, "h": rh, "o": world.org_id, "s": seq},
                )
    _ = seqs
    assert not (await verify(world.org_id)).ok


# ------------------------------------------------------------------ immutability
async def test_app_role_cannot_update_delete_or_truncate_audit_log(world):
    await seed_rows(world, 2)
    for stmt in (
        "UPDATE audit_log SET reason='x'",
        "UPDATE audit_log SET row_hash='x' WHERE org_id=:o",
        "DELETE FROM audit_log",
        "DELETE FROM audit_log WHERE org_id=:o",
        "TRUNCATE audit_log",
        "TRUNCATE audit_log, users CASCADE",
    ):
        async with get_sessionmaker()() as s:
            with pytest.raises(DBAPIError) as ei:
                await s.execute(text(stmt), {"o": world.org_id} if ":o" in stmt else {})
                await s.commit()
        assert "permission denied" in str(ei.value).lower(), (stmt, str(ei.value)[:200])
    assert await audit_count(world.org_id) == 2


async def test_app_role_cannot_bypass_the_guards(world):
    async with get_sessionmaker()() as s:
        for stmt in (
            "SET session_replication_role = replica",
            "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete",
            "DROP TRIGGER audit_log_no_update_delete ON audit_log",
            "DROP TABLE audit_log",
            "ALTER TABLE audit_log ADD COLUMN evil text",
            "CREATE TABLE evil (x int)",
            "CREATE ROLE evil SUPERUSER",
            "DROP FUNCTION audit_log_immutable() CASCADE",
        ):
            with pytest.raises(DBAPIError):
                async with s.begin_nested():
                    await s.execute(text(stmt))
        await s.rollback()


async def test_trigger_stops_the_superuser_update_delete_and_truncate(world, admin_engine):
    await seed_rows(world, 2)
    for stmt in (
        "UPDATE audit_log SET reason='x' WHERE org_id=:o",
        "DELETE FROM audit_log WHERE org_id=:o",
        "TRUNCATE audit_log",
    ):
        async with admin_engine.connect() as c:
            with pytest.raises(DBAPIError) as ei:
                await c.execute(text(stmt), {"o": world.org_id} if ":o" in stmt else {})
            assert "append-only" in str(ei.value).lower() or "audit" in str(ei.value).lower(), stmt
    assert await audit_count(world.org_id) == 2


async def test_app_role_may_insert_so_a_bad_row_must_be_caught_by_the_chain(world):
    """NOTE: the app role can INSERT arbitrary rows (it must, to write audit). A forged row
    is therefore possible from a compromised app and is caught only by the nightly chain check."""
    await seed_rows(world, 2)
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO audit_log (id,org_id,actor_role,action_key,entity_type,entity_id,outcome,origin,prev_hash,row_hash) VALUES (:i,:o,'admin','x','widget','z','ok','screen','a','b')"
            ),
            {"i": uuid.uuid4(), "o": world.org_id},
        )
    assert not (await verify(world.org_id)).ok


# ------------------------------------------------------------------ concurrency
async def test_fifty_simultaneous_writes_keep_the_chain_valid_and_sequence_gapless(client, world):
    org = world.org_id
    names = [f"Concurrent {i}" for i in range(50)]
    rs = await asyncio.gather(
        *[
            client.post("/teams", json={"name": n, "reason": "c"}, headers=h(world.admin))
            for n in names
        ]
    )
    assert [r.status_code for r in rs] == [201] * 50, [r.text for r in rs if r.status_code != 201][
        :2
    ]
    rows = await audit_rows(org)
    assert len(rows) == 50
    seqs = [r["seq"] for r in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == 50
    assert max(seqs) - min(seqs) == 49, "gap in seq"
    res = await verify(org)
    assert res.ok and res.checked == 50
    for a, b in zip(rows, rows[1:], strict=False):
        assert b["prev_hash"] == a["row_hash"]


async def test_mixed_concurrent_writes_and_denials_keep_the_chain_valid(client, world):
    calls = []
    for i in range(20):
        calls.append(
            client.post("/teams", json={"name": f"Mix {i}", "reason": "c"}, headers=h(world.admin))
        )
        calls.append(
            client.patch(
                f"/users/{world.north2.id}",
                json={"name": f"Nm {i}", "reason": "c"},
                headers=h(world.admin),
            )
        )
        calls.append(
            client.patch(
                f"/users/{world.south1.id}",
                json={"name": "x", "reason": "c"},
                headers=h(world.north1),
            )
        )
        calls.append(client.post("/auth/dev/login", json={"email": world.sa.email}))
    rs = await asyncio.gather(*calls)
    assert all(r.status_code < 500 for r in rs), [r.text for r in rs if r.status_code >= 500][:2]
    assert (await verify(world.org_id)).ok


# ------------------------------------------------------------------ nightly check
async def test_nightly_check_notifies_only_active_super_admins_once_per_break(
    world, world_b, admin_engine
):
    seqs = await seed_rows(world)
    async with UnitOfWork(world_b.admin, REQ) as uow:
        await uow.audit("deal.edit", "widget", "b", new={"b": 1}, reason="r")
    # a second, inactive Super admin and one in the other org
    from app.platform.permissions.roles import Role
    from tests.conftest import insert_user

    sa2 = await insert_user(admin_engine, world.org_id, "Sa Two", Role.SUPER_ADMIN, None)
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE users SET is_active=false WHERE id=:i"), {"i": sa2.id})
    assert (await jobs.check_audit_chains())[world.org_id] is True

    async def alerts():
        async with get_sessionmaker()() as s:
            return list(
                (
                    await s.execute(
                        select(Notification).where(
                            Notification.type == "audit.chain_broken",
                            Notification.org_id == world.org_id,
                        )
                    )
                ).scalars()
            )

    base = len(await alerts())
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE audit_log SET reason='tamper' WHERE org_id=:o AND seq=:s"),
                {"o": world.org_id, "s": seqs[1]},
            )
    res = await jobs.check_audit_chains()
    assert res[world.org_id] is False and res[world_b.org_id] is True
    got = (await alerts())[base:]
    assert [str(n.user_id) for n in got] == [
        str(world.sa.id)
    ]  # only the active SA of the broken org
    assert str(seqs[1]) in got[0].body
    await jobs.check_audit_chains()
    await jobs.check_audit_chains()
    assert len((await alerts())[base:]) == 1  # once per break
    # NOTE: a later, different tampering of the same chain is NOT announced again (the first bad
    # row is still the same), so new damage stays silent until the first break is repaired.
    async with trigger_off(admin_engine):
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE audit_log SET reason='tamper2' WHERE org_id=:o AND seq=:s"),
                {"o": world.org_id, "s": seqs[4]},
            )
    await jobs.check_audit_chains()
    assert len((await alerts())[base:]) == 1


async def test_nightly_check_with_an_empty_trail_and_job_return_value(world):
    res = await jobs.check_audit_chains()
    assert res[world.org_id] is True
    n = await jobs.audit_chain_check(0)
    assert isinstance(n, int)


async def test_approval_executed_changes_record_both_names_and_the_approval_id(client, world):
    rid = await make_request(world.tl_north)
    await approvals.decide(world.sa, REQ, rid, approve=True, reason=None)
    rows = await audit_rows(world.org_id, approval_id=rid)
    assert {r["action_key"] for r in rows} == {"deep.executed", "approval.decide", "user.manage"}
    rows = [r for r in rows if r["outcome"] == "ok"]
    assert {r["action_key"] for r in rows} == {"deep.executed", "approval.decide"}
    for r in rows:
        assert str(r["actor_id"]) == str(world.sa.id) and str(r["on_behalf_of"]) == str(
            world.tl_north.id
        )
    hist = (await client.get(f"/history/approval/{rid}", headers=h(world.sa))).json()
    assert {x["action"] for x in hist} >= {"approval.decide", "deep.executed"}
    ok = [x for x in hist if x["outcome"] == "ok"]
    assert len(ok) == 2
    assert all(
        x["on_behalf_of"] == world.tl_north.name and x["approval_id"] == str(rid) for x in ok
    )


async def test_org_wide_audit_log_is_for_admins_only_newest_first_with_record_names(
    client, world, world_b
):
    """GET /audit (the Audit log page): Admin and Super admin read the whole org's log, newest
    first, with the record's name resolved; a Team Leader or rep gets 403; another org's rows
    never appear."""
    async with UnitOfWork(world.admin, REQ) as uow:
        await uow.audit("deep.audit_list", "team", str(world.north_team), new={"x": 1})
    async with UnitOfWork(world_b.admin, REQ) as uow:
        await uow.audit("deep.audit_list_b", "team", str(world_b.north_team), new={"x": 2})
    for actor in (world.admin, world.sa):
        r = await client.get("/audit?limit=50", headers=h(actor))
        assert r.status_code == 200
        rows = r.json()
        assert [x["seq"] for x in rows] == sorted((x["seq"] for x in rows), reverse=True)
        mine = next(x for x in rows if x["action"] == "deep.audit_list")
        assert mine["entity_type"] == "team" and mine["target"] != "team"  # the team's name
        assert mine["actor"] != "System" and mine["new"] == {"x": 1}
        assert not any(x["action"] == "deep.audit_list_b" for x in rows)
    for actor in (world.tl_north, world.north1):
        assert (await client.get("/audit", headers=h(actor))).status_code == 403
