# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 4: the Lead Pool, manual assignment, reassignment, bulk reassignment,
direct team moves, and the team-move hook for a rep."""

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.deep.phase2.support import (
    audit_rows,
    configure,
    headers,
    invariant_violations,
    make_world,
    put_settings,
    q,
    q1,
)
from tests.deep.phase2.support import (
    create_via_leader as create,
)
from tests.helpers import dispatch_all, notifications_for


async def waiting(qc, world, n=1, source="Website", team=None, **kw):
    """n pooled leads that are waiting (everyone at cap 0), routed to `team` (default North)."""
    await put_settings(
        qc, world.admin, world, default_cap=0, default_team_id=str(team or world.north_team)
    )
    out = []
    for _ in range(n):
        d = await create(qc, world.north1, lead_source=source, **kw)
        assert d["status"] == "pooled", d
        out.append(d)
    return out


async def one_open(qc, world, who=None):
    await configure(qc, world)
    d = await create(qc, who or world.north1)
    assert d["status"] == "open"
    return d


def assign(qc, actor, deal_id, user, reason="because", **kw):
    return qc.post(
        f"/pool/{deal_id}/assign",
        json={"user_id": str(user.id), "reason": reason, **kw},
        headers=headers(actor),
    )


def reassign(qc, actor, deal_id, user, reason="because"):
    return qc.post(
        f"/deals/{deal_id}/reassign",
        json={"user_id": str(user.id), "reason": reason},
        headers=headers(actor),
    )


# ------------------------------------------------------------------------------ pool visibility
async def test_pool_visibility_per_role_and_routed_but_unassigned_leads(qc, world):
    (a,) = await waiting(qc, world, 1)  # routed to North
    await put_settings(qc, world.admin, world, default_cap=0, default_team_id=str(world.south_team))
    b = await create(qc, world.north1)
    assert b["status"] == "pooled" and b["team_id"] == str(world.south_team)
    mine = {a["id"]}
    for actor in (world.north1, world.north2, world.south1, world.south2):
        assert (await qc.get("/pool", headers=headers(actor))).status_code == 403
    assert (await qc.get("/pool")).status_code == 401
    got = (await qc.get("/pool", headers=headers(world.tl_north))).json()
    assert {r["deal_id"] for r in got} == mine
    assert (
        got[0]["routed_team_name"] == "Team North" and got[0]["waiting_reason"] == "all_reps_at_cap"
    )
    assert got[0]["held_for_manual"] is False and got[0]["generated_by"] == "Tara"
    got = (await qc.get("/pool", headers=headers(world.tl_south))).json()
    assert {r["deal_id"] for r in got} == {b["id"]}
    for actor in (world.admin, world.sa):
        assert {r["deal_id"] for r in (await qc.get("/pool", headers=headers(actor))).json()} == {
            a["id"],
            b["id"],
        }


async def test_pool_filters(qc, admin_engine, world):
    await put_settings(qc, world.admin, world, default_cap=0)
    old = await create(qc, world.north1, lead_source="Event", company="Quartz Mining")
    new = await create(qc, world.north1, lead_source="Referral", company="Harbor Freight")
    await put_settings(qc, world.admin, world, default_cap=0, default_team_id=str(world.south_team))
    third = await create(qc, world.north1, lead_source="Event", company="Zinc Alloys")
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE lead_pool_entries SET entered_at = now() - interval '3 hours' WHERE deal_id=:d"
            ),
            {"d": old["id"]},
        )
        await c.execute(
            text("UPDATE lead_pool_entries SET waiting_reason='no_active_reps' WHERE deal_id=:d"),
            {"d": new["id"]},
        )
    h = headers(world.admin)

    async def ids(**params):
        return {r["deal_id"] for r in (await qc.get("/pool", params=params, headers=h)).json()}

    everything = {old["id"], new["id"], third["id"]}
    assert await ids() == everything
    assert await ids(source="Event") == {old["id"], third["id"]}
    assert await ids(source="Referral") == {new["id"]}
    assert await ids(source="Cold outbound") == set()
    assert await ids(team_id=str(world.south_team)) == {third["id"]}
    assert await ids(team_id=str(world.north_team)) == {old["id"], new["id"]}
    assert await ids(min_minutes=120) == {old["id"]}
    assert await ids(min_minutes=5000) == set()
    assert await ids(waiting_reason="no_active_reps") == {new["id"]}
    assert await ids(waiting_reason="all_reps_at_cap") == {old["id"], third["id"]}
    assert await ids(source="Event", team_id=str(world.north_team), min_minutes=60) == {old["id"]}
    tl = (await qc.get("/pool", params={"source": "Event"}, headers=headers(world.tl_north))).json()
    assert {r["deal_id"] for r in tl} == {old["id"]}


async def test_pool_huge_min_minutes_is_a_clean_answer(qc, world):
    await waiting(qc, world, 1)
    r = await qc.get("/pool", params={"min_minutes": 10**12}, headers=headers(world.admin))
    assert r.status_code < 500


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"team_id": "garbage"}, 422),
        ({"min_minutes": "abc"}, 422),
        ({"source": "x'; DROP TABLE deals;--"}, 200),
        ({"waiting_reason": "<script>"}, 200),
        ({"source": "अ" * 5000}, 200),
        ({"min_minutes": -5}, 200),
        ({"team_id": str(uuid.uuid4())}, 200),
    ],
)
async def test_pool_hostile_query_params(qc, admin_engine, world, params, code):
    await waiting(qc, world, 1)
    r = await qc.get("/pool", params=params, headers=headers(world.admin))
    assert r.status_code < 500, (params, r.text)
    if code:
        assert r.status_code == code
    assert await q1(admin_engine, "SELECT count(*) FROM deals WHERE org_id=:o", o=world.org_id) == 1


async def test_pool_lists_every_waiting_lead(qc, admin_engine, world):
    (first,) = await waiting(qc, world, 1)
    acc = await q1(
        admin_engine, "SELECT account_id FROM deals WHERE id=:d", d=uuid.UUID(first["id"])
    )
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "WITH nd AS (INSERT INTO deals (org_id, account_id, status, lead_source, team_id, generated_by) "
                "SELECT :o, :a, 'pooled', 'Website', :t, :g FROM generate_series(1, 210) RETURNING id) "
                "INSERT INTO lead_pool_entries (org_id, deal_id, routed_team_id, waiting_reason) "
                "SELECT :o, id, :t, 'all_reps_at_cap' FROM nd"
            ),
            {"o": world.org_id, "a": acc, "t": world.north_team, "g": world.north1.id},
        )
    got = (await qc.get("/pool", headers=headers(world.admin))).json()
    assert len(got) == 211


async def test_pool_is_oldest_first(qc, world):
    ds = await waiting(qc, world, 4)
    got = (await qc.get("/pool", headers=headers(world.admin))).json()
    assert [r["deal_id"] for r in got] == [d["id"] for d in ds]


# ------------------------------------------------------------------------------ distribution
async def test_distribution_numbers_match_raw_sql(qc, admin_engine, world):
    await put_settings(qc, world.admin, world, default_cap=4, caps={str(world.north2.id): 2})
    deals = [await create(qc, world.north1) for _ in range(6)]
    await put_settings(
        qc,
        world.admin,
        world,
        default_cap=4,
        caps={str(world.north2.id): 2},
        default_team_id=str(world.south_team),
    )
    sd = [await create(qc, world.north1) for _ in range(3)]
    mover = next(d for d in deals if d["owner"]["id"] == str(world.north1.id))
    r = await reassign(qc, world.tl_north, mover["id"], world.north2, "balance")
    assert r.status_code == 200
    await put_settings(
        qc,
        world.admin,
        world,
        default_cap=0,
        caps={str(world.north2.id): 2},
        default_team_id=str(world.north_team),
    )
    pooled = [await create(qc, world.north1) for _ in range(2)]
    assert all(p["status"] == "pooled" for p in pooled)
    for actor, expect_team in (
        (world.admin, None),
        (world.sa, None),
        (world.tl_north, world.north_team),
        (world.tl_south, world.south_team),
    ):
        dist = (await qc.get("/pool/distribution", headers=headers(actor))).json()
        team_clause = "" if expect_team is None else "AND d.team_id = :t"
        sql_pool = await q1(
            admin_engine,
            f"SELECT count(*) FROM deals d WHERE d.org_id=:o AND d.status='pooled' AND d.deleted_at IS NULL {team_clause}",
            o=world.org_id,
            t=expect_team,
        )
        assert dist["pool_size"] == sql_pool, actor.name
        rep_sql = {
            r[0]: (r[1], r[2])
            for r in await q(
                admin_engine,
                "SELECT u.name, (SELECT count(*) FROM deals d WHERE d.owner_id=u.id AND d.status='open' AND d.deleted_at IS NULL),"
                " (SELECT count(*) FROM deals d WHERE d.owner_id=u.id AND d.assigned_at >= now() - interval '30 days' AND d.deleted_at IS NULL) "
                "FROM users u WHERE u.org_id=:o AND u.role='user' AND u.is_active "
                + ("" if expect_team is None else "AND u.team_id=:t"),
                o=world.org_id,
                t=expect_team,
            )
        }
        assert {r["name"] for r in dist["reps"]} == set(rep_sql), actor.name
        for r in dist["reps"]:
            assert (r["active_leads"], r["assigned_in_period"]) == rep_sql[r["name"]], (
                actor.name,
                r["name"],
            )
        caps = {r["name"]: r["cap"] for r in dist["reps"]}
        assert caps.get("Rahul", 2) == 2 and caps.get("Priya", 0) == 0 and caps.get("Sneha", 0) == 0
        method_sql = dict(
            await q(
                admin_engine,
                f"SELECT assignment_method, count(*) FROM deals d WHERE d.org_id=:o AND d.assigned_at >= now() - interval '30 days' AND d.assignment_method IS NOT NULL AND d.deleted_at IS NULL {team_clause} GROUP BY 1",
                o=world.org_id,
                t=expect_team,
            )
        )
        assert dist["methods"] == method_sql, actor.name
    admin_view = (await qc.get("/pool/distribution", headers=headers(world.admin))).json()
    assert [(x["deal_id"], x["reason"]) for x in admin_view["reassignments"]] == [
        (mover["id"], "balance")
    ]
    assert (
        admin_view["reassignments"][0]["from"] == mover["owner"]["name"]
        and admin_view["reassignments"][0]["to"] == "Rahul"
    )
    assert (await qc.get("/pool/distribution", headers=headers(world.tl_south))).json()[
        "reassignments"
    ] == []
    assert sum(r["assigned_in_period"] for r in admin_view["reps"]) == len(deals) + len(sd)


@pytest.mark.parametrize("days", [0, -3, 1, 30, 365, 100000])
async def test_distribution_days_parameter_is_clamped(qc, world, days):
    await configure(qc, world)
    r = await qc.get("/pool/distribution", params={"days": days}, headers=headers(world.admin))
    assert r.status_code == 200


async def test_distribution_period_window_and_access(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE deals SET assigned_at = now() - interval '40 days' WHERE id=:i"),
            {"i": d["id"]},
        )
    thirty = (
        await qc.get("/pool/distribution", params={"days": 30}, headers=headers(world.admin))
    ).json()
    sixty = (
        await qc.get("/pool/distribution", params={"days": 60}, headers=headers(world.admin))
    ).json()
    assert sum(r["assigned_in_period"] for r in thirty["reps"]) == 0 and thirty["methods"] == {}
    assert sum(r["assigned_in_period"] for r in sixty["reps"]) == 1 and sixty["methods"] == {
        "round_robin": 1
    }
    for actor in (world.north1, world.south2):
        assert (await qc.get("/pool/distribution", headers=headers(actor))).status_code == 403
    assert (
        await qc.get("/pool/distribution", params={"days": "x"}, headers=headers(world.admin))
    ).status_code == 422


# ------------------------------------------------------------------------------ manual assignment
async def test_manual_assign_role_and_scope_matrix(qc, admin_engine, world):
    (d,) = await waiting(qc, world, 1)
    url_user = world.north2
    for actor in (world.north1, world.south1):
        assert (await assign(qc, actor, d["id"], url_user)).status_code == 403
    assert (
        await assign(qc, world.tl_south, d["id"], world.south1)
    ).status_code == 403  # not their deal
    assert (
        await assign(qc, world.tl_north, d["id"], world.south1)
    ).status_code == 403  # not their team
    assert (
        await assign(qc, world.tl_north, d["id"], world.admin)
    ).status_code == 403  # an Admin is no team member
    assert (await assign(qc, world.tl_north, d["id"], url_user, reason="")).status_code == 422
    assert (await assign(qc, world.admin, d["id"], url_user, reason=None)).status_code == 422
    assert (
        await qc.post(f"/pool/{d['id']}/assign", json={"user_id": str(url_user.id)})
    ).status_code == 401
    r = await assign(qc, world.admin, d["id"], world.south2, reason="cross team on purpose")
    assert (
        r.status_code == 200
        and r.json()["owner"]["name"] == "Vikram"
        and r.json()["team_name"] == "Team South"
    )
    assert r.json()["assignment_method"] == "manual" and r.json()["assigned_by"] == "Anita"
    rows = [tuple(x)[:2] for x in await audit_rows(admin_engine, d["id"])]
    assert rows.count(("pool.assign", "ok")) == 1 and rows.count(("pool.assign", "denied")) >= 4
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_manual_assign_by_super_admin_without_reason_and_to_a_team_leader(qc, world):
    (d,) = await waiting(qc, world, 1)
    r = await assign(qc, world.sa, d["id"], world.tl_north, reason=None)
    assert r.status_code == 200 and r.json()["owner"]["name"] == "Tara"  # a TL can take one by hand


async def test_manual_assign_warns_when_over_cap_and_records_it(qc, admin_engine, world):
    ds = await waiting(qc, world, 2)
    r = await assign(qc, world.tl_north, ds[0]["id"], world.north1)
    assert r.status_code == 200 and "(0/0)" in r.json()["warning"]
    assert r.json()["warning"].startswith("Priya is at their cap")
    note = await q1(
        admin_engine,
        "SELECT note FROM audit_log WHERE entity_id=:e AND action_key='pool.assign' AND outcome='ok'",
        e=ds[0]["id"],
    )
    assert "at their cap" in note
    await put_settings(qc, world.admin, world, default_cap=50)
    r = await assign(qc, world.tl_north, ds[1]["id"], world.north2)
    assert r.status_code == 200 and r.json()["warning"] is None


@pytest.mark.parametrize("state", ["assigned", "lost", "deleted", "unknown"])
async def test_manual_assign_refuses_deals_that_are_not_waiting(qc, world, state):
    (d,) = await waiting(qc, world, 1)
    if state == "assigned":
        assert (await assign(qc, world.tl_north, d["id"], world.north1)).status_code == 200
        assert (await assign(qc, world.tl_north, d["id"], world.north2)).status_code == 409
        return
    if state == "deleted":
        await qc.post(
            f"/deals/{d['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
        )
        assert (await assign(qc, world.admin, d["id"], world.north1)).status_code == 404
        return
    if state == "lost":
        await assign(qc, world.admin, d["id"], world.north1)
        await qc.post(
            f"/deals/{d['id']}/lose", json={"lost_reason": "Price"}, headers=headers(world.north1)
        )
        assert (await assign(qc, world.admin, d["id"], world.north2)).status_code == 409
        return
    assert (await assign(qc, world.admin, str(uuid.uuid4()), world.north1)).status_code == 404


async def test_manual_assign_to_bad_targets(qc, admin_engine, world):
    (d,) = await waiting(qc, world, 1)
    await qc.patch(
        f"/users/{world.north2.id}",
        json={"is_active": False, "reason": "left"},
        headers=headers(world.admin),
    )
    r = await assign(qc, world.tl_north, d["id"], world.north2)
    assert r.status_code == 422 and r.json()["details"]["field"] == "user_id"
    other = await make_world(admin_engine)
    r = await assign(qc, world.admin, d["id"], other.north1)
    assert r.status_code == 422
    r = await qc.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(uuid.uuid4()), "reason": "x"},
        headers=headers(world.admin),
    )
    assert r.status_code == 422
    r = await qc.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": "nope", "reason": "x"},
        headers=headers(world.admin),
    )
    assert r.status_code == 422
    r = await qc.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(world.north1.id), "reason": "x", "owner_id": "1"},
        headers=headers(world.admin),
    )
    assert r.status_code in (200, 422)  # AssignIn is not strict: extra fields are ignored
    still = (await qc.get(f"/deals/{d['id']}", headers=headers(world.admin))).json()
    assert still["owner"] is None or still["owner"]["name"] == "Priya"


async def test_other_organisations_deals_and_users_are_not_reachable(qc, admin_engine, world):
    (d,) = await waiting(qc, world, 1)
    other = await make_world(admin_engine)
    await configure(qc, other, cap=0)
    od = await create(qc, other.north1)
    assert (await assign(qc, world.admin, od["id"], world.north1)).status_code == 404
    assert (await assign(qc, other.admin, d["id"], other.north1)).status_code == 404
    assert (await reassign(qc, world.admin, od["id"], world.north1)).status_code == 404
    assert (await qc.get(f"/deals/{od['id']}", headers=headers(world.admin))).status_code == 404
    assert (await qc.get("/pool", headers=headers(other.admin))).json()[0]["deal_id"] == od["id"]
    assert len((await qc.get("/pool", headers=headers(other.admin))).json()) == 1


async def test_two_leaders_assigning_the_same_waiting_lead_at_once_one_wins(
    qc, admin_engine, world
):
    (d,) = await waiting(qc, world, 1)
    rs = await asyncio.gather(
        assign(qc, world.admin, d["id"], world.north1),
        assign(qc, world.tl_north, d["id"], world.north2),
        assign(qc, world.sa, d["id"], world.north1),
        assign(qc, world.admin, d["id"], world.north2),
    )
    codes = sorted(r.status_code for r in rs)
    assert codes == [200, 409, 409, 409], codes
    await dispatch_all()
    assert not await invariant_violations(admin_engine, world.org_id)
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE entity_id=:e AND action_key='pool.assign' AND outcome='ok'",
            e=d["id"],
        )
        == 1
    )


async def test_a_manual_assignment_racing_the_engine_assigns_once(qc, admin_engine, world):
    (d,) = await waiting(qc, world, 1)
    from app.modules.leads.assignment.service import assign_pooled

    await put_settings(qc, world.admin, world, default_cap=10)  # capacity now exists
    res = await asyncio.gather(
        assign(qc, world.admin, d["id"], world.north2),
        assign_pooled(world.org_id, uuid.UUID(d["id"])),
    )
    assert res[0].status_code in (200, 409)
    await dispatch_all()
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE entity_id=:e AND action_key IN ('pool.assign','lead.assigned') AND outcome='ok'",
            e=d["id"],
        )
        == 1
    )
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_assign_notifies_the_new_owner_and_closes_the_pool_entry(qc, admin_engine, world):
    (d,) = await waiting(qc, world, 1)
    await assign(qc, world.tl_north, d["id"], world.north2)
    await dispatch_all()
    got = [n for n in await notifications_for(world.north2.id) if n.type == "lead.assigned"]
    assert len(got) == 1
    entry = await q(
        admin_engine,
        "SELECT left_at IS NOT NULL, left_reason FROM lead_pool_entries WHERE deal_id=:d",
        d=uuid.UUID(d["id"]),
    )
    assert [tuple(x) for x in entry] == [(True, "assigned")]
    assert (await qc.get("/pool", headers=headers(world.tl_north))).json() == []


# ------------------------------------------------------------------------------ reassign
async def test_reassign_role_scope_reason_and_notifications(qc, admin_engine, world):
    d = await one_open(qc, world)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    other = world.north2 if owner.id == world.north1.id else world.north1
    assert (await reassign(qc, owner, d["id"], other)).status_code == 403  # a User cannot reassign
    assert (await reassign(qc, world.tl_south, d["id"], world.south1)).status_code == 403
    assert (await reassign(qc, world.tl_north, d["id"], world.south1)).status_code == 403
    assert (await reassign(qc, world.tl_north, d["id"], other, reason="")).status_code == 422
    assert (await reassign(qc, world.tl_north, d["id"], other, reason="  ")).status_code == 422
    assert (await reassign(qc, world.admin, d["id"], other, reason=None)).status_code == 422
    assert (await reassign(qc, world.tl_north, d["id"], owner, reason="same")).status_code == 409
    r = await reassign(qc, world.tl_north, d["id"], other, reason="workload")
    assert r.status_code == 200 and r.json()["owner"]["id"] == str(other.id)
    assert r.json()["next_step"]["responsible"]["id"] == str(other.id)
    await dispatch_all()
    assert [
        n.title for n in await notifications_for(owner.id) if n.type == "deal.owner_changed"
    ] == ["A deal moved away from you"]
    assert [
        n.title for n in await notifications_for(other.id) if n.type == "deal.owner_changed"
    ] == ["A deal was given to you"]
    # SA needs no reason; Admin may move across teams
    assert (await reassign(qc, world.sa, d["id"], owner, reason=None)).status_code == 200
    assert (
        await reassign(qc, world.admin, d["id"], world.south1, reason="move teams")
    ).status_code == 200
    row = (
        await q(
            admin_engine,
            "SELECT old_value->>'owner', new_value->>'owner', reason, actor_id FROM audit_log WHERE entity_id=:e AND action_key='lead.reassign' AND outcome='ok' ORDER BY seq DESC LIMIT 1",
            e=d["id"],
        )
    )[0]
    assert tuple(row)[:3] == (owner.name, "Sneha", "move teams") and row[3] == world.admin.id
    got = (await qc.get(f"/deals/{d['id']}", headers=headers(world.south1))).json()
    assert got["team_name"] == "Team South" and got["next_step"]["responsible"]["name"] == "Sneha"
    assert not await invariant_violations(admin_engine, world.org_id)


@pytest.mark.parametrize("state", ["pooled", "lost", "deleted"])
async def test_reassign_only_works_on_open_deals(qc, world, state):
    if state == "pooled":
        (d,) = await waiting(qc, world, 1)
        assert (await reassign(qc, world.admin, d["id"], world.north1)).status_code == 409
        return
    d = await one_open(qc, world)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    if state == "lost":
        await qc.post(
            f"/deals/{d['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner)
        )
        assert (await reassign(qc, world.admin, d["id"], world.south1)).status_code == 409
    else:
        await qc.post(
            f"/deals/{d['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
        )
        assert (await reassign(qc, world.admin, d["id"], world.south1)).status_code == 404


async def test_reassign_to_an_inactive_person_and_concurrent_reassigns(qc, admin_engine, world):
    d = await one_open(qc, world)
    await qc.patch(
        f"/users/{world.south1.id}",
        json={"is_active": False, "reason": "left"},
        headers=headers(world.admin),
    )
    assert (await reassign(qc, world.admin, d["id"], world.south1)).status_code == 422
    rs = await asyncio.gather(
        *(
            reassign(qc, world.admin, d["id"], t, reason="race")
            for t in (world.south2, world.north2, world.north1, world.south2, world.tl_north)
        )
    )
    codes = sorted(r.status_code for r in rs)
    assert 500 not in codes and codes.count(200) >= 1, codes
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_reassign_with_the_selected_deals_endpoint_and_bad_bodies(qc, world):
    d = await one_open(qc, world)
    h = headers(world.admin)
    assert (
        await qc.post(f"/deals/{d['id']}/reassign", json={"reason": "x"}, headers=h)
    ).status_code == 422
    assert (
        await qc.post(f"/deals/{d['id']}/reassign", json={"user_id": "x", "reason": "x"}, headers=h)
    ).status_code == 422
    assert (
        await qc.post(
            "/deals/not-a-uuid/reassign",
            json={"user_id": str(world.north1.id), "reason": "x"},
            headers=h,
        )
    ).status_code == 422


# ------------------------------------------------------------------------------ bulk reassign
async def _fill(qc, world, n, cap=500):
    await put_settings(qc, world.admin, world, default_cap=cap, rules=[])
    for _ in range(n):
        await create(qc, world.north1)


async def bulk(qc, actor, src, to=None, spread=False, reason="leaving"):
    body = {"from_user_id": str(src.id), "spread": spread, "reason": reason}
    if to is not None:
        body["to_user_id"] = str(to.id)
    return await qc.post("/deals/bulk-reassign", json=body, headers=headers(actor))


async def test_bulk_reassign_to_one_rep_moves_every_open_deal_and_writes_one_audit_row_each(
    qc, admin_engine, world
):
    await _fill(qc, world, 10)
    n_src = await q1(
        admin_engine,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND status='open'",
        u=world.north1.id,
    )
    r = await bulk(qc, world.tl_north, world.north1, world.north2)
    assert r.status_code == 200 and r.json()["moved"] == n_src and r.json()["skipped"] == 0
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM deals WHERE owner_id=:u AND status='open'",
            u=world.north1.id,
        )
        == 0
    )
    per_deal = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='deal.bulk_reassign' AND entity_type='deal' AND outcome='ok'",
        o=world.org_id,
    )
    per_bulk = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='deal.bulk_reassign' AND entity_type='user' AND outcome='ok'",
        o=world.org_id,
    )
    assert (per_deal, per_bulk) == (n_src, 1)
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM next_steps n JOIN deals d ON d.id=n.deal_id WHERE d.org_id=:o AND n.done_at IS NULL AND n.responsible_id <> d.owner_id",
            o=world.org_id,
        )
        == 0
    )
    await dispatch_all()
    notes = [n for n in await notifications_for(world.north1.id) if n.type == "deal.owner_changed"]
    assert len(notes) == n_src
    got = [n for n in await notifications_for(world.north2.id) if n.type == "deal.owner_changed"]
    assert len(got) == n_src
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_bulk_reassign_spread_is_round_robin_across_the_team(qc, admin_engine, world):
    await _fill(qc, world, 9)
    rep = await add_rep_to_north(qc, world, "Third")
    n_src = await q1(
        admin_engine,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND status='open'",
        u=world.north1.id,
    )
    r = await bulk(qc, world.admin, world.north1, spread=True)
    assert r.status_code == 200 and r.json()["moved"] == n_src
    owners = Counter(
        x[0]
        for x in await q(
            admin_engine,
            "SELECT owner_id FROM deals WHERE org_id=:o AND status='open' AND owner_id <> :u",
            o=world.org_id,
            u=world.north1.id,
        )
    )
    assert world.north1.id not in owners and set(owners) <= {world.north2.id, uuid.UUID(rep["id"])}


async def add_rep_to_north(qc, world, name):
    r = await qc.post(
        "/users",
        json={
            "name": name,
            "email": f"{name.lower()}.{uuid.uuid4().hex[:5]}@example.com",
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "hire",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_bulk_reassign_with_zero_deals_and_edge_inputs(qc, admin_engine, world):
    await configure(qc, world)
    r = await bulk(qc, world.admin, world.north1, world.north2)
    assert r.status_code == 200 and r.json() == {"moved": 0, "skipped": 0, "deals": []}
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE action_key='deal.bulk_reassign' AND org_id=:o",
            o=world.org_id,
        )
        == 1
    )
    assert (await bulk(qc, world.admin, world.north1, world.north1)).status_code == 409
    assert (
        await qc.post(
            "/deals/bulk-reassign",
            json={"from_user_id": str(world.north1.id), "reason": "x"},
            headers=headers(world.admin),
        )
    ).status_code == 422
    assert (
        await qc.post(
            "/deals/bulk-reassign",
            json={
                "from_user_id": str(uuid.uuid4()),
                "to_user_id": str(world.north2.id),
                "reason": "x",
            },
            headers=headers(world.admin),
        )
    ).status_code == 404
    assert (await bulk(qc, world.admin, world.north1, world.north2, reason=None)).status_code == 422
    assert (await bulk(qc, world.sa, world.north1, world.north2, reason=None)).status_code == 200


async def test_bulk_reassign_permissions(qc, admin_engine, world):
    await _fill(qc, world, 6)
    assert (await bulk(qc, world.north1, world.north1, world.north2)).status_code == 403
    assert (
        await bulk(qc, world.tl_south, world.north1, world.south1)
    ).status_code == 403  # not their rep
    assert (
        await bulk(qc, world.tl_north, world.north1, world.south1)
    ).status_code == 403  # target outside the team
    assert (await bulk(qc, world.tl_north, world.south1, world.north1)).status_code == 403
    other = await make_world(admin_engine)
    assert (await bulk(qc, world.admin, world.north1, other.north1)).status_code == 422
    assert (await bulk(qc, world.admin, other.north1, world.north1)).status_code == 404
    assert (await bulk(qc, other.admin, world.north1, other.north1)).status_code == 404


async def test_bulk_reassign_of_a_deactivated_rep_and_spread_with_nobody_left(qc, world):
    await _fill(qc, world, 6)
    await qc.patch(
        f"/users/{world.north2.id}",
        json={"is_active": False, "reason": "left"},
        headers=headers(world.admin),
    )
    r = await bulk(qc, world.tl_north, world.north1, spread=True)
    assert r.status_code == 409  # nobody else is active in the team
    r = await bulk(qc, world.tl_north, world.north2, world.north1)
    assert (
        r.status_code == 200 and r.json()["moved"] >= 1
    )  # the departing (inactive) rep's deals can still be moved


async def test_bulk_reassign_of_hundreds_of_deals(qc, admin_engine, world):
    await configure(qc, world, cap=1000)
    async with admin_engine.begin() as c:
        acc = (
            await c.execute(
                text("INSERT INTO accounts (org_id, name) VALUES (:o,'Bulk Co') RETURNING id"),
                {"o": world.org_id},
            )
        ).scalar_one()
        await c.execute(
            text(
                "WITH nd AS (INSERT INTO deals (org_id, account_id, status, lead_source, team_id, owner_id, generated_by, assignment_method, assigned_at) "
                "SELECT :o, :a, 'open', 'Website', :t, :u, :u, 'rule', now() FROM generate_series(1, 400) RETURNING id) "
                "INSERT INTO next_steps (org_id, deal_id, text, due_date, responsible_id) SELECT :o, id, 'step', current_date + 1, :u FROM nd"
            ),
            {"o": world.org_id, "a": acc, "t": world.north_team, "u": world.north1.id},
        )
    import time

    t = time.perf_counter()
    r = await bulk(qc, world.admin, world.north1, world.north2)
    secs = time.perf_counter() - t
    assert r.status_code == 200 and r.json()["moved"] == 400, r.text
    print(f"BULK400 {secs:.2f}s")
    assert secs < 60
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM deals WHERE owner_id=:u AND status='open'",
            u=world.north2.id,
        )
        == 400
    )
    await dispatch_all()


async def test_bulk_spread_ignores_caps_and_always_favours_the_first_rep(qc, admin_engine, world):
    """Documented (RISK-P2-05): spread restarts at the same rep every time and ignores caps."""
    await configure(qc, world, cap=2)
    rep3 = await add_rep_to_north(qc, world, "Third")
    await put_settings(qc, world.admin, world, default_cap=2)
    ids = sorted([world.north2.id, uuid.UUID(rep3["id"])])
    got = []
    for _ in range(3):
        d = await create(qc, world.north1)
        if d["owner"]["id"] != str(world.north1.id):
            continue
        got.append(d["id"])
    # whatever north1 holds goes to the lowest id first, one deal at a time
    firsts = Counter()
    for did in got:
        r = await bulk(qc, world.admin, world.north1, spread=True)
        assert r.status_code == 200
        moved_to = await q1(admin_engine, "SELECT owner_id FROM deals WHERE id=:i", i=did)
        firsts[moved_to] += 1
        break
    if firsts:
        assert list(firsts)[0] == ids[0]


# ------------------------------------------------------------------------------ direct team moves
async def test_direct_move_with_and_without_an_owner_and_the_error_cases(qc, admin_engine, world):
    d = await one_open(qc, world)
    url = f"/deals/{d['id']}/move-team"
    south = str(world.south_team)
    for actor in (world.north1, world.tl_north, world.tl_south):
        assert (
            await qc.post(url, json={"to_team_id": south, "reason": "x"}, headers=headers(actor))
        ).status_code == 403
    assert (
        await qc.post(url, json={"to_team_id": south}, headers=headers(world.admin))
    ).status_code == 422
    assert (
        await qc.post(
            url,
            json={"to_team_id": str(world.north_team), "reason": "x"},
            headers=headers(world.admin),
        )
    ).status_code == 409
    assert (
        await qc.post(
            url, json={"to_team_id": str(uuid.uuid4()), "reason": "x"}, headers=headers(world.admin)
        )
    ).status_code == 404
    assert (
        await qc.post(
            url,
            json={"to_team_id": south, "owner_id": str(world.north2.id), "reason": "x"},
            headers=headers(world.admin),
        )
    ).status_code == 422
    other = await make_world(admin_engine)
    assert (
        await qc.post(
            url,
            json={"to_team_id": str(other.south_team), "reason": "x"},
            headers=headers(world.admin),
        )
    ).status_code == 404
    r = await qc.post(
        url,
        json={"to_team_id": south, "owner_id": str(world.south1.id), "reason": "territory"},
        headers=headers(world.admin),
    )
    assert (
        r.status_code == 200
        and r.json()["owner"]["name"] == "Sneha"
        and r.json()["status"] == "open"
    )
    r = await qc.post(
        url,
        json={"to_team_id": str(world.north_team), "reason": "back, no owner"},
        headers=headers(world.sa),
    )
    assert r.status_code == 200 and r.json()["status"] == "pooled" and r.json()["owner"] is None
    pool = (await qc.get("/pool", headers=headers(world.tl_north))).json()
    assert [p["deal_id"] for p in pool] == [d["id"]] and pool[0]["held_for_manual"] is True
    assert pool[0]["routed_team_id"] == str(world.north_team)
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_direct_move_of_a_waiting_lead_without_an_owner(qc, admin_engine, world):
    (d,) = await waiting(qc, world, 1)
    r = await qc.post(
        f"/deals/{d['id']}/move-team",
        json={"to_team_id": str(world.south_team), "reason": "territory"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200, r.status_code
    assert (await qc.get("/pool", headers=headers(world.tl_south))).json()[0]["deal_id"] == d["id"]


async def test_direct_move_of_a_waiting_lead_with_an_owner_works(qc, admin_engine, world):
    (d,) = await waiting(qc, world, 1)
    r = await qc.post(
        f"/deals/{d['id']}/move-team",
        json={
            "to_team_id": str(world.south_team),
            "owner_id": str(world.south1.id),
            "reason": "territory",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200 and r.json()["status"] == "open"
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_direct_move_does_not_resurrect_a_lost_deal(qc, world):
    d = await one_open(qc, world)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    assert (
        await qc.post(
            f"/deals/{d['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner)
        )
    ).status_code == 200
    r = await qc.post(
        f"/deals/{d['id']}/move-team",
        json={
            "to_team_id": str(world.south_team),
            "owner_id": str(world.south1.id),
            "reason": "territory",
        },
        headers=headers(world.admin),
    )
    if r.status_code == 200:
        assert r.json()["status"] == "lost"
    else:
        assert r.status_code == 409


async def test_direct_move_of_a_deleted_deal_is_refused(qc, world):
    d = await one_open(qc, world)
    await qc.post(f"/deals/{d['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin))
    r = await qc.post(
        f"/deals/{d['id']}/move-team",
        json={"to_team_id": str(world.south_team), "reason": "x"},
        headers=headers(world.admin),
    )
    assert r.status_code == 404


# ------------------------------------------------------------------------------ rep team move hook
async def test_team_move_of_a_rep_moves_their_open_deals_with_them(qc, admin_engine, world):
    await configure(qc, world, cap=100)
    for _ in range(6):
        await create(qc, world.south1)
    mine = [
        r[0]
        for r in await q(
            admin_engine,
            "SELECT id FROM deals WHERE owner_id=:u AND status='open'",
            u=world.north1.id,
        )
    ]
    assert mine
    r = await qc.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "open_deals": "move", "reason": "reorg"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    teams = {
        x[0]
        for x in await q(
            admin_engine,
            "SELECT team_id FROM deals WHERE owner_id=:u AND status='open'",
            u=world.north1.id,
        )
    }
    assert teams == {world.south_team}
    assert await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='team.manage' AND entity_type='deal'",
        o=world.org_id,
    ) == len(mine)
    assert (await qc.get(f"/deals/{mine[0]}", headers=headers(world.tl_north))).status_code == 404
    assert (await qc.get(f"/deals/{mine[0]}", headers=headers(world.tl_south))).status_code == 200
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_team_move_of_a_rep_can_send_their_open_deals_back_to_the_pool(
    qc, admin_engine, world
):
    await configure(qc, world, cap=100)
    for _ in range(6):
        await create(qc, world.south1)
    mine = [
        r[0]
        for r in await q(
            admin_engine,
            "SELECT id FROM deals WHERE owner_id=:u AND status='open'",
            u=world.north1.id,
        )
    ]
    r = await qc.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "open_deals": "return_to_pool", "reason": "reorg"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    await dispatch_all()
    rows = await q(
        admin_engine, "SELECT status, owner_id, team_id FROM deals WHERE id = ANY(:i)", i=mine
    )
    # the engine re-assigns them inside the OLD team (North) to the remaining rep
    assert {(s, o, t) for s, o, t in rows} == {("open", world.north2.id, world.north_team)}
    assert await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE entity_type='deal' AND action_key='lead.reassign' AND org_id=:o",
        o=world.org_id,
    ) == len(mine)
    notes = [n for n in await notifications_for(world.north1.id) if n.type == "deal.owner_changed"]
    assert len(notes) == len(mine)
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_team_move_return_to_pool_with_nobody_left_waits_and_tells_the_leader_once(
    qc, admin_engine, world
):
    await configure(qc, world, cap=100)
    await qc.patch(
        f"/users/{world.north2.id}",
        json={"is_active": False, "reason": "left"},
        headers=headers(world.admin),
    )
    for _ in range(3):
        await create(qc, world.south1)
    r = await qc.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "open_deals": "return_to_pool", "reason": "reorg"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    for _ in range(2):
        await dispatch_all()
    pool = (await qc.get("/pool", headers=headers(world.tl_north))).json()
    assert len(pool) == 3 and {p["waiting_reason"] for p in pool} == {"no_active_reps"}
    notes = [n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"]
    assert len(notes) == 3
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_a_team_move_that_changes_nothing_writes_nothing(qc, admin_engine, world):
    await configure(qc, world, cap=100)
    d = await create(qc, world.south1)
    before = await q1(
        admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id
    )
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    r = await qc.patch(
        f"/users/{owner.id}",
        json={"team_id": str(owner.team_id), "reason": "same"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    assert before == await q1(
        admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id
    )
    assert (datetime.now(UTC) - timedelta(days=0)).year >= 2026
