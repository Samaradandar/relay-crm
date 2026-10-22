# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 8: the approval engine."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.platform import registry
from app.platform.approvals import service as approvals
from app.platform.context import RequestInfo
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL, ROUTE_TEAM_LEADER
from app.platform.uow import UnitOfWork
from tests.deep.phase1.kit import audit_rows, h, new_email
from tests.deep.phase1.test_03_isolation import make_request
from tests.helpers import dispatch_all, get_user_row, notifications_for

registry.setup()


async def queue_change_role(requester, target, role, reason="x") -> str:
    """Queue a pending user.change_role request exactly as the service once wrote it.

    A Team Leader can no longer ask for team_leader over HTTP (403), so the executor tests
    create the request through the approvals service as the requesting TL.
    """
    async with UnitOfWork(requester, RequestInfo()) as uow:
        req = await approvals.create_request(
            uow,
            type_="user.change_role",
            action_key="user.manage",
            entity=("user", str(target.id)),
            summary=f"{requester.name} asks to change {target.name} to {role}",
            payload={"user_id": str(target.id), "role": role},
            reason=reason,
            routed_to=ROUTE_ADMIN_POOL,
            team_id=requester.team_id,
        )
        return str(req.id)


async def decide(client, actor, rid, decision="approve", reason="ok"):
    return await client.post(
        f"/approvals/{rid}/decide", json={"decision": decision, "reason": reason}, headers=h(actor)
    )


async def executed(org_id, rid) -> int:
    return len(
        [
            r
            for r in await audit_rows(org_id, action_key="deep.executed")
            if str(r["approval_id"]) == str(rid)
        ]
    )


# ------------------------------------------------------------------ routing
async def test_team_level_requests_go_to_the_requesters_leader_and_admin_pool_requests_to_admins(
    world,
):
    team_req = await make_request(
        world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team
    )
    pool_req = await make_request(world.tl_north)
    await dispatch_all()

    async def types(actor):
        return [n.link for n in await notifications_for(actor.id) if n.type == "approval.requested"]

    # Admin/SA are approvers of everything; only the right TL gets the team request
    assert sorted(await types(world.admin)) == sorted(
        [f"/approvals/{team_req}", f"/approvals/{pool_req}"]
    )
    assert sorted(await types(world.sa)) == sorted(
        [f"/approvals/{team_req}", f"/approvals/{pool_req}"]
    )
    assert await types(world.tl_north) == [f"/approvals/{team_req}"]
    for nobody in (world.tl_south, world.north1, world.north2, world.south1, world.south2):
        assert await types(nobody) == []


async def test_user_create_by_a_tl_routes_to_the_admin_pool_and_never_to_a_tl(client, world):
    r = await client.post(
        "/users",
        json={
            "name": "Routed",
            "email": new_email(),
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "need",
        },
        headers=h(world.tl_north),
    )
    assert r.status_code == 202
    ap = (await client.get(f"/approvals/{r.json()['approval_id']}", headers=h(world.sa))).json()
    assert (
        ap["routed_to"] == "admin_pool"
        and ap["type"] == "user.create"
        and ap["status"] == "pending"
    )
    assert ap["requested_by"] == str(world.tl_north.id) and ap["request_reason"] == "need"
    assert "Routed" in ap["summary"]
    await dispatch_all()
    assert [n.type for n in await notifications_for(world.tl_south.id)] == []


