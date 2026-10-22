# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 3: scope (Own / Team / All) and cross-organisation isolation."""

import uuid

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.platform.approvals import service as approvals
from app.platform.context import RequestInfo
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL, ROUTE_TEAM_LEADER
from app.platform.uow import UnitOfWork
from tests.deep.phase1.kit import ROLES, actor_by_role, audit_rows, h, new_email
from tests.helpers import dispatch_all, notifications_for

REQ = RequestInfo()


async def _noop(uow, req):
    await uow.audit("deep.executed", "approval", str(req.id), new={"ran": True})


approvals.register_type("deep.noop", _noop)


async def make_request(actor, *, routed_to=ROUTE_ADMIN_POOL, team_id=None, owner=None):
    async with UnitOfWork(actor, REQ) as uow:
        req = await approvals.create_request(
            uow,
            type_="deep.noop",
            action_key="user.manage",
            summary=f"{actor.name} asks",
            payload={"x": 1},
            reason="because",
            routed_to=routed_to,
            team_id=team_id,
            record_owner_id=owner,
        )
        return req.id


def same_shape(a, b) -> bool:
    return a.status_code == b.status_code and a.json() == b.json()


# ------------------------------------------------------------------ cross-organisation
@pytest.mark.parametrize("role", ROLES)
async def test_org_b_cannot_read_change_or_detect_org_a_users_and_teams(
    client, world, world_b, role
):
    b = actor_by_role(world_b, role)
    missing = uuid.uuid4()
    n_before = len(await audit_rows(world.org_id))
    for victim in (world.north1, world.tl_north, world.admin, world.sa):
        for call in (
            lambda i: client.get(f"/users/{i}", headers=h(b)),
            lambda i: client.patch(
                f"/users/{i}", json={"name": "Pwn", "reason": "x"}, headers=h(b)
            ),
            lambda i: client.patch(
                f"/users/{i}", json={"is_active": False, "reason": "x"}, headers=h(b)
            ),
            lambda i: client.patch(
                f"/users/{i}", json={"role": "admin", "reason": "x"}, headers=h(b)
            ),
            lambda i: client.post(f"/users/{i}/password-reset", json={"reason": "x"}, headers=h(b)),
        ):
            real, ghost = await call(victim.id), await call(missing)
            assert real.status_code == 404 and same_shape(real, ghost), (role, real.text)
    for t in (world.north_team, world.south_team):
        real = await client.patch(f"/teams/{t}", json={"name": "Pwn", "reason": "x"}, headers=h(b))
        ghost = await client.patch(
            f"/teams/{missing}", json={"name": "Pwn", "reason": "x"}, headers=h(b)
        )
        if role in ("admin", "super_admin"):
            assert real.status_code == 404 and same_shape(real, ghost)
        else:
            assert real.status_code == ghost.status_code == 403
    # moving an org B user into an org A team, or an org A user into an org B team
    for body, uid in (
        ({"team_id": str(world.north_team)}, world_b.north1.id),
        ({"team_id": str(world_b.north_team)}, world.north1.id),
    ):
        r = await client.patch(f"/users/{uid}", json={**body, "reason": "x"}, headers=h(world_b.sa))
        assert r.status_code == 404, r.text
    # nothing changed in org A (a refused cross-org call leaves no audit row in A)
    assert len(await audit_rows(world.org_id)) == n_before
    async with get_sessionmaker()() as s:
        names = (
            (await s.execute(text("SELECT name FROM users WHERE org_id=:o"), {"o": world.org_id}))
            .scalars()
            .all()
        )
    assert "Pwn" not in names
    # lists never contain org A
    emails = {u["email"] for u in (await client.get("/users", headers=h(b))).json()}
    assert not ({a.email for a in world.all_actors} & emails)
    tids = {t["id"] for t in (await client.get("/teams", headers=h(b))).json()}
    assert not ({str(world.north_team), str(world.south_team)} & tids)


