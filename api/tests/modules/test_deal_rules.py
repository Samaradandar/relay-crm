# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Deal change rules: value, discounts, close date, pricing, source; activities and their
24-hour edit window; Undo. All over HTTP, per role."""

import uuid

from sqlalchemy import text

from tests.helpers import dispatch_all, headers
from tests.modules.journey import detail, due, log, new_deal, patch, setup_catalog
from tests.modules.test_leads_api import actor_by_id


async def line(client, actor, deal_id, product_id, qty=1000, expect=201, **kw):
    r = await client.post(
        f"/deals/{deal_id}/lines",
        json={"product_id": product_id, "quantity": qty, **kw},
        headers=headers(actor),
    )
    assert r.status_code == expect, r.text
    return r


async def set_qty(client, actor, deal_id, line_id, qty, expect=200, **kw):
    r = await client.patch(
        f"/deals/{deal_id}/lines/{line_id}", json={"quantity": qty, **kw}, headers=headers(actor)
    )
    assert r.status_code == expect, r.text
    return r


async def value(client, actor, deal_id) -> int:
    return (await detail(client, actor, deal_id))["value_paise"]


async def make_owner(admin_engine, deal_id, user):
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE deals SET owner_id=:u, team_id=:t WHERE id=:d"),
            {"u": user.id, "t": user.team_id, "d": deal_id},
        )


async def approve(client, actor, approval_id, reason="ok", decision="approve", expect=200):
    r = await client.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": decision, "reason": reason},
        headers=headers(actor),
    )
    assert r.status_code == expect, r.text
    return r


# ------------------------------------------------------------------------------ value changes
async def test_value_change_thresholds_for_a_rep(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)
    r = await line(client, owner, did, prod["id"], 1000)  # 0 -> 6,000.00: nothing to compare with
    lid = r.json()["lines"][0]["id"]
    assert await value(client, owner, did) == 600000
    await set_qty(client, owner, did, lid, 1040)  # +4%: allowed, no reason
    assert await value(client, owner, did) == 624000
    r = await set_qty(client, owner, did, lid, 1104, expect=422)  # +6.15%: needs a reason
    assert r.json()["code"] == "reason_required"
    await set_qty(client, owner, did, lid, 1104, reason="Customer added a branch")
    assert await value(client, owner, did) == 662400
    # +11% by a rep: becomes a request to the Team Leader and changes nothing yet
    r = await set_qty(client, owner, did, lid, 1230, expect=202, reason="Big expansion")
    assert r.json()["code"] == "approval_requested"
    assert await value(client, owner, did) == 662400
    approval = r.json()["approval_id"]
    await approve(client, world.tl_south, approval, expect=403)  # not their team
    await approve(client, owner, approval, expect=403)  # never your own
    await approve(client, world.tl_north, approval, reason=None)
    assert await value(client, owner, did) == 738000
    keys = [
        k
        for k, _ in await __import__("tests.modules.test_leads_api", fromlist=["x"]).audit_keys(did)
    ]
    assert "deal.value.above_10" in keys or "deal.value.above_10" in str(keys)


async def test_eleven_percent_by_a_team_leader_goes_to_an_admin(client, world, admin_engine):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)
    lid = (await line(client, owner, did, prod["id"], 1000)).json()["lines"][0]["id"]
    await make_owner(admin_engine, did, world.tl_north)
    r = await set_qty(client, world.tl_north, did, lid, 1115, expect=202, reason="Expansion")
    approval = r.json()["approval_id"]
    await approve(client, world.tl_north, approval, expect=403)  # a TL cannot approve their own
    await approve(
        client, world.admin, approval, reason=None, expect=422
    )  # Admin must give a reason
    await approve(client, world.admin, approval)
    assert await value(client, world.tl_north, did) == 669000
    # an Admin may change it directly, with a reason
    await set_qty(client, world.admin, did, lid, 2000, reason="Contract rewrite")
    assert await value(client, world.admin, did) == 1200000


# ------------------------------------------------------------------------------ discounts
async def test_a_reps_discount_needs_their_team_leader(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)
    r = await line(
        client,
        owner,
        did,
        prod["id"],
        1000,
        discount_pct="5",
        expect=202,
        reason="Competitor offer",
    )
    assert r.json()["code"] == "approval_requested"
    lines = (await client.get(f"/deals/{did}/lines", headers=headers(owner))).json()
    ln = lines["lines"][0]
    assert (
        ln["awaiting_approval"]
        and ln["discount_pct"] == "0.00"
        and ln["pending_discount_pct"] == "5.00"
    )
    assert lines["value_paise"] == 600000  # the discount has not touched the value
    approval = r.json()["approval_id"]
    await approve(client, world.admin, approval)  # Admin pool may also decide
    lines = (await client.get(f"/deals/{did}/lines", headers=headers(owner))).json()
    assert (
        lines["lines"][0]["discount_pct"] == "5.00" and not lines["lines"][0]["awaiting_approval"]
    )
    assert lines["value_paise"] == 570000


async def test_rejected_discount_goes_back_to_normal(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)
    r = await line(
        client, owner, did, prod["id"], 1000, discount_pct="8", expect=202, reason="Please"
    )
    await approve(
        client, world.tl_north, r.json()["approval_id"], reason="Too early", decision="reject"
    )
    ln = (await client.get(f"/deals/{did}/lines", headers=headers(owner))).json()["lines"][0]
    assert not ln["awaiting_approval"] and ln["discount_pct"] == "0.00"


async def test_over_ten_percent_or_below_floor_needs_an_admin(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)  # list 6.00, floor 4.50
    r = await line(
        client, owner, did, prod["id"], 1000, discount_pct="12", expect=202, reason="Big deal"
    )
    approval = r.json()["approval_id"]
    await approve(
        client, world.tl_north, approval, expect=403
    )  # a Team Leader cannot decide this one
    await approve(client, world.admin, approval)
    assert (await client.get(f"/deals/{did}/lines", headers=headers(owner))).json()[
        "value_paise"
    ] == 528000
    # 8% is within 10% but still lands under a 5.70 floor: Admin level
    r = await client.put(
        "/products",
        json={
            "products": [
                {
                    "name": "Premium",
                    "unit": "per_minute",
                    "list_price_paise": 600,
                    "floor_price_paise": 570,
                }
            ],
            "reason": "p",
        },
        headers=headers(world.admin),
    )
    premium = next(p for p in r.json() if p["name"] == "Premium")
    r = await line(
        client, owner, did, premium["id"], 100, discount_pct="8", expect=202, reason="Pls"
    )
    await approve(client, world.tl_north, r.json()["approval_id"], expect=403)


async def test_a_team_leader_may_give_up_to_ten_percent_with_a_reason(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)
    r = await client.post(
        f"/deals/{did}/lines",
        json={"product_id": prod["id"], "quantity": 1000, "discount_pct": "7"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await line(
        client, world.tl_north, did, prod["id"], 1000, discount_pct="7", reason="Loyal customer"
    )
    assert r.json()["value_paise"] == 558000
    # but not above 10%
    r = await client.post(
        f"/deals/{did}/lines",
        json={"product_id": prod["id"], "quantity": 10, "discount_pct": "11", "reason": "x"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 202


async def test_price_changes_never_touch_existing_lines(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)
    await line(client, owner, did, prod["id"], 1000)
    r = await client.put(
        "/products",
        json={
            "products": [
                {
                    "id": prod["id"],
                    "name": "Voice Agent",
                    "unit": "per_minute",
                    "list_price_paise": 900,
                    "floor_price_paise": 700,
                }
            ],
            "reason": "up",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    assert (await client.get(f"/deals/{did}/lines", headers=headers(owner))).json()["lines"][0][
        "unit_price_paise"
    ] == 600
    assert (
        await client.put(
            "/products", json={"products": [], "reason": "x"}, headers=headers(world.tl_north)
        )
    ).status_code == 403


# ------------------------------------------------------------------------------ other change rules
async def test_close_date_pricing_and_source_rules(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    await patch(
        client, owner, did, expected_close_date=due(30), expected_close_reason="Budget cycle"
    )  # first time: free
    r = await patch(
        client, owner, did, expected_close_date=due(45), expect=422
    )  # changing it needs a reason
    assert r.json()["code"] == "reason_required"
    await patch(client, owner, did, expected_close_date=due(45), reason="Customer asked")
    # pricing after Prospecting needs a reason (force the stage via the Admin override)
    await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "qualification", "override": True, "reason": "x"},
        headers=headers(world.admin),
    )
    await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "proposal", "override": True, "reason": "x"},
        headers=headers(world.admin),
    )
    r = await patch(client, owner, did, plan="Scale", expect=422)
    assert r.json()["code"] == "reason_required"
    await patch(client, owner, did, plan="Scale", reason="Upgraded")
    # a rep may not change the source after the Lead stage; the Team Leader may, with a reason
    r = await patch(client, owner, did, lead_source="Event", expect=403)
    r = await client.patch(
        f"/deals/{did}", json={"lead_source": "Event"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 422
    r = await client.patch(
        f"/deals/{did}",
        json={"lead_source": "Event", "reason": "Wrong source"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200 and r.json()["lead_source"] == "Event"
    # required fields are never emptied
    r = await patch(client, owner, did, expect=422, next_step_text=None)
    r = await patch(client, owner, did, expect=422, lead_source=None)
    # custom and fit-check values are validated
    r = await patch(client, owner, did, expect=422, custom_fields={"nope": "x"})
    r = await patch(client, owner, did, expect=422, fit_checklist={str(uuid.uuid4()): True})


async def test_undo_covers_the_new_fields(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    r = await patch(client, owner, did, use_case="Order status calls")
    audit_id = r.json()["last_audit_id"]
    stranger = world.north2 if owner.id == world.north1.id else world.north1
    assert (
        await client.post(
            f"/deals/{did}/undo", json={"audit_id": audit_id}, headers=headers(stranger)
        )
    ).status_code in (403, 404)
    r = await client.post(f"/deals/{did}/undo", json={"audit_id": audit_id}, headers=headers(owner))
    assert r.status_code == 200 and r.json()["use_case"] is None
    assert (
        await client.post(f"/deals/{did}/undo", json={"audit_id": audit_id}, headers=headers(owner))
    ).status_code == 409


# ------------------------------------------------------------------------------ activities
async def test_activity_validation_and_next_step(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    base = {
        "type": "call",
        "two_way": True,
        "discussed": "x",
        "next_step_text": "Call",
        "next_step_due_date": due(1),
    }

    async def post(**kw):
        return await client.post(
            f"/deals/{did}/activities", json={**base, **kw}, headers=headers(owner)
        )

    r = await post(two_way=None)
    assert r.status_code == 422 and r.json()["details"]["field"] == "two_way"
    r = await post(occurred_at="2099-01-01T10:00:00Z")
    assert r.status_code == 422  # only a meeting may be in the future
    assert (await post(type="meeting", occurred_at="2099-01-01T10:00:00Z")).status_code == 201
    assert (await post(next_step_due_date="2000-01-01")).status_code == 422
    r = await client.post(
        f"/deals/{did}/activities",
        json={k: v for k, v in base.items() if k != "next_step_text"},
        headers=headers(owner),
    )
    assert r.status_code == 422
    a = await log(
        client, owner, did, "email", next_step_text="Send deck", next_step_due_date=due(3)
    )
    d = await detail(client, owner, did)
    assert d["next_step"]["text"] == "Send deck" and d["last_audit_id"] is None
    assert a["created_by_name"] == owner.name and a["version_no"] == 1


async def test_activity_edit_window_and_ownership(client, world, admin_engine):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    mine = await log(client, owner, did, "call")
    tl_act = await log(client, world.tl_north, did, "note", two_way=False)

    async def edit(actor, act, expect, **kw):
        r = await client.patch(
            f"/activities/{act['id']}", json={"discussed": "Edited", **kw}, headers=headers(actor)
        )
        assert r.status_code == expect, (actor.role, r.text)
        return r

    r = await edit(owner, mine, 422)  # an edit needs a reason
    assert r.json()["code"] == "reason_required"
    await edit(owner, mine, 200, reason="Typo")
    versions = (
        await client.get(f"/activities/{mine['id']}/versions", headers=headers(owner))
    ).json()
    assert [v["version_no"] for v in versions] == [1, 2] and versions[0]["body"][
        "discussed"
    ] == "Talked it through"
    # nobody edits another person's activity, whatever their role
    for who in (world.tl_north, world.admin, world.sa):
        await edit(who, mine, 403, reason="mine now")
    await edit(owner, tl_act, 403, reason="not mine")
    # after 24 hours a rep and a Team Leader can no longer edit their own; Admin and SA still can
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE activities SET created_at = now() - interval '25 hours'"))
    await edit(owner, mine, 403, reason="late")
    await edit(world.tl_north, tl_act, 403, reason="late")
    admin_act = await log(client, world.admin, did, "note", two_way=False, reason="adding a note")
    sa_act = await log(client, world.sa, did, "note", two_way=False)
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE activities SET created_at = now() - interval '48 hours'"))
    await edit(world.admin, admin_act, 200, reason="fix")
    await edit(world.sa, sa_act, 200)
    d = (await client.get(f"/deals/{did}/activities", headers=headers(owner))).json()
    assert len(d) >= 4


async def test_activity_delete_rights(client, world):
    deal, owner = await new_deal(client, world)
    act = await log(client, owner, deal["id"], "call")
    assert (
        await client.delete(
            f"/activities/{act['id']}", params={"reason": "x"}, headers=headers(owner)
        )
    ).status_code == 403
    assert (
        await client.delete(f"/activities/{act['id']}", headers=headers(world.tl_north))
    ).status_code == 422
    assert (
        await client.delete(
            f"/activities/{act['id']}", params={"reason": "dup"}, headers=headers(world.tl_south)
        )
    ).status_code in (403, 404)
    assert (
        await client.delete(
            f"/activities/{act['id']}", params={"reason": "dup"}, headers=headers(world.tl_north)
        )
    ).status_code == 204
    assert (
        await client.get(f"/deals/{deal['id']}/activities", headers=headers(owner))
    ).json() == []
    _ = (dispatch_all, actor_by_id)