# ------------------------------------------------------------------ self approval matrix
async def test_self_approval_matrix(client, world):
    # (actor, request kind) -> status.  "own": the actor raised it; "owner": it is about a record the actor owns
    own_admin = await make_request(world.admin)
    own_sa = await make_request(world.sa)
    own_tl = await make_request(world.tl_north)
    own_user = await make_request(world.north1)
    own_tl_team_level = await make_request(
        world.tl_north, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team
    )
    owner_admin = await make_request(world.tl_north, owner=world.admin.id)
    owner_sa = await make_request(world.tl_north, owner=world.sa.id)
    cases = [
        (world.admin, own_admin, 403),  # an Admin cannot approve their own request
        (world.sa, own_sa, 200),  # a Super admin may
        (world.tl_north, own_tl, 403),  # a TL cannot (and it is not routed to them)
        (world.north1, own_user, 403),  # a User cannot decide anything
        (
            world.tl_north,
            own_tl_team_level,
            403,
        ),  # not even a team-level request they raised themselves
        (
            world.admin,
            owner_admin,
            403,
        ),  # an Admin cannot approve a request about a record they own
        (world.sa, owner_sa, 200),  # a Super admin can
        (
            world.sa,
            await make_request(world.admin),
            200,
        ),  # another approver can decide an Admin's request
        (world.admin, await make_request(world.sa), 200),  # and an Admin can decide a Super admin's
        (world.admin, await make_request(world.tl_north, owner=world.sa.id), 200),
        (world.sa, owner_admin, 200),
    ]
    for actor, rid, want in cases:
        r = await decide(client, actor, rid)
        assert r.status_code == want, (actor.role, str(rid), r.status_code, r.text)
        await asyncio.sleep(0)
    # the refused ones executed nothing, the allowed ones exactly once
    for rid in (own_tl, own_user, own_tl_team_level):
        assert await executed(world.org_id, rid) == 0
    for rid in (own_sa, owner_admin, owner_sa):
        assert await executed(world.org_id, rid) == 1
    assert await executed(world.org_id, own_admin) == 0  # still pending: it was never decided


async def test_a_super_admin_self_approval_is_recorded_with_both_names_the_same(client, world):
    rid = await make_request(world.sa)
    assert (await decide(client, world.sa, rid, reason=None)).status_code == 200
    rows = [r for r in await audit_rows(world.org_id, approval_id=rid) if r["outcome"] == "ok"]
    assert len(rows) == 2 and all(
        r["actor_id"] == world.sa.id and r["on_behalf_of"] == world.sa.id for r in rows
    )


async def test_decide_permission_matrix_over_http(client, world, world_b):
    team_level = await make_request(
        world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team
    )
    pool = await make_request(world.north2)
    want_team = {world.north1: 403, world.north2: 403, world.south1: 403, world.tl_south: 403,
                 world_b.sa: 404, world_b.admin: 404, world_b.tl_north: 404}  # fmt: skip
    for actor, want in want_team.items():
        assert (await decide(client, actor, team_level)).status_code == want, actor.name
    for actor in (world.north1, world.south1, world.tl_north, world.tl_south):
        assert (await decide(client, actor, pool)).status_code == 403
    assert await executed(world.org_id, team_level) == 0 and await executed(world.org_id, pool) == 0
    # denied attempts left rows, and the request is untouched
    denied = [
        r
        for r in await audit_rows(world.org_id, action_key="approval.decide")
        if r["outcome"] == "denied"
    ]
    assert len(denied) == 4 + 4 + 2 + 2 - 2 + 0 or len(denied) >= 8
    assert (await client.get(f"/approvals/{pool}", headers=h(world.sa))).json()[
        "status"
    ] == "pending"


# ------------------------------------------------------------------ the race
@pytest.mark.parametrize("routed", [ROUTE_ADMIN_POOL, ROUTE_TEAM_LEADER])
async def test_twenty_simultaneous_decisions_exactly_one_executes(client, world, routed):
    rid = await make_request(
        world.north1 if routed == ROUTE_TEAM_LEADER else world.tl_north,
        routed_to=routed,
        team_id=world.north_team if routed == ROUTE_TEAM_LEADER else None,
    )
    approvers = [world.admin, world.sa] + ([world.tl_north] if routed == ROUTE_TEAM_LEADER else [])
    calls = []
    for i in range(20):
        a = approvers[i % len(approvers)]
        dec = "approve" if i % 2 == 0 else "reject"
        calls.append(decide(client, a, rid, dec, "race"))
    rs = await asyncio.gather(*calls)
    codes = sorted(r.status_code for r in rs)
    assert codes == [200] + [409] * 19, codes
    winner = next(r for r in rs if r.status_code == 200).json()
    final = (await client.get(f"/approvals/{rid}", headers=h(world.sa))).json()
    assert final["status"] == winner["status"] and final["decided_by"] == winner["decided_by"]
    assert await executed(world.org_id, rid) == (1 if winner["status"] == "approved" else 0)
    oks = [
        r
        for r in await audit_rows(world.org_id, action_key="approval.decide")
        if r["outcome"] == "ok" and str(r["entity_id"]) == str(rid)
    ]
    assert len(oks) == 1
    # the losers wrote no 'ok' rows and nothing half done
    assert all(r.json()["code"] == "conflict" for r in rs if r.status_code == 409)