async def test_other_org_team_in_create_and_rename_is_indistinguishable_from_missing(
    client, world, world_b
):
    body = {"name": "X", "email": new_email(), "role": "user", "reason": "r"}
    a = await client.post(
        "/users", json={**body, "team_id": str(world.north_team)}, headers=h(world_b.admin)
    )
    ghost = await client.post(
        "/users", json={**body, "team_id": str(uuid.uuid4())}, headers=h(world_b.admin)
    )
    assert a.status_code == 404 and same_shape(a, ghost)


async def test_org_b_approvals_isolated(client, world, world_b):
    rid_a = await make_request(world.tl_north)
    rid_team_a = await make_request(
        world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team
    )
    missing = uuid.uuid4()
    for role in ROLES:
        b = actor_by_role(world_b, role)
        for rid in (rid_a, rid_team_a):
            for method, path, body in (
                ("GET", f"/approvals/{rid}", None),
                ("POST", f"/approvals/{rid}/decide", {"decision": "approve", "reason": "x"}),
                ("POST", f"/approvals/{rid}/decide", {"decision": "reject", "reason": "x"}),
                ("POST", f"/approvals/{rid}/cancel", None),
            ):
                real = await client.request(method, path, json=body, headers=h(b))
                ghost = await client.request(
                    method, path.replace(str(rid), str(missing)), json=body, headers=h(b)
                )
                assert real.status_code == ghost.status_code, (role, method, path, real.text)
                if real.status_code == 404:
                    assert same_shape(real, ghost)
                else:  # a role that may not decide anything answers the same for both
                    assert (
                        real.status_code == 403
                        and ghost.status_code == 403
                        or same_shape(real, ghost)
                    )
        for tab in ("pending", "decided", "mine"):
            ids = {
                a["id"] for a in (await client.get(f"/approvals?tab={tab}", headers=h(b))).json()
            }
            assert not ({str(rid_a), str(rid_team_a)} & ids)
    # org A's requests are still pending and unexecuted
    async with get_sessionmaker()() as s:
        st = (
            (
                await s.execute(
                    text("SELECT status FROM approval_requests WHERE id = ANY(:i)"),
                    {"i": [rid_a, rid_team_a]},
                )
            )
            .scalars()
            .all()
        )
    assert st == ["pending", "pending"]


async def test_approval_decide_for_missing_vs_other_org_user_role_probe(client, world, world_b):
    """A plain User deciding an existing request in own org answers 403; a missing one 404.
    (Inside one org that is acceptable. Across orgs both must look 'missing'.)"""
    rid_a = await make_request(world.tl_north)
    b = world_b.north1
    r_other_org = await client.post(
        f"/approvals/{rid_a}/decide", json={"decision": "approve"}, headers=h(b)
    )
    r_missing = await client.post(
        f"/approvals/{uuid.uuid4()}/decide", json={"decision": "approve"}, headers=h(b)
    )
    assert r_other_org.status_code == r_missing.status_code == 404


async def test_org_b_history_isolated(client, world, world_b):
    await client.patch(
        f"/users/{world.north1.id}",
        json={"name": "Priya Z", "reason": "typo"},
        headers=h(world.admin),
    )
    for role in ROLES:
        b = actor_by_role(world_b, role)
        for etype, eid in (
            ("user", world.north1.id),
            ("user", world.admin.id),
            ("approval", uuid.uuid4()),
            ("team", world.north_team),
        ):
            real = await client.get(f"/history/{etype}/{eid}", headers=h(b))
            ghost = await client.get(f"/history/{etype}/{uuid.uuid4()}", headers=h(b))
            assert real.status_code == 404 and same_shape(real, ghost), (role, etype)
    # a history lookup with an org A id as text for an unknown entity type is the same 404
    r = await client.get("/history/user/not-a-uuid", headers=h(world_b.sa))
    assert r.status_code == 404


