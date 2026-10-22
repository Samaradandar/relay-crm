# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Users and teams through the real HTTP API: allowed and denied for every role."""

import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.platform.audit.models import AuditLog
from tests.helpers import dispatch_all, get_user_row, headers


def new_email() -> str:
    return f"new.{uuid.uuid4().hex[:8]}@example.com"


async def audit_rows(entity_id, key=None):
    async with get_sessionmaker()() as s:
        stmt = select(AuditLog).where(AuditLog.entity_id == str(entity_id)).order_by(AuditLog.seq)
        rows = list((await s.execute(stmt)).scalars())
    return [r for r in rows if key is None or r.action_key == key]


async def test_me_for_every_role(client, world):
    for actor in world.all_actors:
        r = await client.get("/me", headers=headers(actor))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == actor.role and body["id"] == str(actor.id)
        perms = {p["key"]: p for p in body["permissions"]}
        assert perms["leaderboard.view"]["scope"] == "all"
        assert perms["pool.view"]["scope"] == (
            "none" if actor.role == "user" else perms["pool.view"]["scope"]
        )


async def test_unauthenticated_and_unknown_users(client, world):
    assert (await client.get("/me")).status_code == 401
    assert (await client.get("/me", headers={"Authorization": "Bearer nope"})).status_code == 401
    stranger = await client.post("/auth/dev/login", json={"email": "nobody@example.com"})
    assert stranger.status_code == 401 and "No CRM account" in stranger.json()["message"]


async def test_user_directory_scope(client, world):
    def emails(r):
        return {u["email"] for u in r.json()}

    r = await client.get("/users", headers=headers(world.north1))
    assert emails(r) == {a.email for a in (world.north1, world.north2, world.tl_north)}
    r = await client.get("/users", headers=headers(world.tl_south))
    assert emails(r) == {a.email for a in (world.south1, world.south2, world.tl_south)}
    r = await client.get("/users", headers=headers(world.admin))
    assert world.sa.email in emails(r) and world.south1.email in emails(r)


# ----------------------------------------------------------------------------- creating users
async def test_create_user_by_role(client, world):
    body = {
        "name": "New Rep",
        "email": new_email(),
        "role": "user",
        "team_id": str(world.north_team),
    }
    # a User cannot
    r = await client.post("/users", json=body, headers=headers(world.north1))
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"
    # an Admin must give a reason on every change
    r = await client.post("/users", json=body, headers=headers(world.admin))
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await client.post("/users", json={**body, "reason": "  "}, headers=headers(world.admin))
    assert r.status_code == 422
    r = await client.post(
        "/users", json={**body, "reason": "new hire"}, headers=headers(world.admin)
    )
    assert r.status_code == 201, r.text
    created = r.json()
    rows = await audit_rows(created["id"], "user.manage")
    assert len(rows) == 1 and rows[0].reason == "new hire" and rows[0].actor_id == world.admin.id
    # duplicate email
    r = await client.post("/users", json={**body, "reason": "again"}, headers=headers(world.admin))
    assert r.status_code == 409
    # Super admin: reason optional
    body2 = {**body, "email": new_email()}
    r = await client.post("/users", json=body2, headers=headers(world.sa))
    assert r.status_code == 201
    assert (await audit_rows(r.json()["id"], "user.manage"))[0].reason is None


async def test_only_super_admin_manages_admins(client, world):
    body = {"name": "Boss", "email": new_email(), "role": "admin", "reason": "promote"}
    assert (await client.post("/users", json=body, headers=headers(world.admin))).status_code == 403
    assert (
        await client.post("/users", json=body, headers=headers(world.tl_north))
    ).status_code == 403
    r = await client.post("/users", json=body, headers=headers(world.sa))
    assert r.status_code == 201 and r.json()["team_id"] is None
    # an Admin cannot change or deactivate an Admin or Super admin either
    for target in (world.admin, world.sa):
        r = await client.patch(
            f"/users/{target.id}",
            json={"is_active": False, "reason": "x"},
            headers=headers(world.admin),
        )
        assert r.status_code == 403