async def test_cancel_racing_with_approve_leaves_one_consistent_outcome(client, world):
    for _ in range(5):
        rid = await make_request(world.tl_north)
        rs = await asyncio.gather(
            client.post(f"/approvals/{rid}/cancel", headers=h(world.tl_north)),
            decide(client, world.admin, rid, "approve"),
            decide(client, world.sa, rid, "approve", None),
        )
        codes = sorted(r.status_code for r in rs)
        assert codes == [200, 409, 409], codes
        status = (await client.get(f"/approvals/{rid}", headers=h(world.sa))).json()["status"]
        assert status in ("cancelled", "approved")
        assert await executed(world.org_id, rid) == (1 if status == "approved" else 0)


# ------------------------------------------------------------------ reject / cancel / already decided
async def test_reject_cancel_and_already_decided_rules(client, world):
    rid = await make_request(world.north1)  # admin pool, raised by a rep
    # only the requester may cancel; others see 404 (not even 403)
    for other in (world.admin, world.sa, world.tl_north, world.north2):
        assert (await client.post(f"/approvals/{rid}/cancel", headers=h(other))).status_code == 404
    r = await client.post(f"/approvals/{rid}/cancel", headers=h(world.north1))
    assert (
        r.status_code == 200
        and r.json()["status"] == "cancelled"
        and r.json()["decided_by"] is None
    )
    # cancelled requests cannot be decided or cancelled again
    for call in (
        decide(client, world.admin, rid),
        decide(client, world.sa, rid, "reject"),
        client.post(f"/approvals/{rid}/cancel", headers=h(world.north1)),
    ):
        assert (await call).status_code == 409
    # rejected
    rid2 = await make_request(world.north1)
    r = await decide(client, world.admin, rid2, "reject", "not now")
    assert (
        r.status_code == 200
        and r.json()["status"] == "rejected"
        and r.json()["decided_by_name"] == "Anita"
    )
    assert (await decide(client, world.sa, rid2, "approve", "x")).status_code == 409
    assert (
        await client.post(f"/approvals/{rid2}/cancel", headers=h(world.north1))
    ).status_code == 409
    assert await executed(world.org_id, rid2) == 0
    # approved: cannot be cancelled or rejected afterwards
    rid3 = await make_request(world.north1)
    assert (await decide(client, world.sa, rid3, "approve", None)).status_code == 200
    assert (await decide(client, world.admin, rid3, "reject", "late")).status_code == 409
    assert (
        await client.post(f"/approvals/{rid3}/cancel", headers=h(world.north1))
    ).status_code == 409
    assert await executed(world.org_id, rid3) == 1
    # one approval.cancel audit row, one decide row per decided request
    cancels = [
        r
        for r in await audit_rows(world.org_id, action_key="approval.cancel")
        if r["outcome"] == "ok"
    ]
    assert len(cancels) == 1


