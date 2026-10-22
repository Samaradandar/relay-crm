# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Companies, contacts and merging; assignment rules, groups and caps (Phase 2 coverage)."""

from sqlalchemy import text

from tests.helpers import headers
from tests.modules.journey import detail
from tests.modules.test_leads_api import actor_by_id, configure, create


async def put_settings(client, world, expect=200, **kw):
    body = {
        "default_team_id": str(world.north_team),
        "default_cap": 25,
        "rules": [],
        "groups": [],
        "caps": {},
        "reason": "setup",
        **kw,
    }
    r = await client.put("/settings/assignment", json=body, headers=headers(world.admin))
    assert r.status_code == expect, r.text
    return r.json()


# ------------------------------------------------------------------------------ accounts
async def test_account_and_contact_crud(client, world):
    await configure(client, world)
    r = await client.post(
        "/accounts",
        json={"name": "Maple Logistics", "website": "https://maple-log.in", "city": "Pune"},
        headers=headers(world.north1),
    )
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["domain"] == "maple-log.in" and acc["city"] == "Pune"
    r = await client.patch(
        f"/accounts/{acc['id']}",
        json={"industry": "Logistics", "city": "Mumbai"},
        headers=headers(world.north1),
    )
    assert r.status_code == 200 and r.json()["industry"] == "Logistics"
    assert (
        await client.patch(
            f"/accounts/{acc['id']}", json={"name": ""}, headers=headers(world.north1)
        )
    ).status_code == 422
    r = await client.post(
        "/contacts",
        json={
            "account_id": acc["id"],
            "name": "Meera",
            "email": "meera@maple-log.in",
            "role": "Ops head",
        },
        headers=headers(world.north1),
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = await client.post(
        "/contacts", json={"account_id": acc["id"], "name": "Nobody"}, headers=headers(world.north1)
    )
    assert r.status_code == 409  # needs a valid phone or email
    r = await client.patch(
        f"/contacts/{cid}",
        json={"phone": "+91 98765 43210", "is_decision_maker": True},
        headers=headers(world.north1),
    )
    assert (
        r.status_code == 200
        and r.json()["phone"] == "+919876543210"
        and r.json()["is_decision_maker"]
    )
    assert (
        await client.patch(f"/contacts/{cid}", json={"email": "bad"}, headers=headers(world.north1))
    ).status_code in (409, 422)
    found = (
        await client.get("/accounts", params={"q": "maple"}, headers=headers(world.north1))
    ).json()
    assert [a["id"] for a in found] == [acc["id"]]
    assert (
        await client.get("/accounts", params={"q": "maple"}, headers=headers(world.tl_south))
    ).json() == []
    got = (await client.get(f"/accounts/{acc['id']}", headers=headers(world.north1))).json()
    assert got["contacts"][0]["name"] == "Meera"


async def test_merge_keeps_the_source_row_and_moves_deals_and_contacts(client, world, admin_engine):
    await configure(client, world)
    a = await create(client, world.north1, company="Orbit Foods", website="https://orbit-foods.in")
    b = await create(
        client, world.north1, company="Delta Retail", website="https://delta-retail.in"
    )
    for who in (world.north1, world.tl_north):
        r = await client.post(
            f"/accounts/{a['account_id']}/merge",
            json={"into_account_id": b["account_id"], "reason": "dup"},
            headers=headers(who),
        )
        assert r.status_code == 403
    r = await client.post(
        f"/accounts/{a['account_id']}/merge",
        json={"into_account_id": a["account_id"], "reason": "x"},
        headers=headers(world.admin),
    )
    assert r.status_code == 409
    r = await client.post(
        f"/accounts/{a['account_id']}/merge",
        json={"into_account_id": b["account_id"]},
        headers=headers(world.admin),
    )
    assert r.status_code == 422  # reason required
    r = await client.post(
        f"/accounts/{a['account_id']}/merge",
        json={"into_account_id": b["account_id"], "reason": "Same company"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200, r.text
    moved = await detail(client, world.admin, a["id"])
    assert moved["account_id"] == b["account_id"]
    async with admin_engine.begin() as c:
        row = (
            await c.execute(
                text("SELECT merged_into_id, deleted_at FROM accounts WHERE id=:i"),
                {"i": a["account_id"]},
            )
        ).one()
    assert (
        str(row.merged_into_id) == b["account_id"] and row.deleted_at is None
    )  # kept, never erased
    detail_b = (
        await client.get(f"/accounts/{b['account_id']}", headers=headers(world.admin))
    ).json()
    assert len(detail_b["deals"]) == 2 and len(detail_b["contacts"]) == 2


async def test_account_page_shows_other_teams_deals_as_locked_rows(client, world, admin_engine):
    await configure(client, world)
    d1 = await create(client, world.north1, company="Shared Co", website="https://shared-co.in")
    d2 = await create(client, world.north1, company="Other", website="https://other-co.in")
    async with admin_engine.begin() as c:  # a second deal on Shared Co belongs to South
        await c.execute(
            text("UPDATE deals SET account_id=:a, owner_id=:u, team_id=:t WHERE id=:d"),
            {"a": d1["account_id"], "u": world.south1.id, "t": world.south_team, "d": d2["id"]},
        )
    owner = actor_by_id(world, d1["owner"]["id"])
    got = (await client.get(f"/accounts/{d1['account_id']}", headers=headers(owner))).json()
    assert len(got["deals"]) == 1 and got["locked_deals"] == [
        {"label": "Deal owned by another team"}
    ]
    full = (await client.get(f"/accounts/{d1['account_id']}", headers=headers(world.admin))).json()
    assert len(full["deals"]) == 2 and full["locked_deals"] == []


# ------------------------------------------------------------------------------ assignment
async def test_rule_sends_matching_leads_to_a_team_and_names_the_rule(client, world):
    await put_settings(
        client,
        world,
        rules=[
            {
                "name": "Events go South",
                "priority": 10,
                "active": True,
                "match": {"lead_source": "Event"},
                "target_type": "team",
                "target_id": str(world.south_team),
            }
        ],
    )
    south = await create(client, world.north1, lead_source="Event")
    assert south["team_name"] == "Team South" and south["assignment_method"] == "rule"
    assert south["assignment_rule"] == "Events go South" and south["assigned_by"] == "System"
    north = await create(client, world.north1, lead_source="Website")
    assert north["team_name"] == "Team North" and north["assignment_method"] == "round_robin"
    settings = (await client.get("/settings/assignment", headers=headers(world.admin))).json()
    assert settings["rules"][0]["target_name"] == "Team South" and len(settings["caps"]) == 4


async def test_group_user_targets_caps_and_the_test_tool(client, world):
    cfg = await put_settings(client, world)
    g = {"name": "Closers", "team_id": str(world.south_team), "member_ids": [str(world.south1.id)]}
    cfg = await put_settings(client, world, groups=[g])
    gid = cfg["groups"][0]["id"]
    rules = [
        {
            "name": "Referrals to Closers",
            "priority": 5,
            "active": True,
            "match": {"lead_source": "Referral"},
            "target_type": "group",
            "target_id": gid,
        },
        {
            "name": "LinkedIn to Vikram",
            "priority": 6,
            "active": True,
            "match": {"lead_source": "LinkedIn"},
            "target_type": "user",
            "target_id": str(world.south2.id),
        },
    ]
    await put_settings(
        client, world, groups=[{**g, "id": gid}], rules=rules, caps={str(world.south1.id): 1}
    )
    one = await create(client, world.north1, lead_source="Referral")
    assert one["owner"]["id"] == str(world.south1.id)
    two = await create(client, world.north1, lead_source="Referral")  # south1's cap of 1 is full
    assert two["status"] == "pooled" and two["waiting_reason"] == "all_reps_at_cap"
    li = await create(client, world.north1, lead_source="LinkedIn")
    assert li["owner"]["id"] == str(world.south2.id)
    t = await client.post(
        "/settings/assignment/test", json={"lead_source": "Referral"}, headers=headers(world.admin)
    )
    body = t.json()
    assert body["matched_rule"] == "Referrals to Closers" and body["at_cap"] == [
        f"{world.south1.name} (1/1)"
    ]
    t = await client.post(
        "/settings/assignment/test", json={"lead_source": "Event"}, headers=headers(world.admin)
    )
    assert t.json()["matched_rule"] == "(default team)" and t.json()["target_name"] == "Team North"
    # raising the cap releases the waiting lead
    await put_settings(
        client, world, groups=[{**g, "id": gid}], rules=rules, caps={str(world.south1.id): 5}
    )
    from tests.helpers import dispatch_all

    await dispatch_all()
    assert (await detail(client, world.admin, two["id"]))["owner"]["id"] == str(world.south1.id)


async def test_settings_are_validated(client, world):
    import uuid

    await put_settings(client, world, expect=409, default_team_id=str(uuid.uuid4()))
    bad_rule = {
        "name": "Broken",
        "priority": 1,
        "active": True,
        "match": {},
        "target_type": "team",
        "target_id": str(uuid.uuid4()),
    }
    r = await client.put(
        "/settings/assignment",
        json={
            "default_team_id": str(world.north_team),
            "default_cap": 5,
            "rules": [bad_rule],
            "reason": "x",
        },
        headers=headers(world.admin),
    )
    assert r.status_code in (404, 409, 422), r.text
    r = await client.put(
        "/settings/assignment",
        json={
            "default_team_id": str(world.north_team),
            "default_cap": 5,
            "caps": {str(uuid.uuid4()): 3},
            "reason": "x",
        },
        headers=headers(world.admin),
    )
    assert r.status_code in (404, 409, 422), r.text
    r = await client.put(
        "/settings/assignment",
        json={"default_team_id": str(world.north_team), "default_cap": -1, "reason": "x"},
        headers=headers(world.admin),
    )
    assert r.status_code == 422