async def test_team_leader_requests_a_user(client, world):
    body = {
        "name": "Req Rep",
        "email": new_email(),
        "role": "user",
        "team_id": str(world.north_team),
    }
    # needs a reason to request
    r = await client.post("/users", json=body, headers=headers(world.tl_north))
    assert r.status_code == 422
    r = await client.post(
        "/users", json={**body, "reason": "need a rep"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 202 and r.json()["code"] == "approval_requested"
    approval_id = r.json()["approval_id"]
    # nothing was created yet
    r = await client.get("/users", headers=headers(world.admin))
    assert body["email"] not in {u["email"] for u in r.json()}
    # a TL may only request for their own team, and not an Admin
    other = {**body, "email": new_email(), "team_id": str(world.south_team), "reason": "x"}
    assert (
        await client.post("/users", json=other, headers=headers(world.tl_north))
    ).status_code == 403
    adm = {**body, "email": new_email(), "role": "admin", "reason": "x"}
    assert (
        await client.post("/users", json=adm, headers=headers(world.tl_north))
    ).status_code == 403
    # an Admin approves (reason required), and the user appears
    r = await client.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=headers(world.admin),
    )
    assert r.status_code == 422
    r = await client.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": "approve", "reason": "ok"}, headers=headers(world.admin),
    )  # fmt: skip
    assert r.status_code == 200 and r.json()["status"] == "approved"
    users = (await client.get("/users", headers=headers(world.admin))).json()
    created = next(u for u in users if u["email"] == body["email"])
    rows = await audit_rows(created["id"], "user.manage")
    assert len(rows) == 1
    assert rows[0].actor_id == world.admin.id and rows[0].on_behalf_of == world.tl_north.id
    assert str(rows[0].approval_id) == approval_id


# ----------------------------------------------------------------------------- updating users
async def test_role_change_deactivate_and_move(client, world):
    r = await client.post(
        "/users",
        json={"name": "Mover", "email": new_email(), "role": "user",
              "team_id": str(world.north_team), "reason": "hire"},
        headers=headers(world.admin),
    )  # fmt: skip
    uid = r.json()["id"]
    # a User cannot edit anyone
    r = await client.patch(f"/users/{uid}", json={"name": "X"}, headers=headers(world.north1))
    assert r.status_code == 403
    # move teams: Admin needs a reason; TL cannot
    r = await client.patch(
        f"/users/{uid}", json={"team_id": str(world.south_team)}, headers=headers(world.tl_north)
    )
    assert r.status_code == 403
    r = await client.patch(
        f"/users/{uid}", json={"team_id": str(world.south_team)}, headers=headers(world.admin)
    )
    assert r.status_code == 422
    r = await client.patch(
        f"/users/{uid}", json={"team_id": str(world.south_team), "reason": "re-org"},
        headers=headers(world.admin),
    )  # fmt: skip
    assert r.status_code == 200 and r.json()["team_id"] == str(world.south_team)
    async with get_sessionmaker()() as s:
        hist = (
            await s.execute(
                text(
                    "select team_id, ended_at from team_memberships where user_id=:u order by started_at"
                ),
                {"u": uuid.UUID(uid)},
            )
        ).all()
    assert len(hist) == 2 and hist[0][1] is not None and hist[1][1] is None
    # deactivate then reactivate
    r = await client.patch(
        f"/users/{uid}", json={"is_active": False, "reason": "left"}, headers=headers(world.admin)
    )
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = await client.patch(
        f"/users/{uid}", json={"is_active": True, "reason": "back"}, headers=headers(world.admin)
    )
    assert r.json()["is_active"] is True
    # a TL's deactivation and change_role become requests (BUG-P1-18), only within user/team_leader
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"is_active": False, "reason": "x"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 202
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"role": "admin", "reason": "x"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 403
    r = await client.patch(
        f"/users/{world.south1.id}", json={"role": "team_leader", "reason": "x"},
        headers=headers(world.tl_north),
    )  # fmt: skip
    assert r.status_code == 403  # out of scope: another team's user
    r = await client.patch(
        f"/users/{world.north2.id}", json={"role": "team_leader", "reason": "promote"},
        headers=headers(world.tl_north),
    )  # fmt: skip
    assert r.status_code == 403  # only an Admin or Super admin appoints a Team Leader
    r = await client.post(
        "/users",
        json={
            "name": "New Lead", "email": "newlead@example.com", "role": "team_leader",
            "team_id": str(world.north_team), "reason": "need one",
        },
        headers=headers(world.tl_north),
    )  # fmt: skip
    assert r.status_code == 403
    r = await client.post(
        "/users",
        json={
            "name": "New Rep", "email": "newrep@example.com", "role": "user",
            "team_id": str(world.north_team), "reason": "need a rep",
        },
        headers=headers(world.tl_north),
    )  # fmt: skip
    assert r.status_code == 202  # a rep is still only a request