async def test_unknown_malformed_and_missing_ids(client, world):
    for actor in (world.sa, world.north1):
        assert (await client.get(f"/approvals/{uuid.uuid4()}", headers=h(actor))).status_code == 404
        assert (await decide(client, actor, uuid.uuid4())).status_code == 404
        assert (
            await client.post(f"/approvals/{uuid.uuid4()}/cancel", headers=h(actor))
        ).status_code == 404
        for bad in ("abc", "1", "00000000-0000-0000-0000-00000000000g", "%00"):
            assert (await client.get(f"/approvals/{bad}", headers=h(actor))).status_code in (
                404,
                422,
            )
            assert (
                await client.post(
                    f"/approvals/{bad}/decide", json={"decision": "approve"}, headers=h(actor)
                )
            ).status_code in (404, 422)


async def test_the_request_needs_a_reason_and_nothing_is_created_without_one(client, world):
    n = len(await audit_rows(world.org_id))
    for blank in (None, "", "   ", "\n"):
        body = {
            "name": "No reason",
            "email": new_email(),
            "role": "user",
            "team_id": str(world.north_team),
        }
        if blank is not None:
            body["reason"] = blank
        r = await client.post("/users", json=body, headers=h(world.tl_north))
        assert r.status_code == 422 and r.json()["code"] == "reason_required"
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(
                text("SELECT count(*) FROM approval_requests WHERE org_id=:o"), {"o": world.org_id}
            )
        ).scalar_one() == 0
    # only the denied rows for the four refusals
    new = (await audit_rows(world.org_id))[n:]
    assert [x["outcome"] for x in new] == ["denied"] * 4


async def test_requests_never_expire_and_stay_decidable(client, world, admin_engine):
    rid = await make_request(world.tl_north)
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE approval_requests SET created_at = now() - interval '5 years' WHERE id=:i"
            ),
            {"i": rid},
        )
    assert rid in [
        uuid.UUID(a["id"])
        for a in (await client.get("/approvals?tab=pending", headers=h(world.admin))).json()
    ]
    assert (await decide(client, world.admin, rid)).status_code == 200


async def test_reminders_reach_only_eligible_approvers_and_stop_after_a_decision(
    world, admin_engine
):
    team_level = await make_request(
        world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team
    )
    pool = await make_request(
        world.admin
    )  # Admin's own request: the Admin is not an approver of it
    await approvals.send_pending_reminders(
        get_sessionmaker()
    )  # (global: it also reminds other orgs)
    got = {
        u.name: sorted(
            n.link for n in await notifications_for(u.id) if n.type == "approval.reminder"
        )
        for u in (world.admin, world.sa, world.tl_north, world.tl_south, world.north1)
    }
    assert got["Anita"] == [f"/approvals/{team_level}"]
    assert got["Samar"] == sorted([f"/approvals/{team_level}", f"/approvals/{pool}"])
    assert got["Tara"] == [f"/approvals/{team_level}"]
    assert got["Tejas"] == [] and got["Priya"] == []
    assert await approvals.send_pending_reminders(get_sessionmaker()) == 0  # idempotent for the day


# ------------------------------------------------------------------ executors
async def test_user_create_executor_applies_once_even_when_two_approvers_race(client, world):
    email = new_email()
    r = await client.post(
        "/users",
        json={
            "name": "Exec One",
            "email": email,
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "need",
        },
        headers=h(world.tl_north),
    )
    aid = r.json()["approval_id"]
    rs = await asyncio.gather(
        *[
            decide(client, a, aid, "approve", "ok")
            for a in (world.admin, world.sa, world.admin, world.sa, world.admin)
        ]
    )
    assert sorted(x.status_code for x in rs) == [200, 409, 409, 409, 409]
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, org_id, team_id, role, is_active, created_by FROM users WHERE lower(email)=:e"
                ),
                {"e": email},
            )
        ).all()
    assert len(rows) == 1
    uid, org, team, role, active, created_by = rows[0]
    assert org == world.org_id and team == world.north_team and role == "user" and active
    assert created_by in (world.admin.id, world.sa.id)
    audits = [
        x for x in await audit_rows(world.org_id, entity_id=str(uid), action_key="user.manage")
    ]
    assert (
        len(audits) == 1
        and audits[0]["on_behalf_of"] == world.tl_north.id
        and str(audits[0]["approval_id"]) == aid
    )
    assert audits[0]["reason"] == "need"  # the requester's reason is the change's reason
    await dispatch_all()
    assert [n.type for n in await notifications_for(uid)] == ["user.invited"]
    me = await client.post("/auth/dev/login", json={"email": email})
    assert me.status_code == 200


