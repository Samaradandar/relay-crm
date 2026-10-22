# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""A lead a sales rep creates is NOT auto-assigned: it waits in the Pool for the Team Leader, who
gives it to the creator, to another rep, or lets round robin pick. After the hold window (an org
setting, default 20 minutes since migration 0026) the 15-minute job falls back to round robin."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.modules.leads import service as leads_service
from app.modules.leads.assignment.service import expire_rep_holds
from tests.helpers import dispatch_all, headers, notifications_for
from tests.modules.test_leads_api import audit_keys, configure, lead

T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


async def create(client, actor, **kw):
    """POST /leads as is (the shared helper settles a rep's held lead; these tests must not)."""
    r = await client.post("/leads", json=lead(**kw), headers=headers(actor))
    assert r.status_code == 201, r.text
    return r.json()


async def decide(client, actor, deal_id, action, user_id=None, reason=None):
    body = {"action": action}
    if user_id is not None:
        body["user_id"] = str(user_id)
    if reason is not None:
        body["reason"] = reason
    return await client.post(f"/pool/{deal_id}/decide", json=body, headers=headers(actor))


async def set_entered(admin_engine, deal_id, when):
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE lead_pool_entries SET entered_at = :t WHERE deal_id = :d"),
            {"t": when, "d": deal_id},
        )


async def pointer(admin_engine, org_id):
    async with admin_engine.connect() as c:
        rows = (
            await c.execute(
                text("SELECT scope_id, last_assigned_user_id FROM rr_pointers WHERE org_id = :o"),
                {"o": org_id},
            )
        ).all()
    return {str(a): str(b) for a, b in rows}


async def entry(admin_engine, deal_id):
    async with admin_engine.connect() as c:
        return (
            (
                await c.execute(
                    text(
                        "SELECT rep_hold, manual_hold, tl_notified, left_at FROM lead_pool_entries "
                        "WHERE deal_id = :d"
                    ),
                    {"d": deal_id},
                )
            )
            .mappings()
            .one()
        )


def types(notes, t):
    return [n for n in notes if n.type == t]


# ------------------------------------------------------------------------------ the hold
async def test_rep_lead_is_held_not_assigned(client, world, admin_engine):
    await configure(client, world)
    d = await create(client, world.north1)
    assert d["status"] == "pooled" and d["owner"] is None and d["awaiting_team_leader"] is True
    assert d["team_name"] == "Team North" and d["generated_by"] == "Priya"
    e = await entry(admin_engine, d["id"])
    assert e["rep_hold"] and e["manual_hold"] and e["left_at"] is None
    assert await audit_keys(d["id"]) == [("lead.create", "ok")]
    await dispatch_all()
    await dispatch_all()  # the safety net must not assign a held lead
    got = (await client.get(f"/deals/{d['id']}", headers=headers(world.tl_north))).json()
    assert got["status"] == "pooled" and got["owner"] is None
    # nothing moved the round-robin pointer
    assert await pointer(admin_engine, world.org_id) == {}


