# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 3: the assignment engine (rules, round-robin, caps, waiting)."""

import asyncio
import uuid
from collections import Counter

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from tests.deep.phase2.support import (
    configure,
    headers,
    invariant_violations,
    lead,
    put_settings,
    q,
    q1,
)
from tests.deep.phase2.support import (
    create_via_leader as create,
)
from tests.helpers import dispatch_all, notifications_for


def rule(name, priority, match, target_type, target_id, active=True):
    return {
        "name": name,
        "priority": priority,
        "active": active,
        "match": match,
        "target_type": target_type,
        "target_id": str(target_id),
    }


async def add_rep(qc, world, name, team=None):
    r = await qc.post(
        "/users",
        json={
            "name": name,
            "email": f"{name.lower()}.{uuid.uuid4().hex[:5]}@example.com",
            "role": "user",
            "team_id": str(team or world.north_team),
            "reason": "new hire",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def patch_user(qc, world, uid, expect=200, **body):
    r = await qc.patch(f"/users/{uid}", json={"reason": "qa", **body}, headers=headers(world.admin))
    assert r.status_code == expect, r.text
    return r.json()


async def deal(qc, world, did):
    r = await qc.get(f"/deals/{did}", headers=headers(world.admin))
    assert r.status_code == 200, r.text
    return r.json()


async def owner_counts(qc, world):
    deals = (await qc.get("/deals", params={"limit": 500}, headers=headers(world.admin))).json()
    return Counter(d["owner"]["name"] for d in deals if d["owner"])


# ------------------------------------------------------------------------------ rules
async def test_first_matching_rule_by_priority_wins_and_inactive_rules_are_skipped(qc, world):
    rules = [
        rule("Late", 20, {"lead_source": "Website"}, "user", world.south2.id),
        rule("Early", 5, {"lead_source": "Website"}, "user", world.south1.id),
        rule("Off", 1, {"lead_source": "Website"}, "user", world.north1.id, active=False),
    ]
    await put_settings(qc, world.admin, world, rules=rules)
    d = await create(qc, world.north2, lead_source="Website")
    assert (
        d["owner"]["name"] == "Sneha"
        and d["assignment_method"] == "rule"
        and d["assignment_rule"] == "Early"
    )
    assert d["assigned_by"] == "System"
    other = await create(qc, world.north2, lead_source="Referral")  # no rule: default team North
    assert other["assignment_method"] == "round_robin" and other["team_name"] == "Team North"


async def test_rule_match_on_one_several_and_no_fields_and_case_insensitive_values(qc, world):
    rules = [
        rule(
            "Two fields",
            1,
            {"lead_source": "Referral", "industry": "retail"},
            "user",
            world.south1.id,
        ),
        rule("City", 2, {"city": "PUNE"}, "user", world.south2.id),
        rule("Catch all", 99, {}, "user", world.north2.id),
    ]
    await put_settings(qc, world.admin, world, rules=rules)
    a = await create(qc, world.north1, lead_source="Referral", industry="Retail")
    assert a["assignment_rule"] == "Two fields"
    b = await create(
        qc, world.north1, lead_source="Referral", industry="Other"
    )  # only one of two fields
    assert b["assignment_rule"] == "Catch all"
    c = await create(qc, world.north1, city="pune")
    assert c["assignment_rule"] == "City"
    d = await create(qc, world.north1, city="  Pune  ")
    assert d["assignment_rule"] == "City"  # values are trimmed on save and on compare
    e = await create(qc, world.north1)
    assert e["assignment_rule"] == "Catch all"


async def test_duplicate_priorities_resolve_by_a_stable_order(qc, world):
    ids = [uuid.UUID(int=1), uuid.UUID(int=2)]
    rules = [
        {**rule("Second", 5, {}, "user", world.south2.id), "id": str(ids[1])},
        {**rule("First", 5, {}, "user", world.south1.id), "id": str(ids[0])},
    ]
    await put_settings(qc, world.admin, world, rules=rules)
    names = {(await create(qc, world.north1))["assignment_rule"] for _ in range(4)}
    assert names == {"First"}  # ties go to the lower rule id, every time


async def test_rule_targets_team_group_and_user(qc, world):
    gid = uuid.uuid4()
    groups = [
        {
            "id": str(gid),
            "name": "Mixed",
            "team_id": None,
            "member_ids": [str(world.north1.id), str(world.south1.id)],
        }
    ]
    rules = [
        rule("To group", 1, {"lead_source": "Event"}, "group", gid),
        rule("To south", 2, {"lead_source": "LinkedIn"}, "team", world.south_team),
    ]
    await put_settings(qc, world.admin, world, rules=rules, groups=groups)
    owners = [
        (await create(qc, world.north2, lead_source="Event"))["owner"]["name"] for _ in range(4)
    ]
    assert Counter(owners) == {"Priya": 2, "Sneha": 2}
    li = [
        (await create(qc, world.north2, lead_source="LinkedIn"))["owner"]["name"] for _ in range(2)
    ]
    assert set(li) == {"Sneha", "Vikram"}


async def test_rule_on_an_unmatched_key_never_matches(qc, world):
    """`category` and `lead_type` are accepted by the settings but a manual lead carries neither."""
    rules = [rule("By category", 1, {"category": "Enterprise"}, "user", world.south1.id)]
    await put_settings(qc, world.admin, world, rules=rules)
    d = await create(qc, world.north1)
    assert d["assignment_rule"] is None and d["team_name"] == "Team North"


async def test_rule_pointing_at_an_empty_or_capped_target_waits_instead_of_falling_through(
    qc, world
):
    empty_group = uuid.uuid4()
    groups = [{"id": str(empty_group), "name": "Nobody", "team_id": None, "member_ids": []}]
    rules = [
        rule("Empty", 1, {"lead_source": "Event"}, "group", empty_group),
        rule("Capped", 2, {"lead_source": "LinkedIn"}, "user", world.south1.id),
    ]
    await put_settings(
        qc, world.admin, world, rules=rules, groups=groups, caps={str(world.south1.id): 0}
    )
    a = await create(qc, world.north1, lead_source="Event")
    assert a["status"] == "pooled" and a["waiting_reason"] == "no_active_reps"
    b = await create(qc, world.north1, lead_source="LinkedIn")
    assert (
        b["status"] == "pooled"
        and b["waiting_reason"] == "all_reps_at_cap"
        and b["team_id"] == str(world.south_team)
    )
    c = await create(qc, world.north1)  # the default team still works
    assert c["status"] == "open"


async def test_no_default_team_waits_with_no_rule_match_and_admins_are_told_once(qc, world):
    d = await create(qc, world.north1)
    assert (
        d["status"] == "pooled" and d["waiting_reason"] == "no_rule_match" and d["team_id"] is None
    )
    await dispatch_all()
    await dispatch_all()
    notes = [n for n in await notifications_for(world.admin.id) if n.type == "lead.waiting"]
    assert len(notes) == 1
    assert [n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"] == []


# ------------------------------------------------------------------------------ eligibility
async def test_team_leaders_admins_and_inactive_reps_never_get_round_robin(qc, world):
    await configure(qc, world)
    await patch_user(qc, world, world.north2.id, is_active=False)
    owners = {(await create(qc, world.south1))["owner"]["name"] for _ in range(6)}
    assert owners == {"Priya"}
    await patch_user(qc, world, world.north1.id, is_active=False)
    d = await create(qc, world.south1)
    assert (
        d["status"] == "pooled" and d["waiting_reason"] == "no_active_reps"
    )  # the TL is not a fallback


async def test_a_single_rep_rule_cannot_name_a_team_leader_and_groups_hold_only_reps(qc, world):
    for target in (world.tl_north.id, world.admin.id):
        r = await qc.put(
            "/settings/assignment",
            json={
                "default_team_id": str(world.north_team),
                "default_cap": 5,
                "rules": [rule("TL", 1, {}, "user", target)],
                "reason": "x",
            },
            headers=headers(world.admin),
        )
        assert r.status_code == 409, r.text
    groups = [{"name": "G", "member_ids": [str(world.tl_north.id)]}]
    r = await qc.put(
        "/settings/assignment",
        json={
            "default_team_id": str(world.north_team),
            "default_cap": 5,
            "groups": groups,
            "reason": "x",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 409


async def test_a_rep_in_two_groups_has_one_cap_across_both(qc, world):
    g1, g2 = uuid.uuid4(), uuid.uuid4()
    groups = [
        {"id": str(g1), "name": "G1", "member_ids": [str(world.north1.id), str(world.north2.id)]},
        {"id": str(g2), "name": "G2", "member_ids": [str(world.north2.id), str(world.south1.id)]},
    ]
    rules = [
        rule("r1", 1, {"lead_source": "Event"}, "group", g1),
        rule("r2", 2, {"lead_source": "LinkedIn"}, "group", g2),
    ]
    caps = {str(world.north1.id): 0, str(world.south1.id): 0, str(world.north2.id): 3}
    await put_settings(qc, world.admin, world, rules=rules, groups=groups, caps=caps)
    results = [
        await create(qc, world.north1, lead_source=s)
        for s in ("Event", "LinkedIn", "Event", "LinkedIn", "Event")
    ]
    assert [r["status"] for r in results] == ["open", "open", "open", "pooled", "pooled"]
    assert {r["owner"]["name"] for r in results if r["owner"]} == {"Rahul"}


# ------------------------------------------------------------------------------ caps
async def test_cap_zero_one_and_n(qc, world):
    await configure(qc, world, cap=0)
    d = await create(qc, world.north1)
    assert d["status"] == "pooled" and d["waiting_reason"] == "all_reps_at_cap"
    await configure(qc, world, cap=1)
    await dispatch_all()
    assert (await deal(qc, world, d["id"]))["status"] == "open"
    more = [await create(qc, world.north1) for _ in range(3)]
    assert [m["status"] for m in more] == [
        "open",
        "pooled",
        "pooled",
    ]  # 2 reps x cap 1, one used by d
    await configure(qc, world, cap=5)
    await dispatch_all()
    counts = await owner_counts(qc, world)
    assert sum(counts.values()) == 4 and max(counts.values()) - min(counts.values()) <= 1


async def test_per_rep_cap_overrides_the_default_and_unlisted_reps_fall_back(qc, world):
    await put_settings(
        qc,
        world.admin,
        world,
        default_cap=2,
        caps={str(world.north1.id): 5, str(world.north2.id): 0},
    )
    owners = [(await create(qc, world.south1))["owner"]["name"] for _ in range(5)]
    assert set(owners) == {"Priya"}
    settings = (await qc.get("/settings/assignment", headers=headers(world.admin))).json()
    rows = {c["name"]: c for c in settings["caps"]}
    assert rows["Priya"]["cap"] == 5 and rows["Priya"]["is_default"] is False
    assert (
        rows["Rahul"]["cap"] == 0
        and rows["Sneha"]["cap"] == 2
        and rows["Sneha"]["is_default"] is True
    )
    assert rows["Priya"]["active_leads"] == 5
    # a full replace: dropping the override sends Priya back to the default
    await put_settings(qc, world.admin, world, default_cap=2, caps={})
    settings = (await qc.get("/settings/assignment", headers=headers(world.admin))).json()
    assert {c["name"]: c["cap"] for c in settings["caps"]}["Priya"] == 2


async def test_lowering_a_cap_never_takes_deals_away_but_stops_new_ones(qc, world):
    await configure(qc, world, cap=10)
    first = [await create(qc, world.north1) for _ in range(4)]
    await configure(qc, world, cap=1)
    assert [(await deal(qc, world, d["id"]))["status"] for d in first] == ["open"] * 4
    d = await create(qc, world.north1)
    assert d["status"] == "pooled"


async def test_closing_or_deleting_a_deal_frees_the_cap_and_the_oldest_waiting_lead_goes_first(
    qc, world
):
    await put_settings(qc, world.admin, world, default_cap=1, caps={str(world.north2.id): 0})
    a = await create(qc, world.south1, company="Occupies the slot")
    waiting = [
        await create(qc, world.south1, company=n)
        for n in ("Quartz Mining", "Harbor Freight", "Zinc Alloys")
    ]
    assert a["status"] == "open" and all(w["status"] == "pooled" for w in waiting)
    await dispatch_all()
    # lose the deal: the slot frees, only the OLDEST waiting lead takes it
    owner = a["owner"]["id"]
    assert owner == str(world.north1.id)
    r = await qc.post(
        f"/deals/{a['id']}/lose",
        json={"lost_reason": "Price", "notes": "x"},
        headers=headers(world.north1),
    )
    assert r.status_code == 200, r.text
    await dispatch_all()
    states = [(await deal(qc, world, w["id"]))["status"] for w in waiting]
    assert states == ["open", "pooled", "pooled"]
    # delete the deal that now holds the slot (Admin, with a reason)
    r = await qc.post(
        f"/deals/{waiting[0]['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
    )
    assert r.status_code == 204
    await dispatch_all()
    states = [(await deal(qc, world, w["id"]))["status"] for w in waiting[1:]]
    assert states == ["open", "pooled"]


async def test_won_deals_do_not_count_against_the_cap(qc, admin_engine, world):
    await put_settings(qc, world.admin, world, default_cap=1, caps={str(world.north2.id): 0})
    a = await create(qc, world.south1)
    b = await create(qc, world.south1)
    assert b["status"] == "pooled"
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE deals SET status='won' WHERE id=:i"), {"i": a["id"]})
    from app.modules.leads.assignment.service import retry_waiting

    assert await retry_waiting(world.org_id, get_sessionmaker()) == 1
    assert (await deal(qc, world, b["id"]))["status"] == "open"


async def test_cap_boundary_under_concurrency_is_exact(qc, admin_engine, world):
    await configure(qc, world, cap=3)  # North has two reps: exactly six slots
    rs = await asyncio.gather(
        *(qc.post("/leads", json=lead(), headers=headers(world.tl_north)) for _ in range(30))
    )
    assert sorted({r.status_code for r in rs}) == [201]
    deals = [r.json() for r in rs]
    assert Counter(d["status"] for d in deals) == {"open": 6, "pooled": 24}
    counts = Counter(d["owner"]["name"] for d in deals if d["owner"])
    assert counts == {"Priya": 3, "Rahul": 3}
    await dispatch_all()
    assert not await invariant_violations(admin_engine, world.org_id)
    # the TL hears once per waiting lead, not once per retry
    waiting_notes = [
        n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"
    ]
    assert len(waiting_notes) == 24


# ------------------------------------------------------------------------------ fairness
async def _four_rep_group(qc, world, extra_rules=None):
    gid = uuid.uuid4()
    members = [world.north1, world.north2, world.south1, world.south2]
    groups = [{"id": str(gid), "name": "All reps", "member_ids": [str(m.id) for m in members]}]
    rules = [rule("All", 1, {}, "group", gid)] + (extra_rules or [])
    await put_settings(qc, world.admin, world, rules=rules, groups=groups, default_cap=500)
    return gid, members


async def test_two_hundred_leads_across_four_reps_differ_by_at_most_one(qc, admin_engine, world):
    await _four_rep_group(qc, world)
    owners = []
    for _ in range(200):
        owners.append((await create(qc, world.north1))["owner"]["name"])
    c = Counter(owners)
    assert set(c) == {"Priya", "Rahul", "Sneha", "Vikram"}
    assert max(c.values()) - min(c.values()) <= 1 and sum(c.values()) == 200
    # strict rotation: no rep gets a second lead before every other rep got one
    for i in range(0, 200, 4):
        assert len(set(owners[i : i + 4])) == 4, owners[i : i + 4]
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_rotation_survives_a_rep_being_removed_and_added_midway(qc, admin_engine, world):
    gid, members = await _four_rep_group(qc, world)
    seq = [(await create(qc, world.north1))["owner"]["name"] for _ in range(8)]
    assert len(set(seq[:4])) == 4 and len(set(seq[4:])) == 4
    await patch_user(qc, world, world.south2.id, is_active=False)
    after = [(await create(qc, world.north1))["owner"]["name"] for _ in range(9)]
    assert "Vikram" not in after
    assert all(len(set(after[i : i + 3])) == 3 for i in range(0, 9, 3)), after


async def test_removing_the_last_served_rep_does_not_skip_the_next_in_line(qc, admin_engine, world):
    gid, members = await _four_rep_group(qc, world)
    ordered = [
        r[0]
        for r in await q(
            admin_engine,
            "SELECT id FROM users WHERE id = ANY(:ids) ORDER BY id",
            ids=[m.id for m in members],
        )
    ]
    by_id = {m.id: m for m in members}
    first, second, third = (by_id[ordered[0]], by_id[ordered[1]], by_id[ordered[2]])
    # The acting creator must not be a rep that gets deactivated: members are ordered by random
    # UUID, so north1 could be `second` and would then get 401 "account is deactivated".
    actor = world.tl_north
    got = [(await create(qc, actor))["owner"]["id"] for _ in range(2)]
    assert got == [str(first.id), str(second.id)]
    await patch_user(qc, world, second.id, is_active=False)
    nxt = (await create(qc, actor))["owner"]["id"]
    assert nxt == str(third.id), "the rep after the one who was just served should be next"


async def test_the_pointer_is_per_target(qc, world):
    g = uuid.uuid4()
    groups = [
        {"id": str(g), "name": "N1+S1", "member_ids": [str(world.north1.id), str(world.south1.id)]}
    ]
    rules = [rule("Group", 1, {"lead_source": "Event"}, "group", g)]
    await put_settings(qc, world.admin, world, rules=rules, groups=groups)
    ev = [(await create(qc, world.north2, lead_source="Event"))["owner"]["name"] for _ in range(4)]
    assert Counter(ev) == {"Priya": 2, "Sneha": 2}
    team = [
        (await create(qc, world.north2, lead_source="Website"))["owner"]["name"] for _ in range(4)
    ]
    assert Counter(team) == {"Priya": 2, "Rahul": 2}  # the team pointer was untouched by the group


async def test_a_new_rep_joins_the_rotation_and_triggers_a_retry_for_waiting_leads(qc, world):
    await put_settings(qc, world.admin, world, default_cap=1)
    for _ in range(2):
        await create(qc, world.north1)
    waiting = await create(qc, world.north1)
    assert waiting["status"] == "pooled"
    await dispatch_all()
    rep = await add_rep(qc, world, "Newbie")
    await dispatch_all()
    got = await deal(qc, world, waiting["id"])
    assert got["status"] == "open" and got["owner"]["id"] == rep["id"]


# ------------------------------------------------------------------------------ concurrency
@pytest.mark.parametrize("round_", [1, 2, 3])
async def test_hundred_concurrent_leads_are_each_assigned_exactly_once(
    qc, admin_engine, world, round_
):
    gid, members = await _four_rep_group(qc, world)
    await put_settings(
        qc,
        world.admin,
        world,
        rules=[rule("All", 1, {}, "group", gid)],
        groups=[{"id": str(gid), "name": "All reps", "member_ids": [str(m.id) for m in members]}],
        default_cap=20,
    )
    rs = await asyncio.gather(
        *(qc.post("/leads", json=lead(), headers=headers(world.tl_north)) for _ in range(100))
    )
    assert {r.status_code for r in rs} == {201}, [r.text for r in rs if r.status_code != 201][:2]
    await dispatch_all()
    counts = await q(
        admin_engine,
        "SELECT status, count(*) FROM deals WHERE org_id=:o GROUP BY 1",
        o=world.org_id,
    )
    assert dict(counts) == {"open": 80, "pooled": 20}
    per_owner = await q(
        admin_engine,
        "SELECT owner_id, count(*) FROM deals WHERE org_id=:o AND status='open' GROUP BY 1",
        o=world.org_id,
    )
    assert sorted(n for _, n in per_owner) == [20, 20, 20, 20]
    assigned_rows = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='lead.assigned' AND outcome='ok'",
        o=world.org_id,
    )
    assert assigned_rows == 80
    assert not await invariant_violations(admin_engine, world.org_id)
    # raise the caps: every waiting lead is picked up, nothing stays stuck
    await put_settings(
        qc,
        world.admin,
        world,
        rules=[rule("All", 1, {}, "group", gid)],
        groups=[{"id": str(gid), "name": "All reps", "member_ids": [str(m.id) for m in members]}],
        default_cap=40,
    )
    await dispatch_all()
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM deals WHERE org_id=:o AND status='pooled'",
            o=world.org_id,
        )
        == 0
    )
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_concurrent_creates_and_cap_changes_never_dead_lock(qc, admin_engine, world):
    await configure(qc, world, cap=5)
    tasks = [qc.post("/leads", json=lead(), headers=headers(world.tl_north)) for _ in range(40)]
    tasks += [
        qc.put(
            "/settings/assignment",
            json={
                "default_team_id": str(world.north_team),
                "default_cap": 3 + i,
                "reason": f"change {i}",
            },
            headers=headers(world.admin),
        )
        for i in range(8)
    ]
    rs = await asyncio.gather(*tasks)
    assert {r.status_code for r in rs} <= {200, 201}, sorted(set(r.status_code for r in rs))
    await dispatch_all()
    assert not await invariant_violations(admin_engine, world.org_id)


# ------------------------------------------------------------------------------ idempotency
async def test_running_the_engine_and_the_handler_twice_changes_nothing(qc, admin_engine, world):
    from app.modules.leads.assignment.service import assign_pooled, retry_waiting

    await configure(qc, world, cap=1)
    deals = [await create(qc, world.north1) for _ in range(3)]
    placed = [d for d in deals if d["status"] == "open"]
    waiting = next(d for d in deals if d["status"] == "pooled")
    before = await q(
        admin_engine,
        "SELECT id, owner_id, version, assigned_at FROM deals WHERE org_id=:o ORDER BY id",
        o=world.org_id,
    )
    for d in placed:
        out = await assign_pooled(world.org_id, uuid.UUID(d["id"]))
        assert out.status == "skipped"
    for _ in range(3):
        assert await retry_waiting(world.org_id, get_sessionmaker()) == 0
    out = await assign_pooled(world.org_id, uuid.UUID(waiting["id"]))
    assert out.status == "waiting"
    assert before == await q(
        admin_engine,
        "SELECT id, owner_id, version, assigned_at FROM deals WHERE org_id=:o ORDER BY id",
        o=world.org_id,
    )
    rows = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='lead.waiting'",
        o=world.org_id,
    )
    assert rows == 1  # a retry that changes nothing writes nothing


async def test_replaying_every_outbox_event_never_double_notifies_or_double_assigns(
    qc, admin_engine, world
):
    await configure(qc, world, cap=1)
    for _ in range(3):
        await create(qc, world.north1)
    await dispatch_all()
    before_n = await q1(
        admin_engine, "SELECT count(*) FROM notifications WHERE org_id=:o", o=world.org_id
    )
    before_d = await q(
        admin_engine,
        "SELECT id, owner_id, version FROM deals WHERE org_id=:o ORDER BY id",
        o=world.org_id,
    )
    for _ in range(2):  # redelivery: the same events, the same ids
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE outbox_events SET status='pending', available_at=now() WHERE org_id=:o"
                ),
                {"o": world.org_id},
            )
        await dispatch_all()
    assert before_n == await q1(
        admin_engine, "SELECT count(*) FROM notifications WHERE org_id=:o", o=world.org_id
    )
    assert before_d == await q(
        admin_engine,
        "SELECT id, owner_id, version FROM deals WHERE org_id=:o ORDER BY id",
        o=world.org_id,
    )
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_the_fifteen_minute_job_assigns_a_waiting_lead_once_capacity_exists(
    qc, admin_engine, world
):
    from app.platform.jobs.app import pool_retry

    await configure(qc, world, cap=0)
    d = await create(qc, world.north1)
    assert d["status"] == "pooled"
    async with admin_engine.begin() as c:  # capacity appears without any event (a direct DB change)
        await c.execute(
            text("UPDATE assignment_settings SET default_cap=5 WHERE org_id=:o"),
            {"o": world.org_id},
        )
    assigned = await pool_retry.__wrapped__(0) if hasattr(pool_retry, "__wrapped__") else None
    if assigned is None:  # procrastinate wraps the function: call the engine function it calls
        from app.modules.leads.assignment.service import retry_waiting

        assigned = await retry_waiting(world.org_id, get_sessionmaker())
    assert assigned >= 1
    assert (await deal(qc, world, d["id"]))["status"] == "open"
    assert Counter(
        [
            x[0]
            for x in await q(
                admin_engine, "SELECT status FROM deals WHERE org_id=:o", o=world.org_id
            )
        ]
    ) == {"open": 1}