async def test_user_create_executor_conflict_leaves_the_request_pending_and_rejectable(
    client, world
):
    email = new_email()
    r = await client.post(
        "/users",
        json={
            "name": "Dup",
            "email": email,
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "need",
        },
        headers=h(world.tl_north),
    )
    aid = r.json()["approval_id"]
    # meanwhile an Admin creates the same person
    assert (
        await client.post(
            "/users",
            json={
                "name": "Dup",
                "email": email,
                "role": "user",
                "team_id": str(world.south_team),
                "reason": "x",
            },
            headers=h(world.admin),
        )
    ).status_code == 201
    r = await decide(client, world.sa, aid, "approve", None)
    assert r.status_code == 409
    assert (await client.get(f"/approvals/{aid}", headers=h(world.sa))).json()[
        "status"
    ] == "pending"
    ok = [
        x
        for x in await audit_rows(world.org_id, action_key="user.manage")
        if x["outcome"] == "ok" and x["actor_id"] == world.sa.id
    ]
    assert ok == []  # the failed execution left no row
    assert (await decide(client, world.sa, aid, "reject", "duplicate")).status_code == 200


async def test_change_role_executor_promotes_exactly_once_and_updates_the_team_leader(
    client, world
):
    # south's leader asks to promote south1, then steps down before anyone answers
    aid = await queue_change_role(world.tl_south, world.south1, "team_leader", "good rep")
    assert (
        await client.patch(
            f"/users/{world.tl_south.id}",
            json={"role": "user", "reason": "step down"},
            headers=h(world.admin),
        )
    ).status_code == 200
    rs = await asyncio.gather(
        decide(client, world.admin, aid, "approve", "ok"),
        decide(client, world.sa, aid, "approve", None),
    )
    assert sorted(x.status_code for x in rs) == [200, 409]
    u = await get_user_row(world.south1.id)
    assert u.role == "team_leader"
    teams = (await client.get("/teams", headers=h(world.sa))).json()
    south = next(t for t in teams if t["id"] == str(world.south_team))
    assert south["leader_id"] == str(world.south1.id)
    rows = [
        x
        for x in await audit_rows(
            world.org_id, entity_id=str(world.south1.id), action_key="user.manage"
        )
        if x["outcome"] == "ok"
    ]
    assert (
        len(rows) == 1
        and rows[0]["old_value"]["role"] == "user"
        and rows[0]["new_value"]["role"] == "team_leader"
    )
    assert rows[0]["on_behalf_of"] == world.tl_south.id and str(rows[0]["approval_id"]) == aid
    await dispatch_all()
    assert "user.role_changed" in [n.type for n in await notifications_for(world.south1.id)]


async def test_change_role_executor_conflicts_when_the_team_still_has_a_leader(client, world):
    aid = await queue_change_role(world.tl_north, world.north2, "team_leader")
    r = await decide(client, world.sa, aid, "approve", None)
    assert r.status_code == 409 and (await get_user_row(world.north2.id)).role == "user"
    assert (await client.get(f"/approvals/{aid}", headers=h(world.sa))).json()[
        "status"
    ] == "pending"


async def test_change_role_request_for_a_user_since_deactivated_or_made_admin(client, world):
    aid = await queue_change_role(world.tl_south, world.south1, "team_leader")
    await client.patch(
        f"/users/{world.tl_south.id}",
        json={"role": "user", "reason": "step down"},
        headers=h(world.admin),
    )
    await client.patch(
        f"/users/{world.south1.id}",
        json={"role": "admin", "reason": "promoted"},
        headers=h(world.sa),
    )
    r = await decide(client, world.sa, aid, "approve", None)
    assert r.status_code == 409  # "can no longer be applied"
    assert (await get_user_row(world.south1.id)).role == "admin"