async def test_tl_notified_once_and_rep_told(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    for _ in range(3):
        await dispatch_all()
    tl = types(await notifications_for(world.tl_north.id), "lead.rep_waiting")
    assert len(tl) == 1 and "Priya" in tl[0].body and tl[0].link == "/pool"
    rep = types(await notifications_for(world.north1.id), "lead.rep_waiting.creator")
    assert len(rep) == 1 and rep[0].title == "Your lead is waiting for your Team Leader"
    assert not types(await notifications_for(world.tl_south.id), "lead.rep_waiting")
    # a second lead is a second notification, never a repeat for the first
    await create(client, world.north2)
    await dispatch_all()
    assert len(types(await notifications_for(world.tl_north.id), "lead.rep_waiting")) == 2
    _ = d


async def test_no_rule_routes_to_the_creators_own_team(client, world, admin_engine):
    await configure(client, world)  # default team is North, no rules
    d = await create(client, world.south1)  # a South rep
    assert d["team_name"] == "Team South" and d["status"] == "pooled"
    await dispatch_all()
    assert len(types(await notifications_for(world.tl_south.id), "lead.rep_waiting")) == 1
    assert not types(await notifications_for(world.tl_north.id), "lead.rep_waiting")
    south = (await client.get("/pool", headers=headers(world.tl_south))).json()
    north = (await client.get("/pool", headers=headers(world.tl_north))).json()
    assert [p["deal_id"] for p in south] == [d["id"]] and north == []
    assert south[0]["routed_team_name"] == "Team South" and south[0]["rep_hold"] is True
    # round robin chosen by the South TL picks a SOUTH rep, not a default-team (North) rep
    r = await decide(client, world.tl_south, d["id"], "auto")
    assert r.status_code == 200, r.text
    assert r.json()["owner"]["id"] in {str(world.south1.id), str(world.south2.id)}


async def test_matching_rule_team_wins_and_only_that_tl_is_told(client, world):
    rule = {
        "name": "web to south",
        "priority": 1,
        "active": True,
        "match": {"lead_source": "Website"},
        "target_type": "team",
        "target_id": str(world.south_team),
    }
    r = await client.put(
        "/settings/assignment",
        json={
            "default_team_id": str(world.north_team),
            "default_cap": 25,
            "rules": [rule],
            "groups": [],
            "caps": {},
            "reason": "setup",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200, r.text
    d = await create(client, world.north1)  # a North rep, rule says South
    assert d["team_name"] == "Team South"
    await dispatch_all()
    assert len(types(await notifications_for(world.tl_south.id), "lead.rep_waiting")) == 1
    assert not types(await notifications_for(world.tl_north.id), "lead.rep_waiting")
    # the North TL cannot decide it; the South TL can, but the creator is in North
    assert (await decide(client, world.tl_north, d["id"], "auto")).status_code == 403
    pool = (await client.get("/pool", headers=headers(world.tl_south))).json()
    assert pool[0]["creator_assignable"] is True
    admin_pool = (await client.get("/pool", headers=headers(world.admin))).json()
    assert admin_pool[0]["creator_assignable"] is True
    r = await decide(client, world.tl_south, d["id"], "give_creator")
    assert r.status_code == 200 and r.json()["owner"]["id"] == str(world.north1.id)


# ------------------------------------------------------------------------------ the three choices
async def test_give_to_the_creator(client, world, admin_engine):
    await configure(client, world)
    d = await create(client, world.north1)
    await dispatch_all()
    r = await decide(client, world.tl_north, d["id"], "give_creator")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "open" and out["owner"]["id"] == str(world.north1.id)
    assert out["assignment_method"] == "manual"
    got = (await client.get(f"/deals/{d['id']}", headers=headers(world.north1))).json()
    assert got["assigned_by"] == "Tara" and got["next_step"]["responsible"]["id"] == str(
        world.north1.id
    )
    assert await audit_keys(d["id"]) == [("lead.create", "ok"), ("pool.assign", "ok")]
    await dispatch_all()
    assert len(types(await notifications_for(world.north1.id), "lead.assigned")) == 1
    assert await pointer(admin_engine, world.org_id) == {}  # manual picks leave the pointer alone
    assert (await client.get("/pool", headers=headers(world.tl_north))).json() == []
    assert (await entry(admin_engine, d["id"]))["left_at"] is not None


async def test_give_to_another_rep(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    r = await decide(client, world.tl_north, d["id"], "give_rep", world.north2.id)
    assert r.status_code == 200, r.text
    assert r.json()["owner"]["id"] == str(world.north2.id)
    # the creator no longer owns it and cannot open it (scoping unchanged)
    assert (await client.get(f"/deals/{d['id']}", headers=headers(world.north1))).status_code == 404
    await dispatch_all()
    assert len(types(await notifications_for(world.north2.id), "lead.assigned")) == 1
    assert not types(await notifications_for(world.north1.id), "lead.assigned")


async def test_give_rep_needs_a_valid_rep_in_the_tl_team(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    assert (await decide(client, world.tl_north, d["id"], "give_rep")).status_code == 422
    assert (
        await decide(client, world.tl_north, d["id"], "give_rep", world.south1.id)
    ).status_code == 403
    assert (
        await decide(client, world.tl_north, d["id"], "give_rep", world.sa.id)
    ).status_code == 403  # no team: outside the TL's team
    got = (await client.get(f"/deals/{d['id']}", headers=headers(world.tl_north))).json()
    assert got["status"] == "pooled"


async def test_auto_round_robin_moves_the_pointer(client, world, admin_engine):
    await configure(client, world)
    a = await create(client, world.north1)
    b = await create(client, world.north1)
    ra = await decide(client, world.tl_north, a["id"], "auto")
    assert ra.status_code == 200, ra.text
    first = ra.json()["owner"]
    assert first and ra.json()["assignment_method"] == "round_robin"
    ptr = await pointer(admin_engine, world.org_id)
    assert list(ptr.values()) == [first["id"]]
    # a manual pick in between does not move the pointer
    c = await create(client, world.north2)
    assert (await decide(client, world.tl_north, c["id"], "give_creator")).status_code == 200
    assert await pointer(admin_engine, world.org_id) == ptr
    rb = await decide(client, world.tl_north, b["id"], "auto")
    assert rb.json()["owner"]["id"] != first["id"]  # the next rep in the rotation
    keys = await audit_keys(a["id"])
    assert keys == [("lead.create", "ok"), ("pool.assign", "ok"), ("lead.assigned", "ok")]


async def test_auto_with_everyone_at_cap_waits_and_tells_the_tl_again(client, world):
    await configure(client, world, cap=0)
    d = await create(client, world.north1)
    await dispatch_all()
    r = await decide(client, world.tl_north, d["id"], "auto")
    assert r.status_code == 200 and r.json()["status"] == "pooled"
    await dispatch_all()
    pool = (await client.get("/pool", headers=headers(world.tl_north))).json()
    assert pool[0]["rep_hold"] is False and pool[0]["waiting_reason"] == "all_reps_at_cap"
    assert len(types(await notifications_for(world.tl_north.id), "lead.waiting")) == 1


# ------------------------------------------------------------------------------ who may decide
async def test_permissions(client, world, admin_engine):
    await configure(client, world)
    d = await create(client, world.north1)
    for actor in (world.north1, world.north2, world.south1):
        r = await decide(client, actor, d["id"], "auto")
        assert r.status_code == 403, (actor.name, r.text)
    assert (await decide(client, world.tl_south, d["id"], "auto")).status_code == 403
    assert (await entry(admin_engine, d["id"]))["rep_hold"] is True
    # the rep cannot bypass the hold through owner_id or the old Pool endpoints
    body = {**lead(), "owner_id": str(world.north2.id)}
    assert (
        await client.post("/leads", json=body, headers=headers(world.north1))
    ).status_code == 403
    old = await client.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(world.north1.id), "reason": "x"},
        headers=headers(world.north1),
    )
    assert old.status_code == 403
    assert (await client.get("/pool", headers=headers(world.north1))).status_code == 403
    # an Admin may decide, with a reason (routine action); a Team Leader needs none
    assert (await decide(client, world.admin, d["id"], "auto")).status_code == 422
    assert (await decide(client, world.admin, d["id"], "auto", reason="ok")).status_code == 200
    e = await create(client, world.north1)
    assert (await decide(client, world.sa, e["id"], "give_creator")).status_code == 200


async def test_old_pool_assign_still_works_for_the_tl_and_ends_the_hold(
    client, world, admin_engine
):
    await configure(client, world)
    d = await create(client, world.north1)
    r = await client.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(world.north2.id), "reason": "balance"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200, r.text
    assert (await entry(admin_engine, d["id"]))["left_at"] is not None


async def test_inactive_creator(client, world, admin_engine):
    await configure(client, world)
    d = await create(client, world.north1)
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE users SET is_active = false WHERE id = :u"), {"u": world.north1.id}
        )
    pool = (await client.get("/pool", headers=headers(world.tl_north))).json()
    assert pool[0]["creator_assignable"] is False
    r = await decide(client, world.tl_north, d["id"], "give_creator")
    assert r.status_code == 422 and "no longer active" in r.json()["message"]
    ok = await decide(client, world.tl_north, d["id"], "auto")  # still decidable
    assert ok.status_code == 200 and ok.json()["owner"]["id"] == str(world.north2.id)


async def test_second_decision_conflicts_and_non_held_leads_are_refused(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    assert (await decide(client, world.tl_north, d["id"], "give_creator")).status_code == 200
    assert (await decide(client, world.tl_north, d["id"], "auto")).status_code == 409
    waiting = await create(client, world.tl_north)  # TL-created: not held
    assert waiting["status"] == "open"
    assert (await decide(client, world.tl_north, waiting["id"], "auto")).status_code == 409


async def test_concurrent_decisions_have_one_winner(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    rs = await asyncio.gather(
        decide(client, world.tl_north, d["id"], "give_creator"),
        decide(client, world.tl_north, d["id"], "give_rep", world.north2.id),
        decide(client, world.admin, d["id"], "auto", reason="r"),
    )
    assert sorted(r.status_code for r in rs) == [200, 409, 409], [r.text for r in rs]
    keys = await audit_keys(d["id"])
    assert keys.count(("pool.assign", "ok")) == 1


async def test_failure_after_the_assignment_leaves_the_hold(
    world, database, admin_engine, monkeypatch
):
    from app.main import create_app

    await_real = leads_service.apply_owner

    async def boom(*a, **kw):
        await await_real(*a, **kw)
        raise RuntimeError("simulated failure")

    transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await configure(c, world)
        d = await create(c, world.north1)
        monkeypatch.setattr(leads_service, "apply_owner", boom)
        r = await decide(c, world.tl_north, d["id"], "give_creator")
        assert r.status_code == 500
    e = await entry(admin_engine, d["id"])
    assert e["rep_hold"] and e["left_at"] is None
    assert await audit_keys(d["id"]) == [("lead.create", "ok")]


# ------------------------------------------------------------------------------ expiry job
async def test_expiry_at_the_window_boundary_with_a_controlled_clock(client, world, admin_engine):
    await configure(client, world)
    d = await create(client, world.north1)
    await set_entered(admin_engine, d["id"], T0)
    window = timedelta(minutes=20)  # the default since migration 0026
    assert await expire_rep_holds(world.org_id, T0 + window - timedelta(seconds=1)) == 0
    assert (await entry(admin_engine, d["id"]))["rep_hold"] is True
    assert await expire_rep_holds(world.org_id, T0 + window) == 1  # exactly at the window
    got = (await client.get(f"/deals/{d['id']}", headers=headers(world.tl_north))).json()
    assert got["status"] == "open" and got["assignment_method"] == "round_robin"
    assert got["assigned_by"] == "System"
    keys = await audit_keys(d["id"])
    assert keys == [("lead.create", "ok"), ("lead.hold_expired", "ok"), ("lead.assigned", "ok")]
    # idempotent: running again changes nothing
    assert await expire_rep_holds(world.org_id, T0 + window * 2) == 0
    assert await audit_keys(d["id"]) == keys
    await dispatch_all()
    assert len(types(await notifications_for(world.tl_north.id), "lead.rep_waiting")) == 1


async def test_expiry_does_not_touch_decided_or_other_leads(client, world, admin_engine):
    await configure(client, world)
    decided = await create(client, world.north1)
    fresh = await create(client, world.north2)
    other_org_free = await create(client, world.tl_north)  # not held at all
    await decide(client, world.tl_north, decided["id"], "give_creator")
    await set_entered(admin_engine, decided["id"], T0)
    await set_entered(admin_engine, fresh["id"], T0 + timedelta(minutes=5))
    assert await expire_rep_holds(world.org_id, T0 + timedelta(minutes=21)) == 0  # 20-min window
    got = (await client.get(f"/deals/{decided['id']}", headers=headers(world.admin))).json()
    assert got["owner"]["id"] == str(world.north1.id) and got["assignment_method"] == "manual"
    assert (await entry(admin_engine, fresh["id"]))["rep_hold"] is True
    assert other_org_free["status"] == "open"


async def test_expiry_with_nobody_eligible_waits_and_tells_the_tl(client, world, admin_engine):
    await configure(client, world, cap=0)
    d = await create(client, world.north1)
    await set_entered(admin_engine, d["id"], T0)
    assert await expire_rep_holds(world.org_id, T0 + timedelta(minutes=120)) == 1
    await dispatch_all()
    pool = (await client.get("/pool", headers=headers(world.tl_north))).json()
    assert pool[0]["waiting_reason"] == "all_reps_at_cap" and pool[0]["rep_hold"] is False
    assert len(types(await notifications_for(world.tl_north.id), "lead.waiting")) == 1


async def test_the_window_is_the_org_setting(client, world, admin_engine):
    await configure(client, world)
    r = await client.put(
        "/settings/assignment",
        json={
            "default_team_id": str(world.north_team),
            "default_cap": 25,
            "rep_lead_hold_minutes": 30,
            "reason": "tighter",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200 and r.json()["rep_lead_hold_minutes"] == 30
    d = await create(client, world.north1)
    await set_entered(admin_engine, d["id"], T0)
    pool = (await client.get("/pool", headers=headers(world.tl_north))).json()
    assert pool[0]["auto_assign_at"].startswith("2026-03-02T10:30:00")
    assert await expire_rep_holds(world.org_id, T0 + timedelta(minutes=29, seconds=59)) == 0
    assert await expire_rep_holds(world.org_id, T0 + timedelta(minutes=30)) == 1


# ------------------------------------------------------------------------------ settings
async def put_settings(client, actor, **extra):
    body = {
        "default_team_id": extra.pop("team", None),
        "default_cap": 25,
        "rules": [],
        "groups": [],
        "caps": {},
        **extra,
    }
    return await client.put("/settings/assignment", json=body, headers=headers(actor))


async def test_hold_setting_default_validation_and_audit(client, world):
    await configure(client, world)
    got = (await client.get("/settings/assignment", headers=headers(world.admin))).json()
    assert got["rep_lead_hold_minutes"] == 20  # the default since migration 0026
    team = str(world.north_team)
    for bad in (0, -5, 10081, "x", 1.5):
        r = await put_settings(
            client, world.admin, team=team, rep_lead_hold_minutes=bad, reason="r"
        )
        assert r.status_code == 422, (bad, r.text)
    # a reason is required for an Admin, a Team Leader and a rep cannot change it
    assert (
        await put_settings(client, world.admin, team=team, rep_lead_hold_minutes=60)
    ).status_code == 422
    for actor in (world.tl_north, world.north1):
        r = await put_settings(client, actor, team=team, rep_lead_hold_minutes=60, reason="r")
        assert r.status_code == 403
    for ok in (1, 10080):
        r = await put_settings(client, world.admin, team=team, rep_lead_hold_minutes=ok, reason="r")
        assert r.status_code == 200 and r.json()["rep_lead_hold_minutes"] == ok
    # omitted = unchanged
    r = await put_settings(client, world.admin, team=team, reason="keep")
    assert r.json()["rep_lead_hold_minutes"] == 10080
    from sqlalchemy import select

    from app.core.db import get_sessionmaker
    from app.platform.audit.models import AuditLog

    async with get_sessionmaker()() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.org_id == world.org_id,
                        AuditLog.action_key == "assignment.config",
                        AuditLog.outcome == "ok",
                    )
                    .order_by(AuditLog.seq)
                )
            )
            .scalars()
            .all()
        )
    changes = [
        (r.old_value["rep_lead_hold_minutes"], r.new_value["rep_lead_hold_minutes"]) for r in rows
    ]
    assert (20, 1) in changes and (1, 10080) in changes and (10080, 10080) in changes
    assert all(r.reason for r in rows)


# ------------------------------------------------------------------------------ creator's view
async def test_creator_sees_only_their_own_waiting_leads(client, world):
    await configure(client, world)
    mine = await create(client, world.north1, company="Mine Co")
    await create(client, world.north2, company="Theirs Co")
    r = await client.get("/leads/waiting", headers=headers(world.north1))
    assert r.status_code == 200
    rows = r.json()
    assert [x["company"] for x in rows] == ["Mine Co"] and set(rows[0]) == {
        "deal_id",
        "company",
        "created_at",
        "auto_assign_at",
    }
    # the deal itself stays closed to the creator: scoping is not widened
    assert (
        await client.get(f"/deals/{mine['id']}", headers=headers(world.north1))
    ).status_code == 404
    assert (await client.get("/leads/waiting", headers=headers(world.north2))).json()[0][
        "company"
    ] == "Theirs Co"
    await decide(client, world.tl_north, mine["id"], "give_creator")
    assert (await client.get("/leads/waiting", headers=headers(world.north1))).json() == []


@pytest.mark.parametrize("role", ["tl_north", "admin", "sa"])
async def test_leads_by_leaders_are_not_held(client, world, role):
    await configure(client, world)
    d = await create(client, getattr(world, role), reason="r")
    assert d["status"] == "open" and d["awaiting_team_leader"] is False