async def test_retry_runs_across_every_organisation_without_mixing_them(qc, admin_engine, world):
    from app.modules.leads.assignment.service import retry_waiting
    from tests.deep.phase2.support import make_world

    other = await make_world(admin_engine)
    await configure(qc, world, cap=0)
    await configure(qc, other, cap=0)
    mine = await create(qc, world.north1)
    theirs = await create(qc, other.north1)
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE assignment_settings SET default_cap=5 WHERE org_id=:o"),
            {"o": other.org_id},
        )
    assert await retry_waiting(world.org_id, get_sessionmaker()) == 0
    assert (await deal(qc, world, mine["id"]))["status"] == "pooled"
    assert await retry_waiting(other.org_id, get_sessionmaker()) == 1
    got = (await qc.get(f"/deals/{theirs['id']}", headers=headers(other.admin))).json()
    assert got["status"] == "open"


# ------------------------------------------------------------------------------ waiting leads
async def test_waiting_reasons_and_the_leader_is_told_once_across_every_retry_trigger(
    qc, admin_engine, world
):
    await put_settings(qc, world.admin, world, default_cap=0)
    d = await create(qc, world.south1, company="Waiting For Capacity")
    assert d["waiting_reason"] == "all_reps_at_cap"
    triggers = []
    for i in range(3):
        await put_settings(
            qc, world.admin, world, default_cap=0, reason=f"save {i}"
        )  # settings saved
        triggers.append("settings")
    await patch_user(qc, world, world.north1.id, is_active=False)
    await patch_user(qc, world, world.north1.id, is_active=True)  # reactivated
    await patch_user(qc, world, world.north2.id, team_id=str(world.south_team))  # team change
    await add_rep(qc, world, "Zed")  # new user (also cap 0)
    for _ in range(3):
        await dispatch_all()
    notes = [n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"]
    assert (
        len(notes) == 1
        and "Waiting For Capacity" in notes[0].body
        and "all_reps_at_cap" in notes[0].body
    )
    assert not [n for n in await notifications_for(world.tl_south.id) if n.type == "lead.waiting"]
    st = await q(
        admin_engine,
        "SELECT waiting_reason, tl_notified FROM lead_pool_entries WHERE deal_id=:d",
        d=uuid.UUID(d["id"]),
    )
    assert [tuple(x) for x in st] == [("all_reps_at_cap", True)]
    waiting_audit = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE entity_id=:e AND action_key='lead.waiting'",
        e=d["id"],
    )
    assert waiting_audit == 1