async def test_org_b_notifications_events_failed_jobs_isolated(
    client, world, world_b, admin_engine
):
    await make_request(world.tl_north)
    await dispatch_all()
    a_notifs = await notifications_for(world.admin.id)
    assert a_notifs
    ids = [str(n.id) for n in a_notifs]
    # org B reads nothing of A
    for b in world_b.all_actors:
        body = (await client.get("/notifications", headers=h(b))).json()
        assert not ({i["id"] for i in body["items"]} & set(ids))
    # org B tries to mark A's notification read: marks nothing, A's stays unread
    r = await client.patch("/notifications", json={"ids": ids}, headers=h(world_b.sa))
    assert r.json() == {"marked": 0}
    r = await client.patch(
        "/notifications", json={"ids": ids}, headers=h(world.north1)
    )  # same org, wrong user
    assert r.json() == {"marked": 0}
    assert all(n.read_at is None for n in await notifications_for(world.admin.id))
    # catch-up events
    async with get_sessionmaker()() as s:
        last = (await s.execute(text("SELECT coalesce(max(id),0) FROM outbox_events"))).scalar_one()
    async with UnitOfWork(world.admin, REQ) as uow:
        await uow.emit(
            "user.updated",
            {},
            rt={
                "entity_type": "user",
                "entity_id": str(world.north1.id),
                "owner_id": world.north1.id,
                "team_id": world.north_team,
                "scope_key": "audit.view",
            },
        )
    assert len((await client.get(f"/events?since={last}", headers=h(world.admin))).json()) == 1
    for b in world_b.all_actors:
        assert (await client.get(f"/events?since={last}", headers=h(b))).json() == []
    # failed jobs: org A's failed event invisible to org B and cannot be retried by B
    async with admin_engine.begin() as c:
        eid = (
            await c.execute(
                text(
                    "INSERT INTO outbox_events (org_id,type,payload,status,attempts,last_error) VALUES (:o,'x.boom','{}','failed',10,'boom') RETURNING id"
                ),
                {"o": world.org_id},
            )
        ).scalar_one()
    assert [
        j["id"] for j in (await client.get("/admin/failed-jobs", headers=h(world.admin))).json()
    ] == [eid]
    assert (await client.get("/admin/failed-jobs", headers=h(world_b.sa))).json() == []
    await client.post(
        f"/admin/failed-jobs/{eid}/retry", json={"reason": "x"}, headers=h(world_b.sa)
    )
    async with get_sessionmaker()() as s:
        status = (
            await s.execute(text("SELECT status FROM outbox_events WHERE id=:i"), {"i": eid})
        ).scalar_one()
    assert status == "failed"  # org B could not touch it


async def test_no_endpoint_response_ever_contains_a_foreign_org_id(client, world, world_b):
    foreign = {str(world.org_id)}
    for actor in world_b.all_actors:
        for path in (
            "/me",
            "/users",
            "/teams",
            "/approvals?tab=pending",
            "/approvals?tab=decided",
            "/notifications",
            "/events",
            "/admin/failed-jobs",
        ):
            r = await client.get(path, headers=h(actor))
            if r.status_code == 200:
                assert not any(f in r.text for f in foreign), (actor.role, path)


# ------------------------------------------------------------------ own / team / all
async def _emails(client, actor):
    return {u["email"] for u in (await client.get("/users", headers=h(actor))).json()}


async def test_user_list_exact_rows_per_role(client, world):
    everyone = {a.email for a in world.all_actors}
    north = {world.tl_north.email, world.north1.email, world.north2.email}
    south = {world.tl_south.email, world.south1.email, world.south2.email}
    assert await _emails(client, world.north1) == north
    assert await _emails(client, world.north2) == north
    assert await _emails(client, world.south1) == south
    assert await _emails(client, world.tl_north) == north
    assert await _emails(client, world.tl_south) == south
    assert await _emails(client, world.admin) == everyone
    assert await _emails(client, world.sa) == everyone
    # a Super admin is never listed to a Team Leader or User (PRD 4.3)
    for a in (world.north1, world.tl_north, world.south2, world.tl_south):
        assert world.sa.email not in await _emails(client, a)
        assert world.admin.email not in await _emails(client, a)
        assert (await client.get(f"/users/{world.sa.id}", headers=h(a))).status_code == 404
        assert (await client.get(f"/users/{world.admin.id}", headers=h(a))).status_code == 404


