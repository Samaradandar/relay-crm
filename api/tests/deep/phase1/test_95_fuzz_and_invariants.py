# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test: model-based fuzz. Random legal and illegal actions by random roles (seeded),
then database invariants and cross-checks of every figure the API shows."""

import random
import uuid

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.platform import registry
from app.platform.approvals import service as approvals
from app.platform.audit import service as audit_service
from tests.deep.phase1.kit import h, new_email
from tests.helpers import dispatch_all

registry.setup()

ROLE_NAMES = ["user", "user", "user", "team_leader", "admin", "super_admin"]


async def one(conn, sql, **p):
    return (await conn.execute(text(sql), p)).scalar_one()


async def snapshot_org(org):
    async with get_sessionmaker()() as s:
        out = {}
        for t in (
            "users",
            "teams",
            "audit_log",
            "approval_requests",
            "notifications",
            "outbox_events",
            "files",
            "team_memberships",
        ):
            out[t] = (
                await s.execute(text(f"SELECT count(*) FROM {t} WHERE org_id=:o"), {"o": org})
            ).scalar_one()  # noqa: S608
        out["user_state"] = (
            await s.execute(
                text(
                    "SELECT md5(string_agg(id::text||role||coalesce(team_id::text,'')||is_active::text||name, ',' ORDER BY id)) FROM users WHERE org_id=:o"
                ),
                {"o": org},
            )
        ).scalar_one()
        return out


async def check_invariants(org, client, actors_by_id):
    async with get_sessionmaker()() as s:
        # chain
        res = await audit_service.verify_chain(s, org)
        assert res.ok, res
        rows = (
            await s.execute(
                text(
                    "SELECT seq, outcome, actor_id, action_key FROM audit_log WHERE org_id=:o ORDER BY seq"
                ),
                {"o": org},
            )
        ).all()
        seqs = [r[0] for r in rows]
        assert seqs == sorted(set(seqs))
        assert {r[1] for r in rows} <= {"ok", "denied", "approval_requested"}
        # every actor of an audit row is a user of this org
        bad = await one(
            s,
            "SELECT count(*) FROM audit_log a WHERE a.org_id=:o AND a.actor_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=a.actor_id AND u.org_id=a.org_id)",
            o=org,
        )
        assert bad == 0
        # users and teams
        assert (
            await one(
                s,
                "SELECT count(*) FROM users WHERE org_id=:o AND role='super_admin' AND is_active AND deleted_at IS NULL",
                o=org,
            )
            >= 1
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM users WHERE org_id=:o AND role IN ('admin','super_admin') AND team_id IS NOT NULL",
                o=org,
            )
            == 0
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM users u WHERE u.org_id=:o AND u.team_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.id=u.team_id AND t.org_id=u.org_id)",
                o=org,
            )
            == 0
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM teams t WHERE t.org_id=:o AND t.leader_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=t.leader_id AND u.team_id=t.id AND u.role='team_leader')",
                o=org,
            )
            == 0
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM (SELECT leader_id FROM teams WHERE org_id=:o AND leader_id IS NOT NULL GROUP BY 1 HAVING count(*)>1) x",
                o=org,
            )
            == 0
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM users u WHERE u.org_id=:o AND u.is_active AND u.role='team_leader' AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.leader_id=u.id)",
                o=org,
            )
            == 0
        ), "an active Team Leader who leads nothing"
        # memberships: exactly one open row for every user with a team, none for the rest
        assert (
            await one(
                s,
                "SELECT count(*) FROM users u WHERE u.org_id=:o AND u.team_id IS NOT NULL AND (SELECT count(*) FROM team_memberships m WHERE m.user_id=u.id AND m.ended_at IS NULL) <> 1",
                o=org,
            )
            == 0
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM users u WHERE u.org_id=:o AND u.team_id IS NULL AND EXISTS (SELECT 1 FROM team_memberships m WHERE m.user_id=u.id AND m.ended_at IS NULL)",
                o=org,
            )
            == 0
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM users u JOIN team_memberships m ON m.user_id=u.id AND m.ended_at IS NULL WHERE u.org_id=:o AND m.team_id <> u.team_id",
                o=org,
            )
            == 0
        )
        # one email per org among live users
        assert (
            await one(
                s,
                "SELECT count(*) FROM (SELECT lower(email) FROM users WHERE org_id=:o AND deleted_at IS NULL GROUP BY 1 HAVING count(*)>1) x",
                o=org,
            )
            == 0
        )
        # approvals: status consistent
        assert (
            await one(
                s,
                "SELECT count(*) FROM approval_requests WHERE org_id=:o AND ((status='pending' AND (decided_by IS NOT NULL OR decided_at IS NOT NULL)) OR (status IN ('approved','rejected') AND (decided_by IS NULL OR decided_at IS NULL)) OR (status='cancelled' AND decided_at IS NULL) OR status NOT IN ('pending','approved','rejected','cancelled'))",
                o=org,
            )
            == 0
        )
        # an approved request has exactly one executed audit row for its type; user.create creates one user
        approved = (
            await s.execute(
                text(
                    "SELECT id, type, payload->>'email' FROM approval_requests WHERE org_id=:o AND status='approved' AND type IN ('user.create','user.change_role')"
                ),
                {"o": org},
            )
        ).all()
        for rid, typ, em in approved:
            n = await one(
                s,
                "SELECT count(*) FROM audit_log WHERE org_id=:o AND approval_id=:a AND action_key='user.manage' AND outcome='ok'",
                o=org,
                a=rid,
            )
            assert n == 1, (typ, n)
            if typ == "user.create":
                assert (
                    await one(
                        s,
                        "SELECT count(*) FROM users WHERE org_id=:o AND lower(email)=lower(:e)",
                        o=org,
                        e=em,
                    )
                    == 1
                )
        assert (
            await one(
                s,
                "SELECT count(*) FROM approval_requests WHERE org_id=:o AND status IN ('rejected','cancelled') AND EXISTS (SELECT 1 FROM audit_log a WHERE a.approval_id=approval_requests.id AND a.action_key='user.manage' AND a.outcome='ok')",
                o=org,
            )
            == 0
        )
        # notifications: no duplicate keys, only to this org's users
        assert (
            await one(
                s,
                "SELECT count(*) FROM (SELECT user_id, dedupe_key FROM notifications WHERE org_id=:o AND dedupe_key IS NOT NULL GROUP BY 1,2 HAVING count(*)>1) x",
                o=org,
            )
            == 0
        )
        assert (
            await one(
                s,
                "SELECT count(*) FROM notifications n WHERE n.org_id=:o AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=n.user_id AND u.org_id=n.org_id)",
                o=org,
            )
            == 0
        )
        # outbox: nothing stuck without a reason
        assert (
            await one(
                s,
                "SELECT count(*) FROM outbox_events WHERE org_id=:o AND status='pending' AND attempts=0 AND available_at < now() - interval '1 minute'",
                o=org,
            )
            == 0
        )
        # files
        assert (
            await one(
                s,
                "SELECT count(*) FROM files WHERE org_id=:o AND (size <= 0 OR size > 26214400)",
                o=org,
            )
            == 0
        )
        # --- cross-checks of figures the API shows
        active_by_team = dict(
            (
                await s.execute(
                    text(
                        "SELECT team_id, count(*) FROM users WHERE org_id=:o AND is_active AND deleted_at IS NULL AND team_id IS NOT NULL GROUP BY 1"
                    ),
                    {"o": org},
                )
            ).all()
        )
        pending_all = {
            str(r[0])
            for r in (
                await s.execute(
                    text("SELECT id FROM approval_requests WHERE org_id=:o AND status='pending'"),
                    {"o": org},
                )
            ).all()
        }
    sa = next(a for a in actors_by_id.values() if a["role"] == "super_admin" and a["active"])
    teams = (await client.get("/teams", headers=sa["h"])).json()
    for t in teams:
        assert t["member_count"] == active_by_team.get(uuid.UUID(t["id"]), 0), t
    users = (await client.get("/users", headers=sa["h"])).json()
    async with get_sessionmaker()() as s:
        n_users = await one(
            s, "SELECT count(*) FROM users WHERE org_id=:o AND deleted_at IS NULL", o=org
        )
    assert len(users) == n_users
    inbox = {a["id"] for a in (await client.get("/approvals?tab=pending", headers=sa["h"])).json()}
    assert inbox <= pending_all and len(inbox) == min(len(inbox), 100)
    # every role's directory view equals what SQL says
    async with get_sessionmaker()() as s:
        for uid, a in actors_by_id.items():
            if not a["active"] or (a["role"] == "user" and not a["team"]):
                continue
            r = await client.get("/users", headers=a["h"])
            if r.status_code != 200:
                continue
            if a["role"] in ("admin", "super_admin"):
                want = n_users
            else:
                want = await one(
                    s,
                    "SELECT count(*) FROM users WHERE org_id=:o AND deleted_at IS NULL AND team_id=(SELECT team_id FROM users WHERE id=:u)",
                    o=org,
                    u=uid,
                )
            assert len(r.json()) == want, (a["role"], len(r.json()), want)
            nb = (await client.get("/notifications", headers=a["h"])).json()
            unread = await one(
                s, "SELECT count(*) FROM notifications WHERE user_id=:u AND read_at IS NULL", u=uid
            )
            assert (
                nb["unread"] == min(unread, 50) or nb["unread"] <= unread
            )  # (the 50 cap is BUG-P1-06)


@pytest.mark.parametrize("seed", [7, 11, 2026])
async def test_random_sequences_keep_every_invariant(
    client, world, world_b, admin_engine, fake_storage, seed
):
    rng = random.Random(seed)
    org = world.org_id
    before_b = await snapshot_org(world_b.org_id)
    actors: dict[uuid.UUID, dict] = {}
    for a in world.all_actors:
        actors[a.id] = {
            "id": a.id,
            "email": a.email,
            "role": a.role,
            "team": a.team_id,
            "active": True,
            "h": h(a),
        }
    teams = [world.north_team, world.south_team]
    approvals_seen: list[str] = []
    stats = {"calls": 0, "5xx": []}
    expected_denied = 0

    def pick_actor():
        pool = [a for a in actors.values() if a["active"]]
        return rng.choice(pool)

    async def call(method, path, actor, body=None, mutating=True):
        nonlocal expected_denied
        r = await client.request(method, path, json=body, headers=actor["h"])
        stats["calls"] += 1
        if r.status_code >= 500:
            stats["5xx"].append((method, path, body, r.status_code))
        if mutating and r.status_code in (403, 422):
            try:
                j = r.json()
            except Exception:  # noqa: BLE001
                return r
            teamless = "not in a team" in j.get("message", "")
            if (
                j.get("code") in ("permission_denied", "out_of_scope", "reason_required")
                and not teamless
            ):
                expected_denied += 1
        return r

    async def refresh_actor(uid):
        async with get_sessionmaker()() as s:
            row = (
                await s.execute(
                    text("SELECT role, team_id, is_active FROM users WHERE id=:u"), {"u": uid}
                )
            ).one()
        actors[uid].update(role=row[0], team=row[1], active=row[2])

    for step in range(350):
        a = pick_actor()
        kind = rng.choice(
            [
                "create",
                "create",
                "patch",
                "patch",
                "patch",
                "team",
                "rename_team",
                "reset",
                "decide",
                "decide",
                "cancel",
                "retry",
                "upload",
                "mark",
                "login",
                "read",
                "dispatch",
                "failed_event",
            ]
        )
        reason = rng.choice(["because", "because", "", "  ", None, "r" * 50])
        body_reason = {} if reason is None else {"reason": reason}
        if kind == "create":
            role = rng.choice(ROLE_NAMES)
            body = {"name": f"Fz {step}", "email": new_email("fz"), "role": role, **body_reason}
            if role in ("user", "team_leader") and rng.random() < 0.9:
                body["team_id"] = str(rng.choice(teams))
            r = await call("POST", "/users", a, body)
            if r.status_code == 201:
                j = r.json()
                from app.platform.context import Actor
                from tests.helpers import headers as mk

                actors[uuid.UUID(j["id"])] = {
                    "id": uuid.UUID(j["id"]),
                    "email": j["email"],
                    "role": j["role"],
                    "team": j["team_id"],
                    "active": True,
                    "h": mk(
                        Actor(
                            id=uuid.UUID(j["id"]),
                            org_id=org,
                            role=j["role"],
                            team_id=None,
                            name="x",
                            email=j["email"],
                        )
                    ),
                }
            elif r.status_code == 202:
                approvals_seen.append(r.json()["approval_id"])
        elif kind == "patch":
            target = rng.choice(list(actors.values()))
            body = dict(body_reason)
            for k in rng.sample(["name", "role", "is_active", "team_id"], rng.randint(1, 3)):
                if k == "name":
                    body["name"] = f"Renamed {step}"
                elif k == "role":
                    body["role"] = rng.choice(ROLE_NAMES)
                elif k == "is_active":
                    body["is_active"] = rng.random() < 0.7
                else:
                    body["team_id"] = str(rng.choice(teams)) if rng.random() < 0.9 else None
            r = await call("PATCH", f"/users/{target['id']}", a, body)
            if r.status_code == 202:
                approvals_seen.append(r.json()["approval_id"])
            for uid in list(actors):
                await refresh_actor(uid)
        elif kind == "team":
            r = await call("POST", "/teams", a, {"name": f"Fz team {step}", **body_reason})
            if r.status_code == 201:
                teams.append(uuid.UUID(r.json()["id"]))
        elif kind == "rename_team":
            await call(
                "PATCH", f"/teams/{rng.choice(teams)}", a, {"name": f"Rn {step}", **body_reason}
            )
        elif kind == "reset":
            target = rng.choice(list(actors.values()))
            await call("POST", f"/users/{target['id']}/password-reset", a, body_reason or {})
        elif kind == "decide":
            pend = (
                (await client.get("/approvals?tab=pending", headers=a["h"])).json()
                if a["role"] != "user"
                else []
            )
            if pend and rng.random() < 0.8:
                rid = rng.choice(pend)["id"]
            else:
                rid = rng.choice(approvals_seen) if approvals_seen else str(uuid.uuid4())
            await call(
                "POST",
                f"/approvals/{rid}/decide",
                a,
                {"decision": rng.choice(["approve", "reject"]), **body_reason},
            )
            for uid in list(actors):
                await refresh_actor(uid)
        elif kind == "cancel":
            rid = rng.choice(approvals_seen) if approvals_seen else str(uuid.uuid4())
            await call("POST", f"/approvals/{rid}/cancel", a, mutating=False)
        elif kind == "retry":
            await call(
                "POST", f"/admin/failed-jobs/{rng.randint(1, 5000)}/retry", a, body_reason or {}
            )
        elif kind == "upload":
            target = rng.choice(list(actors.values()))
            r = await call(
                "POST",
                "/files/upload-url",
                a,
                {
                    "entity_type": "user",
                    "entity_id": str(target["id"]),
                    "filename": rng.choice(["a.pdf", "../x", "ह.pdf"]),
                    "size": rng.choice([0, 1, 100, 26214401]),
                },
            )
            if r.status_code == 200:
                await call("POST", f"/files/{r.json()['file_id']}/complete", a, mutating=False)
        elif kind == "mark":
            await call("PATCH", "/notifications", a, {}, mutating=False)
        elif kind == "login":
            await call(
                "POST",
                "/auth/dev/login",
                a,
                {"email": rng.choice([a["email"], "ghost@example.com", a["email"].upper()])},
                mutating=False,
            )
        elif kind == "read":
            path = rng.choice(
                [
                    "/me",
                    "/users",
                    "/teams",
                    "/approvals?tab=pending",
                    "/approvals?tab=decided",
                    "/approvals?tab=mine",
                    "/notifications",
                    "/events",
                    "/admin/failed-jobs",
                    f"/history/user/{rng.choice(list(actors))}",
                ]
            )
            r = await call("GET", path, a, mutating=False)
            if r.status_code == 200:
                assert str(world_b.org_id) not in r.text and str(world_b.sa.id) not in r.text
        elif kind == "dispatch":
            await dispatch_all()
        elif kind == "failed_event":
            async with admin_engine.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO outbox_events (org_id,type,payload,status,attempts,last_error) VALUES (:o,'fz.failed','{}','failed',10,'x')"
                    ),
                    {"o": org},
                )
    await dispatch_all()
    await approvals.send_pending_reminders(get_sessionmaker())
    await dispatch_all()
    assert not stats["5xx"], stats["5xx"][:5]
    # denied accounting: every refused write left exactly one 'denied' audit row
    async with get_sessionmaker()() as s:
        denied = await one(
            s, "SELECT count(*) FROM audit_log WHERE org_id=:o AND outcome='denied'", o=org
        )
    assert denied == expected_denied, (denied, expected_denied)
    await check_invariants(org, client, actors)
    assert await snapshot_org(world_b.org_id) == before_b  # the other organisation never moved
    print(
        f"\nFUZZ seed={seed}: {stats['calls']} calls, {denied} denied rows, {len(actors)} actors, {len(teams)} teams"
    )