async def test_a_reason_change_while_waiting_is_recorded_but_does_not_renotify(
    qc, admin_engine, world
):
    await put_settings(qc, world.admin, world, default_cap=0)
    d = await create(qc, world.south1)
    assert d["waiting_reason"] == "all_reps_at_cap"
    await patch_user(qc, world, world.north1.id, is_active=False)
    await patch_user(qc, world, world.north2.id, is_active=False)
    await dispatch_all()
    got = await deal(qc, world, d["id"])
    assert got["waiting_reason"] == "no_active_reps"
    notes = [n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"]
    assert len(notes) == 1
    kinds = [
        tuple(x)
        for x in await q(
            admin_engine,
            "SELECT new_value->>'waiting_reason' FROM audit_log WHERE entity_id=:e AND action_key='lead.waiting' ORDER BY seq",
            e=d["id"],
        )
    ]
    assert kinds == [("all_reps_at_cap",), ("no_active_reps",)]


async def test_moving_a_waiting_leads_target_team_tells_that_teams_leader(qc, world):
    await put_settings(qc, world.admin, world, default_cap=0)
    d = await create(qc, world.south1)
    await put_settings(
        qc,
        world.admin,
        world,
        default_cap=0,
        default_team_id=str(world.south_team),
        reason="route south",
    )
    await dispatch_all()
    got = await deal(qc, world, d["id"])
    assert got["team_id"] == str(world.south_team)  # the entry follows the new routing
    assert (
        len([n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"])
        == 1
    )


async def test_lead_assigned_notifies_the_new_owner_exactly_once(qc, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    for _ in range(3):
        await dispatch_all()
    owner_id = d["owner"]["id"]
    got = [n for n in await notifications_for(uuid.UUID(owner_id)) if n.type == "lead.assigned"]
    assert len(got) == 1 and f"/deals/{d['id']}" == got[0].link
    other = world.north2.id if owner_id == str(world.north1.id) else world.north1.id
    assert not [n for n in await notifications_for(other) if n.type == "lead.assigned"]


async def test_manual_hold_leads_are_ignored_by_the_engine_until_assigned(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    r = await qc.post(
        f"/deals/{d['id']}/move-team",
        json={"to_team_id": str(world.south_team), "reason": "territory"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200 and r.json()["status"] == "pooled"
    for _ in range(2):
        await put_settings(qc, world.admin, world, reason="poke")
        await dispatch_all()
    assert (await deal(qc, world, d["id"]))["status"] == "pooled"
    entry = await q(
        admin_engine,
        "SELECT manual_hold, routed_team_id FROM lead_pool_entries WHERE deal_id=:d AND left_at IS NULL",
        d=uuid.UUID(d["id"]),
    )
    assert entry[0][0] is True and entry[0][1] == world.south_team
    r = await qc.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(world.south1.id), "reason": "mine"},
        headers=headers(world.tl_south),
    )
    assert r.status_code == 200


# ------------------------------------------------------------------------------ settings API
def body(world, **kw):
    return {"default_team_id": str(world.north_team), "default_cap": 5, "reason": "x", **kw}


async def test_settings_default_team_is_required_and_must_be_one_of_this_orgs_live_teams(
    qc, admin_engine, world
):
    from tests.deep.phase2.support import make_world

    h = headers(world.admin)
    b = body(world)
    del b["default_team_id"]
    assert (await qc.put("/settings/assignment", json=b, headers=h)).status_code == 422
    assert (
        await qc.put("/settings/assignment", json=body(world, default_team_id=None), headers=h)
    ).status_code == 422
    assert (
        await qc.put(
            "/settings/assignment", json=body(world, default_team_id=str(uuid.uuid4())), headers=h
        )
    ).status_code == 409
    other = await make_world(admin_engine)
    assert (
        await qc.put(
            "/settings/assignment",
            json=body(world, default_team_id=str(other.north_team)),
            headers=h,
        )
    ).status_code == 409
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE teams SET deleted_at=now() WHERE id=:t"), {"t": world.south_team}
        )
    assert (
        await qc.put(
            "/settings/assignment",
            json=body(world, default_team_id=str(world.south_team)),
            headers=h,
        )
    ).status_code == 409
    assert (await qc.put("/settings/assignment", json=body(world), headers=h)).status_code == 200


async def test_settings_reference_checks(qc, world):
    h = headers(world.admin)
    missing = str(uuid.uuid4())
    for r_ in (
        rule("x", 1, {}, "team", missing),
        rule("x", 1, {}, "group", missing),
        rule("x", 1, {}, "user", missing),
    ):
        r = await qc.put("/settings/assignment", json=body(world, rules=[r_]), headers=h)
        assert r.status_code in (404, 409), (r_["target_type"], r.text)
    r = await qc.put("/settings/assignment", json=body(world, caps={missing: 3}), headers=h)
    assert r.status_code == 404
    r = await qc.put(
        "/settings/assignment",
        json=body(world, groups=[{"name": "g", "member_ids": [missing]}]),
        headers=h,
    )
    assert r.status_code == 409
    r = await qc.put(
        "/settings/assignment",
        json=body(world, rules=[rule("x", 1, {"colour": "red"}, "team", world.north_team)]),
        headers=h,
    )
    assert r.status_code == 409 and "Unknown rule condition" in r.json()["message"]
    r = await qc.put(
        "/settings/assignment",
        json=body(world, rules=[{**rule("x", 1, {}, "team", world.north_team), "extra": 1}]),
        headers=h,
    )
    assert r.status_code == 422
    r = await qc.put(
        "/settings/assignment",
        json=body(world, rules=[rule("", 1, {}, "team", world.north_team)]),
        headers=h,
    )
    assert r.status_code == 422
    r = await qc.put(
        "/settings/assignment",
        json=body(world, rules=[rule("n" * 201, 1, {}, "team", world.north_team)]),
        headers=h,
    )
    assert r.status_code == 422
    r = await qc.put(
        "/settings/assignment",
        json=body(world, rules=[rule("x", 1, {"city": 5}, "team", world.north_team)]),
        headers=h,
    )
    assert r.status_code == 422
    assert (
        await qc.put("/settings/assignment", json=body(world, default_cap=-1), headers=h)
    ).status_code == 422
    assert (
        await qc.put("/settings/assignment", json=body(world, extra=1), headers=h)
    ).status_code == 422


@pytest.mark.parametrize(
    "bad", [{"caps": "neg"}, {"caps": "huge"}, {"cap": 2**40}, {"priority": 2**40}]
)
async def test_out_of_range_numbers_are_a_clean_422(qc, world, bad):
    h = headers(world.admin)
    if "caps" in bad:
        v = -5 if bad["caps"] == "neg" else 2**40
        r = await qc.put(
            "/settings/assignment", json=body(world, caps={str(world.north1.id): v}), headers=h
        )
    elif "cap" in bad:
        r = await qc.put(
            "/settings/assignment", json=body(world, default_cap=bad["cap"]), headers=h
        )
    else:
        r = await qc.put(
            "/settings/assignment",
            json=body(world, rules=[rule("p", bad["priority"], {}, "team", world.north_team)]),
            headers=h,
        )
    assert r.status_code == 422, r.status_code


async def test_negative_and_huge_priorities_within_the_column_are_sorted_correctly(qc, world):
    rules = [
        rule("neg", -5, {}, "user", world.south1.id),
        rule("pos", 2_000_000_000, {}, "user", world.south2.id),
    ]
    await put_settings(qc, world.admin, world, rules=rules)
    assert (await create(qc, world.north1))["assignment_rule"] == "neg"
    got = (await qc.get("/settings/assignment", headers=headers(world.admin))).json()
    assert [r["name"] for r in got["rules"]] == ["neg", "pos"]


async def test_rules_and_groups_round_trip_and_removed_ones_are_soft_deleted(
    qc, admin_engine, world
):
    gid = uuid.uuid4()
    groups = [
        {
            "id": str(gid),
            "name": "  Keep  ",
            "team_id": str(world.north_team),
            "member_ids": [str(world.north1.id), str(world.north1.id)],
        }
    ]
    rules = [
        rule("A", 3, {"city": "Pune"}, "group", gid),
        rule("B", 1, {}, "team", world.south_team),
    ]
    out = await put_settings(
        qc, world.admin, world, rules=rules, groups=groups, caps={str(world.north1.id): 7}
    )
    assert [r["name"] for r in out["rules"]] == ["B", "A"]
    assert out["groups"][0]["name"] == "Keep" and out["groups"][0]["member_ids"] == [
        str(world.north1.id)
    ]
    assert {c["name"]: c["cap"] for c in out["caps"]}["Priya"] == 7
    again = await put_settings(qc, world.admin, world)  # remove everything
    assert again["rules"] == [] and again["groups"] == []
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM assignment_rules WHERE org_id=:o AND deleted_at IS NOT NULL",
            o=world.org_id,
        )
        == 2
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM rep_groups WHERE org_id=:o AND deleted_at IS NOT NULL",
            o=world.org_id,
        )
        == 1
    )


async def test_settings_roles_reasons_and_the_audit_row(qc, admin_engine, world):
    for actor in (world.north1, world.south1, world.tl_north, world.tl_south):
        assert (await qc.get("/settings/assignment", headers=headers(actor))).status_code in (
            403,
            404,
        )
        assert (
            await qc.put("/settings/assignment", json=body(world), headers=headers(actor))
        ).status_code == 403
        assert (
            await qc.post("/settings/assignment/test", json={}, headers=headers(actor))
        ).status_code in (403, 404)
    assert (await qc.get("/settings/assignment")).status_code == 401
    assert (
        await qc.put(
            "/settings/assignment", json=body(world, reason=None), headers=headers(world.admin)
        )
    ).status_code == 422
    assert (
        await qc.put(
            "/settings/assignment", json=body(world, reason="   "), headers=headers(world.admin)
        )
    ).status_code == 422
    ok = await qc.put(
        "/settings/assignment", json=body(world, reason=None), headers=headers(world.sa)
    )
    assert ok.status_code == 200
    ok = await qc.put(
        "/settings/assignment",
        json=body(world, default_cap=9, reason="raise"),
        headers=headers(world.admin),
    )
    assert ok.status_code == 200
    rows = await q(
        admin_engine,
        "SELECT reason, old_value->>'default_cap', new_value->>'default_cap', actor_role FROM audit_log WHERE org_id=:o AND action_key='assignment.config' AND outcome='ok' ORDER BY seq",
        o=world.org_id,
    )
    assert [tuple(r) for r in rows] == [
        (None, "25", "5", "super_admin"),
        ("raise", "5", "9", "admin"),
    ]
    assert (await qc.get("/settings/assignment", headers=headers(world.sa))).status_code == 200


async def test_the_rule_test_tool_is_read_only(qc, admin_engine, world):
    await put_settings(
        qc,
        world.admin,
        world,
        rules=[rule("Events", 1, {"lead_source": "Event"}, "team", world.south_team)],
        caps={str(world.south1.id): 0},
    )

    async def snap():
        return await q(
            admin_engine,
            "SELECT (SELECT count(*) FROM audit_log WHERE org_id=:o), (SELECT count(*) FROM outbox_events WHERE org_id=:o), "
            "(SELECT count(*) FROM deals WHERE org_id=:o), (SELECT count(*) FROM rr_pointers WHERE org_id=:o), "
            "(SELECT count(*) FROM notifications WHERE org_id=:o)",
            o=world.org_id,
        )

    before = await snap()
    r = await qc.post(
        "/settings/assignment/test", json={"lead_source": "Event"}, headers=headers(world.admin)
    )
    assert r.status_code == 200
    out = r.json()
    assert out["matched_rule"] == "Events" and out["target_name"] == "Team South"
    assert out["at_cap"] == ["Sneha (0/0)"] and out["eligible_reps"] == ["Vikram (0/25)"]
    r = await qc.post("/settings/assignment/test", json={}, headers=headers(world.sa))
    assert r.json()["matched_rule"] == "(default team)"
    r = await qc.post(
        "/settings/assignment/test",
        json={"unknown": 1, "industry": "x" * 5000},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    assert before == await snap()


async def test_first_ever_settings_saves_at_the_same_time_all_succeed(qc, world):
    rs = await asyncio.gather(
        *(
            qc.put(
                "/settings/assignment",
                json=body(world, default_cap=i, reason=f"s{i}"),
                headers=headers(world.admin),
            )
            for i in range(8)
        )
    )
    assert {r.status_code for r in rs} == {200}, sorted(r.status_code for r in rs)


async def test_concurrent_saves_leave_one_consistent_state(qc, admin_engine, world):
    await configure(qc, world)  # the settings row exists (see BUG-P2-19 for the first-ever request)
    gid = uuid.uuid4()
    groups = [{"id": str(gid), "name": "Shared", "member_ids": [str(world.north1.id)]}]
    tasks = [
        qc.put(
            "/settings/assignment",
            json=body(
                world,
                default_cap=i,
                rules=[rule(f"r{i}", i, {}, "group", gid)],
                groups=groups,
                caps={str(world.north1.id): i},
                reason=f"save {i}",
            ),
            headers=headers(world.admin),
        )
        for i in range(10)
    ]
    rs = await asyncio.gather(*tasks)
    codes = sorted(r.status_code for r in rs)
    assert 500 not in codes, codes
    live = await q(
        admin_engine,
        "SELECT count(*) FROM assignment_rules WHERE org_id=:o AND deleted_at IS NULL",
        o=world.org_id,
    )
    assert live[0][0] == 1
    final = (await qc.get("/settings/assignment", headers=headers(world.admin))).json()
    i = int(final["rules"][0]["name"][1:])
    assert final["default_cap"] == i and {c["name"]: c["cap"] for c in final["caps"]}["Priya"] == i
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_settings_cannot_point_at_another_organisations_team(qc, admin_engine, world):
    from tests.deep.phase2.support import make_world

    other = await make_world(admin_engine)
    r = await qc.put(
        "/settings/assignment",
        json=body(world, rules=[rule("x", 1, {}, "team", other.south_team)]),
        headers=headers(world.admin),
    )
    assert r.status_code in (404, 409), r.text
    g = await qc.put(
        "/settings/assignment",
        json=body(
            world, groups=[{"name": "g", "team_id": str(other.south_team), "member_ids": []}]
        ),
        headers=headers(world.admin),
    )
    assert g.status_code in (404, 409), g.text


async def test_a_rule_pointing_at_another_organisations_team_routes_the_lead_into_that_team(
    qc, admin_engine, world
):
    """Consequence of BUG-P2-18, asserted as it stands today: the pooled lead carries the other
    organisation's team id and shows its team name."""
    from tests.deep.phase2.support import make_world

    other = await make_world(admin_engine)
    r = await qc.put(
        "/settings/assignment",
        json=body(world, rules=[rule("x", 1, {}, "team", other.south_team)]),
        headers=headers(world.admin),
    )
    if r.status_code != 200:
        pytest.skip("fixed: the rule is refused")
    d = await create(qc, world.north1)
    assert (
        d["status"] == "pooled"
        and d["team_id"] == str(other.south_team)
        and d["team_name"] == "Team South"
    )
    assert await invariant_violations(admin_engine, world.org_id)  # the invariant check flags it


@pytest.mark.parametrize("kind", ["rule", "group"])
async def test_resaving_a_removed_rule_or_group_id_is_not_a_500(qc, world, kind):
    rid = uuid.uuid4()
    first = (
        {"rules": [{**rule("R", 1, {}, "team", world.north_team), "id": str(rid)}]}
        if kind == "rule"
        else {"groups": [{"id": str(rid), "name": "G", "member_ids": []}]}
    )
    await put_settings(qc, world.admin, world, **first)
    await put_settings(qc, world.admin, world)  # removed (soft-deleted)
    r = await qc.put(
        "/settings/assignment",
        json={
            "default_team_id": str(world.north_team),
            "default_cap": 25,
            "reason": "stale tab",
            **first,
        },
        headers=headers(world.admin),
    )
    assert r.status_code < 500, r.status_code