async def test_user_detail_scope_matrix(client, world):
    cases = {
        world.north1: {
            world.north1: 200,
            world.north2: 200,
            world.tl_north: 200,
            world.south1: 404,
            world.tl_south: 404,
        },
        world.tl_north: {
            world.north1: 200,
            world.north2: 200,
            world.tl_north: 200,
            world.south1: 404,
            world.tl_south: 404,
        },
        world.tl_south: {world.north1: 404, world.south1: 200, world.south2: 200},
    }
    for actor, row in cases.items():
        for target, want in row.items():
            r = await client.get(f"/users/{target.id}", headers=h(actor))
            assert r.status_code == want, (actor.name, target.name, r.status_code)
    for a in (world.admin, world.sa):
        for t in world.all_actors:
            assert (await client.get(f"/users/{t.id}", headers=h(a))).status_code == 200


async def test_approvals_inbox_exact_rows_per_role(client, world):
    r_tl_admin = await make_request(world.tl_north)  # admin pool
    r_n1_tl = await make_request(
        world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team
    )
    r_s1_tl = await make_request(
        world.south1, routed_to=ROUTE_TEAM_LEADER, team_id=world.south_team
    )
    r_sa_own = await make_request(world.sa)  # a Super admin's own request
    r_adm_own = await make_request(world.admin)

    async def ids(actor, tab):
        return {
            a["id"] for a in (await client.get(f"/approvals?tab={tab}", headers=h(actor))).json()
        }

    s = lambda *x: {str(i) for i in x}  # noqa: E731
    # pending (what the person may decide)
    assert await ids(world.north1, "pending") == set()
    assert await ids(world.south2, "pending") == set()
    assert await ids(world.tl_north, "pending") == s(r_n1_tl)
    assert await ids(world.tl_south, "pending") == s(r_s1_tl)
    # an Admin may not decide their own request, a Super admin may
    assert await ids(world.admin, "pending") == s(r_tl_admin, r_n1_tl, r_s1_tl, r_sa_own)
    assert await ids(world.sa, "pending") == s(r_tl_admin, r_n1_tl, r_s1_tl, r_sa_own, r_adm_own)
    # mine
    assert await ids(world.north1, "mine") == s(r_n1_tl)
    assert await ids(world.tl_north, "mine") == s(r_tl_admin)
    assert await ids(world.admin, "mine") == s(r_adm_own)
    assert await ids(world.tl_south, "mine") == set()
    # decided tab only after a decision
    assert await ids(world.sa, "decided") == set()
    await approvals.decide(world.tl_north, REQ, r_n1_tl, approve=True, reason=None)
    assert await ids(world.sa, "decided") == s(r_n1_tl)
    assert await ids(world.tl_north, "decided") == s(r_n1_tl)
    assert await ids(world.tl_south, "decided") == set()
    assert await ids(world.north1, "decided") == set()
    assert await ids(world.north1, "mine") == s(r_n1_tl)


async def test_approval_detail_visibility_matrix(client, world):
    rid = await make_request(world.north1, routed_to=ROUTE_TEAM_LEADER, team_id=world.north_team)
    want = {world.north1: 200, world.tl_north: 200, world.admin: 200, world.sa: 200,
            world.north2: 404, world.tl_south: 404, world.south1: 404}  # fmt: skip
    for actor, code in want.items():
        assert (await client.get(f"/approvals/{rid}", headers=h(actor))).status_code == code, (
            actor.name
        )