async def test_user_must_have_team_for_a_team_leader_and_team_has_one_leader(client, world):
    # north already has a leader
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"role": "team_leader", "reason": "x"},
        headers=headers(world.admin),
    )
    assert r.status_code == 409
    # a Team Leader cannot be moved without changing role
    r = await client.patch(
        f"/users/{world.tl_north.id}", json={"team_id": str(world.south_team), "reason": "x"},
        headers=headers(world.admin),
    )  # fmt: skip
    assert r.status_code == 409
    # demote leader frees the team, then promote another
    r = await client.patch(
        f"/users/{world.tl_north.id}", json={"role": "user", "reason": "step down"},
        headers=headers(world.admin),
    )  # fmt: skip
    assert r.status_code == 200
    r = await client.patch(
        f"/users/{world.north2.id}", json={"role": "team_leader", "reason": "new lead"},
        headers=headers(world.admin),
    )  # fmt: skip
    assert r.status_code == 200
    teams = (await client.get("/teams", headers=headers(world.admin))).json()
    north = next(t for t in teams if t["id"] == str(world.north_team))
    assert north["leader_id"] == str(world.north2.id)


async def test_last_super_admin_cannot_be_deactivated_or_demoted(client, world):
    for patch in ({"is_active": False}, {"role": "admin"}):
        r = await client.patch(
            f"/users/{world.sa.id}", json={**patch, "reason": "x"}, headers=headers(world.sa)
        )
        assert r.status_code == 409, r.text
    # with a second SA it is allowed, then the remaining one is protected again
    r = await client.post(
        "/users", json={"name": "SA2", "email": new_email(), "role": "super_admin"},
        headers=headers(world.sa),
    )  # fmt: skip
    sa2 = r.json()["id"]
    r = await client.patch(f"/users/{sa2}", json={"is_active": False}, headers=headers(world.sa))
    assert r.status_code == 200
    r = await client.patch(
        f"/users/{world.sa.id}", json={"is_active": False}, headers=headers(world.sa)
    )
    assert r.status_code == 409


async def test_two_simultaneous_demotions_cannot_remove_all_super_admins(client, world):
    import asyncio

    r = await client.post(
        "/users", json={"name": "SA2", "email": new_email(), "role": "super_admin"},
        headers=headers(world.sa),
    )  # fmt: skip
    sa2_id, sa2_email = r.json()["id"], r.json()["email"]
    from app.platform.context import Actor

    sa2 = Actor(id=uuid.UUID(sa2_id), org_id=world.org_id, role="super_admin", team_id=None,
                name="SA2", email=sa2_email)  # fmt: skip
    a = client.patch(f"/users/{sa2_id}", json={"role": "admin"}, headers=headers(world.sa))
    b = client.patch(f"/users/{world.sa.id}", json={"role": "admin"}, headers=headers(sa2))
    ra, rb = await asyncio.gather(a, b)
    codes = sorted([ra.status_code, rb.status_code])
    assert codes[0] == 200 and codes[1] in (403, 409)  # 403 if the loser was already demoted
    async with get_sessionmaker()() as s:
        left = (
            await s.execute(
                text(
                    "select count(*) from users where org_id=:o and role='super_admin' and is_active"
                ),
                {"o": world.org_id},
            )
        ).scalar_one()
    assert left == 1


# ----------------------------------------------------------------------------- login rules
async def test_user_without_team_cannot_log_in_and_deactivated_token_stops(client, world):
    r = await client.post(
        "/users", json={"name": "No Team", "email": new_email(), "role": "user", "reason": "hire"},
        headers=headers(world.admin),
    )  # fmt: skip
    email, uid = r.json()["email"], r.json()["id"]
    r = await client.post("/auth/dev/login", json={"email": email})
    assert r.status_code == 403 and "not in a team" in r.json()["message"]
    # put them in a team: login works; then deactivate: the SAME token stops working immediately
    await client.patch(
        f"/users/{uid}",
        json={"team_id": str(world.south_team), "reason": "assign"},
        headers=headers(world.admin),
    )
    r = await client.post("/auth/dev/login", json={"email": email})
    assert r.status_code == 200
    token = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await client.get("/me", headers=token)).status_code == 200
    await client.patch(
        f"/users/{uid}", json={"is_active": False, "reason": "left"}, headers=headers(world.admin)
    )
    r = await client.get("/me", headers=token)
    assert r.status_code == 401 and "deactivated" in r.json()["message"]
    assert (await get_user_row(uuid.UUID(uid))).last_login_at is not None


