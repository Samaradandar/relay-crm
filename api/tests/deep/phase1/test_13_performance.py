# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test: timings with realistic volumes. Numbers are printed (run with -s) and copied
into the report; the assertions are deliberately generous so the suite is not flaky."""

import time
import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.platform.audit import service as audit_service
from app.platform.audit.models import AuditLog
from app.platform.context import RequestInfo
from app.platform.permissions.roles import Role
from app.platform.uow import UnitOfWork
from tests.conftest import insert_user
from tests.deep.phase1.kit import h


async def timed(label, coro, store):
    t0 = time.perf_counter()
    r = await coro
    store[label] = (time.perf_counter() - t0) * 1000
    return r


async def build_org(admin_engine, n_teams=60, per_team=25):
    org = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id,name) VALUES (:i,'Perf org')"), {"i": org})
        await c.execute(
            text(
                "INSERT INTO teams (id,org_id,name) SELECT gen_random_uuid(), :o, 'Team ' || g FROM generate_series(1,:n) g"
            ),
            {"o": org, "n": n_teams},
        )
        await c.execute(
            text(
                "INSERT INTO users (id,org_id,email,name,role,team_id) "
                "SELECT gen_random_uuid(), :o, 'u' || t.rn || '_' || g || '@perf.example', 'User ' || g, 'user', t.id "
                "FROM (SELECT id, row_number() OVER () rn FROM teams WHERE org_id=:o) t, generate_series(1,:per) g"
            ),
            {"o": org, "per": per_team},
        )
    admin = await insert_user(admin_engine, org, "Perf Admin", Role.ADMIN, None)
    sa = await insert_user(admin_engine, org, "Perf SA", Role.SUPER_ADMIN, None)
    return org, admin, sa


async def test_main_reads_and_writes_with_1500_users_60_teams_and_large_tables(
    client, admin_engine
):
    org, admin, sa = await build_org(admin_engine)
    async with admin_engine.begin() as c:
        rep = (
            await c.execute(
                text("SELECT id FROM users WHERE org_id=:o AND role='user' LIMIT 1"), {"o": org}
            )
        ).scalar_one()
        await c.execute(
            text(
                "INSERT INTO approval_requests (org_id,type,action_key,payload,requested_by,request_reason,routed_to) SELECT :o,'user.create','user.manage','{\"summary\":\"s\"}',:u,'r','admin_pool' FROM generate_series(1,3000)"
            ),
            {"o": org, "u": rep},
        )
        await c.execute(
            text(
                "INSERT INTO notifications (org_id,user_id,type,title,body) SELECT :o,:u,'x','t','b' FROM generate_series(1,5000)"
            ),
            {"o": org, "u": admin.id},
        )
        await c.execute(
            text(
                "INSERT INTO outbox_events (org_id,type,payload,status) SELECT :o,'x.y','{}','done' FROM generate_series(1,50000)"
            ),
            {"o": org},
        )
        await c.execute(
            text(
                "INSERT INTO audit_log (id,org_id,actor_role,action_key,entity_type,entity_id,outcome,origin,prev_hash,row_hash) SELECT gen_random_uuid(),:o,'admin','x','user',:e,'ok','screen','p','h' FROM generate_series(1,3000)"
            ),
            {"o": org, "e": str(rep)},
        )
    t: dict[str, float] = {}
    H = h(admin)
    assert (
        len(
            (
                await timed("GET /users (1500 rows, admin)", client.get("/users", headers=H), t)
            ).json()
        )
        == 1502
    )
    assert (
        len((await timed("GET /teams (60 teams)", client.get("/teams", headers=H), t)).json()) == 60
    )
    assert (
        len(
            (
                await timed(
                    "GET /approvals?tab=pending (500 of 3000)",
                    client.get("/approvals?tab=pending", headers=H),
                    t,
                )
            ).json()
        )
        == 500
    )
    await timed("GET /notifications (50 of 5000)", client.get("/notifications", headers=H), t)
    await timed(
        "GET /history/user/<id> (3000 rows, page 50)",
        client.get(f"/history/user/{rep}", headers=H),
        t,
    )
    await timed(
        "GET /events?since=0 (50k outbox rows)", client.get("/events?since=0", headers=H), t
    )
    await timed("GET /me", client.get("/me", headers=H), t)
    await timed("GET /admin/failed-jobs", client.get("/admin/failed-jobs", headers=H), t)
    for i in range(5):
        await timed(
            f"PATCH /users/{{id}} rename #{i}",
            client.patch(f"/users/{rep}", json={"name": f"N{i}", "reason": "perf"}, headers=H),
            t,
        )
    await timed(
        "POST /teams", client.post("/teams", json={"name": "Perf new", "reason": "r"}, headers=H), t
    )
    await timed(
        "POST /auth/dev/login",
        client.post("/auth/dev/login", json={"email": "u1_1@perf.example"}),
        t,
    )
    print("\nPERF (ms):")
    for k, v in t.items():
        print(f"  {v:8.1f}  {k}")
    assert (
        t["GET /users (1500 rows, admin)"] < 5000
        and t["GET /approvals?tab=pending (500 of 3000)"] < 5000
    )
    assert t["GET /teams (60 teams)"] < 5000


async def test_audit_append_does_not_slow_down_with_other_tenants_rows(world, admin_engine):
    """The next audit row needs the previous hash of ITS org. With 400k newer rows of other orgs the
    lookup must stay fast."""
    async with UnitOfWork(world.admin, RequestInfo()) as uow:
        await uow.audit("deal.edit", "widget", "first", new={"a": 1}, reason="r")
    t0 = time.perf_counter()
    for i in range(5):
        async with UnitOfWork(world.admin, RequestInfo()) as uow:
            await uow.audit("deal.edit", "widget", f"w{i}", new={"a": 1}, reason="r")
    base = (time.perf_counter() - t0) / 5 * 1000
    async with admin_engine.begin() as c:
        orgs = [uuid.uuid4() for _ in range(40)]
        for o in orgs:
            await c.execute(text("INSERT INTO orgs (id,name) VALUES (:i,'noise')"), {"i": o})
            await c.execute(
                text(
                    "INSERT INTO audit_log (id,org_id,actor_role,action_key,entity_type,entity_id,outcome,origin,prev_hash,row_hash) SELECT gen_random_uuid(),:o,'admin','x','w','e','ok','screen','p','h' FROM generate_series(1,10000)"
                ),
                {"o": o},
            )
    t0 = time.perf_counter()
    for i in range(5):
        async with UnitOfWork(world.admin, RequestInfo()) as uow:
            await uow.audit("deal.edit", "widget", f"n{i}", new={"a": 1}, reason="r")
    noisy = (time.perf_counter() - t0) / 5 * 1000
    print(
        f"\nAUDIT APPEND: {base:.1f} ms with an empty table, {noisy:.1f} ms with 400k newer rows of 40 other orgs"
    )
    assert noisy < 1500


async def test_verify_chain_time_and_memory_at_30000_rows(admin_engine):
    org = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id,name) VALUES (:i,'chain')"), {"i": org})
    rows = []
    prev = audit_service.GENESIS
    from app.platform.context import Actor

    actor = Actor(id=None, org_id=org, role="system", team_id=None, name="s")
    _ = actor
    from datetime import UTC, datetime

    for i in range(30000):
        r = AuditLog(
            id=uuid.uuid4(),
            org_id=org,
            occurred_at=datetime.now(UTC),
            actor_id=None,
            actor_role="admin",
            on_behalf_of=None,
            action_key="x",
            entity_type="w",
            entity_id=str(i),
            outcome="ok",
            old_value=None,
            new_value={"i": i, "pad": "x" * 200},
            reason="r",
            approval_id=None,
            request_id=None,
            ip=None,
            user_agent=None,
            origin="screen",
            note=None,
            prev_hash=prev,
        )
        r.row_hash = audit_service.compute_hash(prev, r)
        prev = r.row_hash
        rows.append(r)
    import json as _json

    async with admin_engine.begin() as c:  # one statement, rows inserted in list order
        await c.execute(
            text(
                "INSERT INTO audit_log (id,org_id,occurred_at,actor_role,action_key,entity_type,entity_id,outcome,new_value,reason,origin,prev_hash,row_hash) "
                "SELECT id, :org, at, 'admin','x','w', e, 'ok', CAST(nv AS jsonb), 'r','screen', p, h "
                "FROM unnest(CAST(:ids AS uuid[]), CAST(:ats AS timestamptz[]), CAST(:es AS text[]), CAST(:nvs AS text[]), CAST(:ps AS text[]), CAST(:hs AS text[])) "
                "WITH ORDINALITY AS t(id, at, e, nv, p, h, ord) ORDER BY ord"
            ),
            {
                "org": org,
                "ids": [r.id for r in rows],
                "ats": [r.occurred_at for r in rows],
                "es": [r.entity_id for r in rows],
                "nvs": [_json.dumps(r.new_value) for r in rows],
                "ps": [r.prev_hash for r in rows],
                "hs": [r.row_hash for r in rows],
            },
        )
    import tracemalloc

    tracemalloc.start()
    t0 = time.perf_counter()
    async with get_sessionmaker()() as s:
        res = await audit_service.verify_chain(s, org)
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        f"\nVERIFY CHAIN: {res.checked} rows in {dt:.1f}s, peak Python memory {peak / 1e6:.0f} MB"
    )
    if not res.ok:
        async with get_sessionmaker()() as s2:
            rr = (
                await s2.execute(
                    text(
                        "select seq, entity_id, prev_hash=:p from audit_log where org_id=:o and seq between :a and :b order by seq"
                    ),
                    {"p": "x", "o": org, "a": res.first_bad_seq - 3, "b": res.first_bad_seq + 1},
                )
            ).all()
        print("DEBUGROWS", rr)
    assert res.ok and res.checked == 30000
    assert dt < 60
    _ = select
    _ = pytest