async def test_history_scope_matrix_for_user_records(client, world):
    for t in (world.north1, world.tl_north, world.south1, world.admin, world.sa):
        await client.patch(
            f"/users/{t.id}", json={"name": t.name + "x", "reason": "r"}, headers=h(world.sa)
        )
    allow = {
        world.north1: {world.north1.id},
        world.north2: {world.north2.id},
        world.tl_north: {world.north1.id, world.north2.id, world.tl_north.id},
        world.tl_south: {world.south1.id, world.south2.id, world.tl_south.id},
        world.south1: {world.south1.id},
    }
    universe = [a.id for a in world.all_actors]
    for actor, ok in allow.items():
        for uid in universe:
            r = await client.get(f"/history/user/{uid}", headers=h(actor))
            assert r.status_code == (200 if uid in ok else 404), (actor.name, uid, r.status_code)
    for a in (world.admin, world.sa):
        for uid in universe:
            assert (await client.get(f"/history/user/{uid}", headers=h(a))).status_code == 200


async def test_a_user_can_read_the_reason_of_a_change_to_their_own_record_and_nobody_out_of_scope(
    client, world
):
    r = await client.patch(
        f"/users/{world.north1.id}",
        json={"name": "Priya Rao", "reason": "Marriage name change"},
        headers=h(world.admin),
    )
    assert r.status_code == 200
    mine = (await client.get(f"/history/user/{world.north1.id}", headers=h(world.north1))).json()
    assert mine[0]["reason"] == "Marriage name change" and mine[0]["actor"] == world.admin.name
    assert mine[0]["old"]["name"] == "Priya" and mine[0]["new"]["name"] == "Priya Rao"
    for hidden in (world.south1, world.tl_south, world.south2):
        assert (
            await client.get(f"/history/user/{world.north1.id}", headers=h(hidden))
        ).status_code == 404


async def test_events_catch_up_scope_per_role_in_order_without_gaps_or_duplicates(client, world):
    async with get_sessionmaker()() as s:
        last = (await s.execute(text("SELECT coalesce(max(id),0) FROM outbox_events"))).scalar_one()
    owners = [
        world.north1,
        world.north2,
        world.south1,
        world.tl_north,
        world.sa,
        world.admin,
        world.south2,
    ]
    for o in owners:
        async with UnitOfWork(world.admin, REQ) as uow:
            await uow.emit(
                "user.updated", {},
                rt={"entity_type": "user", "entity_id": str(o.id), "owner_id": o.id,
                    "team_id": o.team_id, "scope_key": "audit.view"},
            )  # fmt: skip

    async def seen(actor):
        return [
            e["entity_id"]
            for e in (await client.get(f"/events?since={last}", headers=h(actor))).json()
        ]

    ids = lambda *x: [str(i.id) for i in x]  # noqa: E731
    assert await seen(world.admin) == ids(*owners)
    assert await seen(world.sa) == ids(*owners)
    assert await seen(world.tl_north) == ids(world.north1, world.north2, world.tl_north)
    assert await seen(world.north1) == ids(world.north1)
    assert await seen(world.south2) == ids(world.south2)
    assert await seen(world.tl_south) == ids(world.south1, world.south2)
    # ids strictly increasing, no duplicates, resuming from the last one gives nothing new
    body = (await client.get(f"/events?since={last}", headers=h(world.admin))).json()
    evs = [e["id"] for e in body]
    assert evs == sorted(set(evs))
    assert (await client.get(f"/events?since={evs[-1]}", headers=h(world.admin))).json() == []
    # resume in the middle: exactly the tail
    mid = evs[2]
    assert [
        e["id"] for e in (await client.get(f"/events?since={mid}", headers=h(world.admin))).json()
    ] == evs[3:]