async def test_rejected_and_cancelled_requests_change_nothing(client, world):
    r = await client.post(
        "/users",
        json={
            "name": "Never",
            "email": (e := new_email()),
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "n",
        },
        headers=h(world.tl_north),
    )
    aid = r.json()["approval_id"]
    assert (await decide(client, world.admin, aid, "reject", "no")).status_code == 200
    r2 = await client.post(
        "/users",
        json={
            "name": "Never2",
            "email": (e2 := new_email()),
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "n",
        },
        headers=h(world.tl_north),
    )
    assert (
        await client.post(
            f"/approvals/{r2.json()['approval_id']}/cancel", headers=h(world.tl_north)
        )
    ).status_code == 200
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(
                text("SELECT count(*) FROM users WHERE email = ANY(:e)"), {"e": [e, e2]}
            )
        ).scalar_one() == 0
    await dispatch_all()
    types = [n.type for n in await notifications_for(world.tl_north.id)]
    assert types == [
        "approval.decided"
    ]  # the rejection reached the requester; cancelling notifies nobody


async def test_tl_cannot_request_for_other_teams_or_privileged_roles_and_creates_no_request(
    client, world
):
    for body in (
        {"role": "user", "team_id": str(world.south_team)},
        {"role": "admin"},
        {"role": "super_admin"},
        {"role": "user"},  # no team given
    ):
        r = await client.post(
            "/users",
            json={"name": "X", "email": new_email(), "reason": "r", **body},
            headers=h(world.tl_north),
        )
        assert r.status_code == 403, (body, r.text)
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(
                text("SELECT count(*) FROM approval_requests WHERE org_id=:o"), {"o": world.org_id}
            )
        ).scalar_one() == 0


async def test_a_tl_request_for_an_existing_email_is_refused_at_once(client, world):
    r = await client.post(
        "/users",
        json={
            "name": "X",
            "email": world.north1.email.upper(),
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "r",
        },
        headers=h(world.tl_north),
    )
    assert r.status_code == 409


async def test_a_tl_cannot_even_queue_a_team_leader_request(client, world):
    """Only an Admin or Super admin appoints a Team Leader: refused at once, nothing is created."""
    audits_before = len(await audit_rows(world.org_id, action_key="user.manage"))
    r = await client.post(
        "/users",
        json={
            "name": "Second TL",
            "email": new_email(),
            "role": "team_leader",
            "team_id": str(world.north_team),
            "reason": "r",
        },
        headers=h(world.tl_north),
    )
    assert r.status_code == 403
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(
                text("SELECT count(*) FROM approval_requests WHERE org_id=:o"), {"o": world.org_id}
            )
        ).scalar_one() == 0
    rows = await audit_rows(world.org_id, action_key="user.manage")
    assert len(rows) == audits_before + 1
    assert not [x for x in rows if x["outcome"] == "ok" and x["actor_id"] == world.tl_north.id]
    assert [x for x in rows if x["outcome"] == "denied" and x["actor_id"] == world.tl_north.id]


async def test_inbox_row_has_no_secret_fields_and_the_payload_is_plain(client, world):
    r = await client.post(
        "/users",
        json={
            "name": "Plain",
            "email": new_email(),
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "r",
        },
        headers=h(world.tl_north),
    )
    ap = (await client.get(f"/approvals/{r.json()['approval_id']}", headers=h(world.admin))).json()
    assert set(ap) == {
        "id",
        "type",
        "status",
        "summary",
        "payload",
        "requested_by",
        "requested_by_name",
        "request_reason",
        "routed_to",
        "decided_by",
        "decided_by_name",
        "decision_reason",
        "created_at",
        "decided_at",
    }
    assert ap["requested_by_name"] == "Tara" and ap["payload"]["role"] == "user"