# ----------------------------------------------------------------------------- teams
async def test_teams(client, world):
    assert (
        await client.post("/teams", json={"name": "T"}, headers=headers(world.north1))
    ).status_code == 403
    assert (
        await client.post("/teams", json={"name": "T"}, headers=headers(world.tl_north))
    ).status_code == 403
    assert (
        await client.post("/teams", json={"name": "East"}, headers=headers(world.admin))
    ).status_code == 422
    r = await client.post(
        "/teams", json={"name": "East", "reason": "new region"}, headers=headers(world.admin)
    )
    assert r.status_code == 201
    tid = r.json()["id"]
    assert (
        await client.post(
            "/teams", json={"name": "east", "reason": "dup"}, headers=headers(world.admin)
        )
    ).status_code == 409
    r = await client.patch(
        f"/teams/{tid}",
        json={"name": "East Zone", "reason": "rename"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200 and r.json()["name"] == "East Zone"
    rows = await audit_rows(tid, "team.manage")
    assert [x.reason for x in rows] == ["new region", "rename"]
    # visibility: a TL lists every team name (to give a deal away, BUG-P2-40) but sees the leader
    # and head-count of their own team only; a rep sees only their own team; Admin sees all
    tl_view = (await client.get("/teams", headers=headers(world.tl_north))).json()
    assert len(tl_view) >= 3
    assert all(x["leader_id"] is None for x in tl_view if x["id"] != str(world.north_team))
    assert len((await client.get("/teams", headers=headers(world.north1))).json()) == 1
    assert len((await client.get("/teams", headers=headers(world.admin))).json()) >= 3


# ----------------------------------------------------------------------------- password reset
async def test_password_reset_rules(client, world):
    provider = __import__("app.core.security", fromlist=["x"]).get_auth_provider()
    provider.sent_resets.clear()
    # own reset: any role (Admin needs a reason because the matrix class is routine for Admin)
    assert (
        await client.post(
            f"/users/{world.north1.id}/password-reset", json={}, headers=headers(world.north1)
        )
    ).status_code == 204
    assert (
        await client.post(
            f"/users/{world.tl_north.id}/password-reset", json={}, headers=headers(world.tl_north)
        )
    ).status_code == 204
    # reset someone else: User and TL cannot; Admin needs a reason; SA anyone
    for actor in (world.north1, world.tl_north):
        r = await client.post(
            f"/users/{world.north2.id}/password-reset", json={"reason": "x"}, headers=headers(actor)
        )
        assert r.status_code == 403
    r = await client.post(
        f"/users/{world.north2.id}/password-reset", json={}, headers=headers(world.admin)
    )
    assert r.status_code == 422
    r = await client.post(
        f"/users/{world.north2.id}/password-reset",
        json={"reason": "locked out"},
        headers=headers(world.admin),
    )
    assert r.status_code == 204
    assert (
        await client.post(
            f"/users/{world.south1.id}/password-reset", json={}, headers=headers(world.sa)
        )
    ).status_code == 204
    kinds = [k for k, _ in provider.sent_resets]
    assert kinds.count("self") == 2 and kinds.count("admin") == 2


async def test_denied_attempts_are_audited(client, world):
    r = await client.post(
        "/users",
        json={"name": "X", "email": new_email(), "role": "user"},
        headers=headers(world.north1),
    )
    assert r.status_code == 403
    async with get_sessionmaker()() as s:
        rows = list(
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.actor_id == world.north1.id, AuditLog.outcome == "denied"
                    )
                )
            ).scalars()
        )
    assert rows and rows[0].action_key == "user.manage"
    await dispatch_all()


@pytest.mark.parametrize("path", ["/users", "/teams", "/approvals", "/notifications", "/events"])
async def test_every_get_endpoint_requires_login(client, path):
    assert (await client.get(path)).status_code == 401