async def test_super_admin_owned_events_hidden_from_team_roles(client, world):
    async with get_sessionmaker()() as s:
        last = (await s.execute(text("SELECT coalesce(max(id),0) FROM outbox_events"))).scalar_one()
    # a record owned by the Super admin but attached to the north team (scope key with ALL for admins)
    async with UnitOfWork(world.admin, REQ) as uow:
        await uow.emit(
            "deal.updated", {},
            rt={"entity_type": "deal", "entity_id": str(uuid.uuid4()), "owner_id": world.sa.id,
                "team_id": world.north_team, "scope_key": "deal.view"},
        )  # fmt: skip
    for a in (world.tl_north, world.north1):
        assert (await client.get(f"/events?since={last}", headers=h(a))).json() == []
    assert len((await client.get(f"/events?since={last}", headers=h(world.admin))).json()) == 1


# ------------------------------------------------------------------ pagination & search extremes
@pytest.mark.parametrize(
    "qs,want",
    [("limit=0", 422), ("limit=-1", 422), ("limit=1", 200), ("limit=200", 200), ("limit=201", 422),
     ("limit=abc", 422), ("limit=1.5", 422), ("before_seq=abc", 422), ("before_seq=-5", 200),
     ("before_seq=0", 200), ("limit=50&before_seq=1", 200)],
)  # fmt: skip
async def test_history_pagination_boundaries(client, world, qs, want):
    await client.patch(
        f"/users/{world.north1.id}", json={"name": "P1", "reason": "r"}, headers=h(world.admin)
    )
    r = await client.get(f"/history/user/{world.north1.id}?{qs}", headers=h(world.admin))
    assert r.status_code == want, r.text
    if want == 422:
        assert set(r.json()) == {"code", "message", "details"}


async def test_history_pages_are_stable_and_complete(client, world):
    for i in range(7):
        await client.patch(
            f"/users/{world.north1.id}",
            json={"name": f"P{i}", "reason": f"r{i}"},
            headers=h(world.admin),
        )
    path = f"/history/user/{world.north1.id}"
    full = (await client.get(f"{path}?limit=200", headers=h(world.admin))).json()
    assert [x["seq"] for x in full] == sorted((x["seq"] for x in full), reverse=True)
    paged, before = [], None
    while True:
        url = f"{path}?limit=3" + (f"&before_seq={before}" if before else "")
        page = (await client.get(url, headers=h(world.admin))).json()
        if not page:
            break
        paged.extend(page)
        before = page[-1]["seq"]
    assert [x["id"] for x in paged] == [x["id"] for x in full] and len(full) == 7


async def test_huge_numbers_in_query_strings_are_4xx_not_500(client, world):
    big = "9" * 25
    for path in (f"/history/user/{world.north1.id}?before_seq={big}", f"/events?since={big}"):
        r = await client.get(path, headers=h(world.admin))
        assert r.status_code < 500, (path, r.status_code)
    r = await client.post(
        f"/admin/failed-jobs/{big}/retry", json={"reason": "x"}, headers=h(world.admin)
    )
    assert r.status_code < 500


async def test_pending_inbox_is_capped_at_100_and_oldest_requests_become_unreachable(
    client, world, admin_engine
):
    """Requests never expire (PRD 4.5) but the inbox shows only the newest 100 and has no paging."""
    async with admin_engine.begin() as c:
        for i in range(105):
            await c.execute(
                text(
                    "INSERT INTO approval_requests (org_id,type,action_key,payload,requested_by,request_reason,routed_to,created_at) "
                    "VALUES (:o,'deep.noop','user.manage','{}',:u,'r','admin_pool', now() - make_interval(secs => :s))"
                ),
                {"o": world.org_id, "u": world.tl_north.id, "s": 105 - i},
            )
    got = (await client.get("/approvals?tab=pending", headers=h(world.admin))).json()
    oldest = (
        await client.get("/approvals?tab=pending&limit=200&offset=100", headers=h(world.admin))
    ).json()
    assert len(got) == 105, f"inbox shows {len(got)} of 105 pending requests; no way to page"
    _ = oldest
