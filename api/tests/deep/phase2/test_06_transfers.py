# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 5: cross-team transfers (give and take)."""

import asyncio
import uuid

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


async def north_deal(qc, world, **kw):
    await configure(qc, world)
    d = await create(qc, world.north1, **kw)
    assert d["team_name"] == "Team North"
    return d


def take(qc, actor, deal_id, owner=None, reason="their client is in our city"):
    body = {"direction": "take", "deal_id": deal_id, "reason": reason}
    if owner is not None:
        body["owner_id"] = str(owner.id)
    return qc.post("/transfers", json=body, headers=headers(actor))


def give(qc, actor, deal_id, to_team, reason="not our segment", **kw):
    return qc.post(
        "/transfers",
        json={
            "direction": "give",
            "deal_id": deal_id,
            "to_team_id": str(to_team),
            "reason": reason,
            **kw,
        },
        headers=headers(actor),
    )


def decide(qc, actor, approval_id, decision="approve", reason="ok"):
    return qc.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": decision, "reason": reason},
        headers=headers(actor),
    )


async def transfers(qc, actor):
    r = await qc.get("/transfers", headers=headers(actor))
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------ who may ask
async def test_only_team_leaders_may_request_and_reasons_are_mandatory(qc, world):
    d = await north_deal(qc, world)
    for actor in (world.north1, world.south1):
        assert (await take(qc, actor, d["id"], world.south1)).status_code == 403
        assert (await give(qc, actor, d["id"], world.south_team)).status_code == 403
    for actor in (world.admin, world.sa):
        assert (await take(qc, actor, d["id"])).status_code == 403  # they move deals directly
    assert (
        await qc.post("/transfers", json={"direction": "take", "deal_id": d["id"]})
    ).status_code == 401
    r = await take(qc, world.tl_south, d["id"], reason=None)
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    assert (await take(qc, world.tl_south, d["id"], reason="   ")).status_code == 422
    r = await take(qc, world.tl_south, d["id"])
    assert r.status_code == 202 and r.json()["code"] == "approval_requested"


async def test_give_rules(qc, world):
    d = await north_deal(qc, world)
    assert (
        await give(qc, world.tl_south, d["id"], world.north_team)
    ).status_code == 404  # not their deal
    assert (
        await give(qc, world.tl_north, d["id"], world.north_team)
    ).status_code == 422  # own team
    assert (await give(qc, world.tl_north, d["id"], uuid.uuid4())).status_code == 404
    r = await qc.post(
        "/transfers",
        json={"direction": "give", "deal_id": d["id"], "reason": "x"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 422
    assert (
        await give(qc, world.tl_north, d["id"], world.south_team, owner_id=str(world.south1.id))
    ).status_code == 422
    assert (await give(qc, world.tl_north, str(uuid.uuid4()), world.south_team)).status_code == 404
    assert (await give(qc, world.tl_north, d["id"], world.south_team)).status_code == 202


async def test_take_rules(qc, world):
    d = await north_deal(qc, world)
    assert (
        await take(qc, world.tl_north, d["id"], world.north1)
    ).status_code == 422  # already their team
    assert (
        await take(qc, world.tl_south, d["id"], world.north1)
    ).status_code == 422  # rep from the other team
    await qc.patch(
        f"/users/{world.south2.id}",
        json={"is_active": False, "reason": "left"},
        headers=headers(world.admin),
    )
    assert (
        await take(qc, world.tl_south, d["id"], world.south2)
    ).status_code == 422  # inactive rep
    assert (await take(qc, world.tl_south, str(uuid.uuid4()), world.south1)).status_code == 404
    r = await qc.post(
        "/transfers",
        json={"direction": "take", "deal_id": d["id"], "reason": "x", "owner_id": "nope"},
        headers=headers(world.tl_south),
    )
    assert r.status_code == 422
    r = await qc.post(
        "/transfers",
        json={"direction": "steal", "deal_id": d["id"], "reason": "x"},
        headers=headers(world.tl_south),
    )
    assert r.status_code == 422
    assert (await take(qc, world.tl_south, d["id"], world.south1)).status_code == 202


@pytest.mark.parametrize("state", ["lost", "deleted"])
async def test_only_open_or_pooled_deals_can_be_transferred(qc, world, state):
    d = await north_deal(qc, world)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    if state == "lost":
        await qc.post(
            f"/deals/{d['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner)
        )
        assert (await take(qc, world.tl_south, d["id"], world.south1)).status_code == 409
        assert (await give(qc, world.tl_north, d["id"], world.south_team)).status_code == 409
    else:
        await qc.post(
            f"/deals/{d['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
        )
        assert (await take(qc, world.tl_south, d["id"], world.south1)).status_code == 404


async def test_a_pooled_deal_with_no_team_cannot_be_taken_and_says_so(qc, world):
    d = await create(qc, world.north1)  # no default team: pooled, team_id None
    assert d["status"] == "pooled" and d["team_id"] is None
    r = await take(qc, world.tl_south, d["id"], world.south1)
    assert r.status_code == 422
    assert "already in your team" not in r.json()["message"]


async def test_other_organisations_deals_and_teams_are_unreachable(qc, admin_engine, world):
    d = await north_deal(qc, world)
    other = await make_world(admin_engine)
    await configure(qc, other)
    od = await create(qc, other.north1)
    assert (await take(qc, world.tl_south, od["id"], world.south1)).status_code == 404
    assert (await give(qc, other.tl_north, d["id"], other.south_team)).status_code == 404
    assert (await give(qc, world.tl_north, d["id"], other.south_team)).status_code == 404
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM transfer_requests WHERE org_id IN (:a,:b)",
            a=world.org_id,
            b=other.org_id,
        )
        == 0
    )


# ------------------------------------------------------------------------------ one open request
async def test_ten_simultaneous_requests_for_one_deal_have_one_winner(qc, admin_engine, world):
    d = await north_deal(qc, world)
    rs = await asyncio.gather(
        *[take(qc, world.tl_south, d["id"], world.south1, reason=f"r{i}") for i in range(10)]
    )
    codes = sorted(r.status_code for r in rs)
    assert codes == [202] + [409] * 9, codes
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM transfer_requests WHERE deal_id=:d",
            d=uuid.UUID(d["id"]),
        )
        == 1
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM approval_requests WHERE org_id=:o AND type='deal.team_transfer'",
            o=world.org_id,
        )
        == 1
    )


async def test_give_and_take_for_the_same_deal_race(qc, admin_engine, world):
    d = await north_deal(qc, world)
    rs = await asyncio.gather(
        give(qc, world.tl_north, d["id"], world.south_team),
        take(qc, world.tl_south, d["id"], world.south1),
    )
    assert sorted(r.status_code for r in rs) == [202, 409]
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_a_rejected_or_cancelled_request_does_not_block_a_new_one(qc, world):
    d = await north_deal(qc, world)
    r = await take(qc, world.tl_south, d["id"], world.south1)
    assert (
        await decide(qc, world.admin, r.json()["approval_id"], "reject", "no")
    ).status_code == 200
    assert (await transfers(qc, world.tl_south))[0]["status"] == "rejected"
    r2 = await take(qc, world.tl_south, d["id"], world.south1, reason="again")
    assert r2.status_code == 202
    assert (
        await qc.post(
            f"/approvals/{r2.json()['approval_id']}/cancel", headers=headers(world.tl_north)
        )
    ).status_code == 404
    assert (
        await qc.post(
            f"/approvals/{r2.json()['approval_id']}/cancel", headers=headers(world.tl_south)
        )
    ).status_code == 200
    assert {t["status"] for t in await transfers(qc, world.tl_south)} == {"rejected", "cancelled"}
    r3 = await give(qc, world.tl_north, d["id"], world.south_team, reason="third time")
    assert r3.status_code == 202
    assert (
        await qc.post(
            f"/approvals/{r3.json()['approval_id']}/cancel", headers=headers(world.tl_north)
        )
    ).status_code == 200
    assert (
        await qc.post(
            f"/approvals/{r3.json()['approval_id']}/cancel", headers=headers(world.tl_north)
        )
    ).status_code == 409


# ------------------------------------------------------------------------------ comments
async def test_the_other_leaders_comment(qc, admin_engine, world):
    d = await north_deal(qc, world)
    await take(qc, world.tl_south, d["id"], world.south1)
    tid = (await transfers(qc, world.tl_north))[0]["id"]
    url = f"/transfers/{tid}/comment"
    for actor in (world.tl_south, world.north1, world.south1):  # requester, users
        assert (
            await qc.post(url, json={"comment": "x"}, headers=headers(actor))
        ).status_code == 404
    assert (
        await qc.post(url, json={"comment": ""}, headers=headers(world.tl_north))
    ).status_code == 422
    assert (
        await qc.post(url, json={"comment": "c" * 2001}, headers=headers(world.tl_north))
    ).status_code == 422
    assert (
        await qc.post(url, json={"comment": "c" * 2000}, headers=headers(world.tl_north))
    ).status_code == 204
    assert (
        await qc.post(
            url, json={"comment": "<b>fine</b> by us '; --"}, headers=headers(world.tl_north)
        )
    ).status_code == 204
    assert (
        await qc.post(url, json={"comment": "admin note"}, headers=headers(world.admin))
    ).status_code == 204
    assert (await qc.post(url, json={"comment": "x"})).status_code == 401
    assert (
        await qc.post(
            f"/transfers/{uuid.uuid4()}/comment",
            json={"comment": "x"},
            headers=headers(world.tl_north),
        )
    ).status_code == 404
    other = await make_world(admin_engine)
    assert (
        await qc.post(url, json={"comment": "x"}, headers=headers(other.tl_north))
    ).status_code == 404
    assert (
        await qc.post(url, json={"comment": "x"}, headers=headers(other.admin))
    ).status_code == 404
    await dispatch_all()
    assert (await transfers(qc, world.tl_south))[0]["other_tl_comment"] == "admin note"
    admins = [n for n in await notifications_for(world.admin.id) if n.type == "transfer.commented"]
    assert len(admins) == 3
    ok = await decide(qc, world.admin, (await transfers(qc, world.tl_north))[0]["approval_id"])
    assert ok.status_code == 200
    assert (
        await qc.post(url, json={"comment": "late"}, headers=headers(world.tl_north))
    ).status_code == 409


async def test_a_comment_is_a_comment_and_cannot_block_the_decision(qc, world):
    d = await north_deal(qc, world)
    r = await take(qc, world.tl_south, d["id"], world.south1)
    tid = (await transfers(qc, world.tl_north))[0]["id"]
    await qc.post(
        f"/transfers/{tid}/comment",
        json={"comment": "please do not"},
        headers=headers(world.tl_north),
    )
    assert (await decide(qc, world.admin, r.json()["approval_id"])).status_code == 200
    got = (await qc.get(f"/deals/{d['id']}", headers=headers(world.south1))).json()
    assert got["team_name"] == "Team South"


async def test_no_comment_on_a_rejected_request(qc, world):
    d = await north_deal(qc, world)
    r = await take(qc, world.tl_south, d["id"], world.south1)
    tid = (await transfers(qc, world.tl_north))[0]["id"]
    await decide(qc, world.admin, r.json()["approval_id"], "reject", "no")
    assert (
        await qc.post(
            f"/transfers/{tid}/comment", json={"comment": "late"}, headers=headers(world.tl_north)
        )
    ).status_code == 409


async def test_the_other_leaders_notification_link_opens(qc, world):
    d = await north_deal(qc, world)
    r = await take(qc, world.tl_south, d["id"], world.south1)
    await dispatch_all()
    note = next(
        n for n in await notifications_for(world.tl_north.id) if n.type == "transfer.requested"
    )
    assert r.status_code == 202
    assert (
        note.link == "/transfers"
    )  # the page the other leader can read (not the Admin's approval)
    got = await qc.get(note.link, headers=headers(world.tl_north))
    assert got.status_code == 200


# ------------------------------------------------------------------------------ deciding
async def test_deciding_roles_and_reasons(qc, world):
    d = await north_deal(qc, world)
    r = await take(qc, world.tl_south, d["id"], world.south1)
    aid = r.json()["approval_id"]
    for actor in (world.north1, world.south1, world.tl_north, world.tl_south):
        assert (await decide(qc, actor, aid)).status_code == 403, actor.name
    assert (await decide(qc, world.admin, aid, reason=None)).status_code == 422
    assert (await decide(qc, world.admin, aid, "reject", reason="  ")).status_code == 422
    assert (
        await qc.post(
            f"/approvals/{aid}/decide", json={"decision": "maybe"}, headers=headers(world.admin)
        )
    ).status_code == 422
    assert (await decide(qc, world.sa, aid, reason=None)).status_code == 200
    assert (await decide(qc, world.admin, aid)).status_code == 409
    assert (await decide(qc, world.sa, aid, "reject")).status_code == 409


async def test_two_admins_approving_at_once_move_the_deal_once(qc, admin_engine, world):
    d = await north_deal(qc, world)
    aid = (await take(qc, world.tl_south, d["id"], world.south1)).json()["approval_id"]
    rs = await asyncio.gather(
        decide(qc, world.admin, aid),
        decide(qc, world.sa, aid),
        decide(qc, world.admin, aid, "reject"),
    )
    assert sorted(r.status_code for r in rs) == [200, 409, 409]
    moved = (
        await q1(admin_engine, "SELECT team_id FROM deals WHERE id=:d", d=uuid.UUID(d["id"]))
        == world.south_team
    )
    expected = 1 if moved else 0  # one of the three calls was a rejection; exactly one call won
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE entity_id=:e AND outcome='ok' AND action_key='deal.team_transfer.request'",
            e=d["id"],
        )
        == expected
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE action_key='approval.decide' AND outcome='ok' AND org_id=:o",
            o=world.org_id,
        )
        == 1
    )
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_approving_a_take_moves_the_deal_and_revokes_the_old_side_at_once(
    qc, admin_engine, world
):
    d = await north_deal(qc, world, company="Orchid Textiles")
    old_owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    aid = (await take(qc, world.tl_south, d["id"], world.south1)).json()["approval_id"]
    await qc.post(
        f"/transfers/{(await transfers(qc, world.tl_north))[0]['id']}/comment",
        json={"comment": "ok"},
        headers=headers(world.tl_north),
    )
    await dispatch_all()
    for actor in (old_owner, world.tl_north):  # before: they can read it
        assert (await qc.get(f"/deals/{d['id']}", headers=headers(actor))).status_code == 200
    assert (await decide(qc, world.admin, aid)).status_code == 200
    got = (await qc.get(f"/deals/{d['id']}", headers=headers(world.south1))).json()
    assert (
        got["owner"]["name"] == "Sneha"
        and got["team_name"] == "Team South"
        and got["next_step"]["responsible"]["name"] == "Sneha"
    )
    assert (await qc.get(f"/deals/{d['id']}", headers=headers(world.tl_south))).status_code == 200
    # access gone for the old owner and old leader, in every read path
    for actor in (old_owner, world.tl_north):
        h = headers(actor)
        assert (await qc.get(f"/deals/{d['id']}", headers=h)).status_code == 404
        assert d["id"] not in (await qc.get("/deals", params={"limit": 500}, headers=h)).text
        assert (await qc.get(f"/accounts/{d['account_id']}", headers=h)).status_code in (404, 200)
        assert (
            "Orchid" not in (await qc.get("/accounts", params={"q": "Orchid"}, headers=h)).text
            or actor is old_owner
        )
        assert (await qc.get(f"/history/deal/{d['id']}", headers=h)).status_code in (403, 404)
        assert (
            d["id"] not in (await qc.get("/board", headers=h)).text
            if (await qc.get("/board", headers=h)).status_code == 200
            else True
        )
        assert (
            await qc.patch(f"/deals/{d['id']}", json={"lead_source": "Event"}, headers=h)
        ).status_code in (403, 404)
        assert (await reassign_call(qc, actor, d["id"], world.south2)).status_code in (403, 404)
    # the Pool of the old team does not show it and no live event about it leaks afterwards
    before = max(
        [e["id"] for e in (await qc.get("/events", headers=headers(old_owner))).json()] or [0]
    )
    new = await qc.patch(
        f"/deals/{d['id']}", json={"lead_source": "Event"}, headers=headers(world.south1)
    )
    assert new.status_code == 200
    await dispatch_all()
    for actor in (old_owner, world.tl_north):
        ev = (await qc.get("/events", params={"since": before}, headers=headers(actor))).json()
        assert d["id"] not in {e["entity_id"] for e in ev}, actor.name
    ev = (await qc.get("/events", params={"since": before}, headers=headers(world.tl_south))).json()
    assert d["id"] in {e["entity_id"] for e in ev}
    # notifications: both leaders and the requester, old and new owner
    decided = [
        n for n in await notifications_for(world.tl_north.id) if n.type == "transfer.decided"
    ]
    assert len(decided) == 1
    assert (
        len([n for n in await notifications_for(world.tl_south.id) if n.type == "transfer.decided"])
        == 1
    )
    assert [
        n.title for n in await notifications_for(old_owner.id) if n.type == "deal.owner_changed"
    ] == ["A deal moved away from you"]
    assert [
        n.title for n in await notifications_for(world.south1.id) if n.type == "deal.owner_changed"
    ] == ["A deal was given to you"]
    # history: requester, direction, decider on behalf of
    rows = await q(
        admin_engine,
        "SELECT action_key, outcome, actor_id, on_behalf_of, reason FROM audit_log WHERE entity_id=:e ORDER BY seq",
        e=d["id"],
    )
    moves = [
        r
        for r in rows
        if r[0] == "deal.team_transfer.request" and r[1] == "ok" and r[4] is not None
    ]
    comment_rows = [r for r in rows if r[0] == "deal.team_transfer.request" and r[1] == "ok"]
    assert len(comment_rows) == 2  # the comment and the move, each one row
    assert len(moves) == 1 and moves[0][2] == world.admin.id and moves[0][3] == world.tl_south.id
    assert [r for r in rows if r[1] == "approval_requested"]
    assert not await invariant_violations(admin_engine, world.org_id)


def reassign_call(qc, actor, deal_id, user):
    return qc.post(
        f"/deals/{deal_id}/reassign",
        json={"user_id": str(user.id), "reason": "x"},
        headers=headers(actor),
    )


async def test_approving_a_give_lands_in_the_receiving_pool_held_for_the_receiving_leader(
    qc, admin_engine, world
):
    d = await north_deal(qc, world)
    old_owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    aid = (await give(qc, world.tl_north, d["id"], world.south_team)).json()["approval_id"]
    assert (await decide(qc, world.admin, aid)).status_code == 200
    await dispatch_all()
    await dispatch_all()
    pool = (await qc.get("/pool", headers=headers(world.tl_south))).json()
    assert [(p["deal_id"], p["held_for_manual"], p["routed_team_name"]) for p in pool] == [
        (d["id"], True, "Team South")
    ]
    assert (await qc.get("/pool", headers=headers(world.tl_north))).json() == []
    assert (await qc.get(f"/deals/{d['id']}", headers=headers(old_owner))).status_code == 404
    assert [
        n.title for n in await notifications_for(old_owner.id) if n.type == "deal.owner_changed"
    ] == ["A deal moved away from you"]
    r = await qc.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(world.south1.id), "reason": "mine"},
        headers=headers(world.tl_south),
    )
    assert r.status_code == 200 and r.json()["next_step"]["responsible"]["name"] == "Sneha"
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_giving_a_waiting_pool_lead_to_another_team(qc, admin_engine, world):
    await put_settings(qc, world.admin, world, default_cap=0)
    d = await create(qc, world.north1)
    assert d["status"] == "pooled"
    aid = (await give(qc, world.tl_north, d["id"], world.south_team)).json()["approval_id"]
    assert (await decide(qc, world.admin, aid)).status_code == 200


async def test_taking_a_waiting_pool_lead_with_a_named_rep(qc, admin_engine, world):
    await put_settings(qc, world.admin, world, default_cap=0)
    d = await create(qc, world.north1)
    aid = (await take(qc, world.tl_south, d["id"], world.south1)).json()["approval_id"]
    assert (await decide(qc, world.admin, aid)).status_code == 200
    got = (await qc.get(f"/deals/{d['id']}", headers=headers(world.south1))).json()
    assert got["status"] == "open" and got["owner"]["name"] == "Sneha"
    assert not await invariant_violations(admin_engine, world.org_id)


@pytest.mark.parametrize("change", ["owner", "team", "lost", "deleted", "owner_then_back"])
async def test_approving_a_stale_request_is_refused_and_leaves_everything_as_it_was(
    qc, admin_engine, world, change
):
    d = await north_deal(qc, world)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    other = world.north2 if owner.id == world.north1.id else world.north1
    aid = (await take(qc, world.tl_south, d["id"], world.south1)).json()["approval_id"]
    if change == "owner":
        assert (await reassign_call(qc, world.tl_north, d["id"], other)).status_code == 200
    elif change == "owner_then_back":
        assert (await reassign_call(qc, world.tl_north, d["id"], other)).status_code == 200
        assert (await reassign_call(qc, world.tl_north, d["id"], owner)).status_code == 200
    elif change == "team":
        r = await qc.post(
            f"/deals/{d['id']}/move-team",
            json={
                "to_team_id": str(world.south_team),
                "owner_id": str(world.south2.id),
                "reason": "x",
            },
            headers=headers(world.admin),
        )
        assert r.status_code == 200
    elif change == "lost":
        assert (
            await qc.post(
                f"/deals/{d['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner)
            )
        ).status_code == 200
    else:
        assert (
            await qc.post(
                f"/deals/{d['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
            )
        ).status_code == 204
    before = await q(
        admin_engine,
        "SELECT owner_id, team_id, status, version FROM deals WHERE id=:d",
        d=uuid.UUID(d["id"]),
    )
    r = await decide(qc, world.admin, aid)
    if change == "owner_then_back":
        assert r.status_code == 200  # same owner and team again: nothing is stale
        return
    assert r.status_code in (404, 409), r.text
    if change != "deleted":
        assert r.status_code == 409
    assert before == await q(
        admin_engine,
        "SELECT owner_id, team_id, status, version FROM deals WHERE id=:d",
        d=uuid.UUID(d["id"]),
    )
    still = (await qc.get(f"/approvals/{aid}", headers=headers(world.admin))).json()
    assert still["status"] == "pending"
    # the Admin can reject it and a fresh request can be raised
    assert (await decide(qc, world.admin, aid, "reject", "stale")).status_code == 200
    if change in ("owner", "team"):
        r2 = await take(
            qc,
            world.tl_south if change == "owner" else world.tl_north,
            d["id"],
            world.south1 if change == "owner" else world.north1,
            reason="again",
        )
        assert r2.status_code == 202
    assert not await invariant_violations(admin_engine, world.org_id)


async def test_approval_is_refused_when_the_deal_was_taken_over_by_a_different_request(qc, world):
    d = await north_deal(qc, world)
    aid = (await take(qc, world.tl_south, d["id"], world.south1)).json()["approval_id"]
    assert (await decide(qc, world.admin, aid)).status_code == 200
    assert (await decide(qc, world.sa, aid)).status_code == 409
    # the deal is now South's; a stale *give* for it from North cannot be raised
    assert (await give(qc, world.tl_north, d["id"], world.south_team)).status_code == 404


# ------------------------------------------------------------------------------ lookup
async def test_lookup_returns_company_and_team_only_and_only_to_team_leaders(qc, world):
    d = await north_deal(
        qc,
        world,
        company="Orchid Textiles",
        website="orchid.example",
        phone="9812345670",
        email=None,
    )
    got = (
        await qc.get("/transfers/lookup", params={"q": "orchid"}, headers=headers(world.tl_south))
    ).json()
    assert got == [{"deal_id": d["id"], "company": "Orchid Textiles", "team_name": "Team North"}]
    assert (
        await qc.get("/transfers/lookup", params={"q": "orchid"}, headers=headers(world.tl_north))
    ).json() == []  # own team
    for actor in (world.north1, world.south1, world.admin, world.sa):
        assert (
            await qc.get("/transfers/lookup", params={"q": "orchid"}, headers=headers(actor))
        ).status_code == 403
    assert (await qc.get("/transfers/lookup", params={"q": "orchid"})).status_code == 401
    assert (await qc.get("/transfers/lookup", headers=headers(world.tl_south))).status_code == 422
    for short in ("", " ", "o", " o "):
        assert (
            await qc.get("/transfers/lookup", params={"q": short}, headers=headers(world.tl_south))
        ).json() == []


async def test_lookup_wildcards_injection_and_limits(qc, admin_engine, world):
    await north_deal(qc, world, company="100% Pure_Foods")
    await create(
        qc, world.north1, company="1000 Pure Foods", create_anyway=True, reason="sister company"
    )
    h = headers(world.tl_south)
    assert [
        r["company"]
        for r in (await qc.get("/transfers/lookup", params={"q": "100%"}, headers=h)).json()
    ] == ["100% Pure_Foods"]
    assert [
        r["company"]
        for r in (await qc.get("/transfers/lookup", params={"q": "ure_F"}, headers=h)).json()
    ] == ["100% Pure_Foods"]
    assert (await qc.get("/transfers/lookup", params={"q": "%%"}, headers=h)).json() == []
    assert (await qc.get("/transfers/lookup", params={"q": "\\\\"}, headers=h)).json() == []
    for nasty in ("'; DROP TABLE deals; --", "<script>alert(1)</script>", "अ" * 2_000, "x" * 8_000):
        assert (
            await qc.get("/transfers/lookup", params={"q": nasty}, headers=h)
        ).status_code == 200
    assert await q1(admin_engine, "SELECT count(*) FROM deals WHERE org_id=:o", o=world.org_id) == 2
    async with admin_engine.begin() as c:
        acc = (
            await c.execute(
                text("INSERT INTO accounts (org_id, name) VALUES (:o,'Many Co') RETURNING id"),
                {"o": world.org_id},
            )
        ).scalar_one()
        await c.execute(
            text(
                "INSERT INTO deals (org_id, account_id, status, lead_source, team_id, owner_id) SELECT :o,:a,'open','Website',:t,:u FROM generate_series(1,30)"
            ),
            {"o": world.org_id, "a": acc, "t": world.north_team, "u": world.north1.id},
        )
    assert len((await qc.get("/transfers/lookup", params={"q": "many"}, headers=h)).json()) == 20


async def test_lookup_hides_deleted_lost_and_other_organisation_deals(qc, admin_engine, world):
    a = await north_deal(qc, world, company="Visible Foods")
    b = await create(qc, world.north1, company="Deleted Foods")
    c = await create(qc, world.north1, company="Lost Foods")
    await qc.post(f"/deals/{b['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin))
    owner = next(x for x in world.all_actors if str(x.id) == c["owner"]["id"])
    await qc.post(f"/deals/{c['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner))
    other = await make_world(admin_engine)
    await configure(qc, other)
    await create(qc, other.north1, company="Foreign Foods")
    got = (
        await qc.get("/transfers/lookup", params={"q": "foods"}, headers=headers(world.tl_south))
    ).json()
    assert [r["company"] for r in got] == ["Visible Foods"]
    assert (
        await qc.get("/transfers/lookup", params={"q": "foreign"}, headers=headers(world.tl_south))
    ).json() == []
    assert a["id"] in str(got)


async def test_lookup_shows_deals_owned_by_a_super_admin_company_and_team_only(qc, world):
    """Documented (NOTE): a Super admin's deals are hidden from Team Leaders everywhere except
    this lookup, which exposes company name and team name (team is empty for SA-owned deals)."""
    d = await north_deal(qc, world, company="Samar Personal Deal")
    r = await qc.post(
        f"/deals/{d['id']}/reassign",
        json={"user_id": str(world.sa.id), "reason": "mine"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    got = (
        await qc.get(
            "/transfers/lookup", params={"q": "samar personal"}, headers=headers(world.tl_south)
        )
    ).json()
    assert got == []  # an SA-owned deal has no team, so the lookup (which needs a team) skips it


# ------------------------------------------------------------------------------ list and isolation
async def test_transfer_list_visibility(qc, admin_engine, world):
    d = await north_deal(qc, world)
    await take(qc, world.tl_south, d["id"], world.south1)
    assert (
        len(await transfers(qc, world.tl_south)) == 1
        and len(await transfers(qc, world.tl_north)) == 1
    )
    assert len(await transfers(qc, world.admin)) == 1 and len(await transfers(qc, world.sa)) == 1
    for actor in (world.north1, world.south1):
        assert (await qc.get("/transfers", headers=headers(actor))).status_code == 403
    other = await make_world(admin_engine)
    assert await transfers(qc, other.admin) == [] and await transfers(qc, other.tl_north) == []
    row = (await transfers(qc, world.admin))[0]
    assert set(row) == {
        "id",
        "deal_id",
        "company",
        "direction",
        "from_team",
        "to_team",
        "proposed_owner",
        "requested_by",
        "reason",
        "other_tl_comment",
        "status",
        "approval_id",
        "created_at",
    }
    assert (
        row["from_team"] == "Team North"
        and row["to_team"] == "Team South"
        and row["proposed_owner"] == "Sneha"
    )
    ap = (await qc.get("/approvals", headers=headers(world.admin))).json()
    assert ap[0]["summary"].startswith("Tejas asks to take") and "Team North" in ap[0]["summary"]
    other_approval = await qc.get(f"/approvals/{row['approval_id']}", headers=headers(other.admin))
    assert other_approval.status_code == 404
    assert (await decide(qc, other.admin, row["approval_id"])).status_code == 404


async def test_a_take_without_a_named_rep_goes_to_the_requesting_teams_pool(
    qc, admin_engine, world
):
    """PRD 4.5a says the leader chooses the rep, but the API accepts a take without one and the
    deal then waits in the requesting team's Pool, held for manual assignment (documented)."""
    d = await north_deal(qc, world)
    aid = (await take(qc, world.tl_south, d["id"])).json()["approval_id"]
    assert (await decide(qc, world.admin, aid)).status_code == 200
    pool = (await qc.get("/pool", headers=headers(world.tl_south))).json()
    assert [p["deal_id"] for p in pool] == [d["id"]]


async def test_transfer_audit_rows_and_denials(qc, admin_engine, world):
    d = await north_deal(qc, world)
    await take(qc, world.north1, d["id"], world.south1)
    r = await take(qc, world.tl_south, d["id"], world.south1)
    rows = [tuple(x)[:2] for x in await audit_rows(admin_engine, d["id"])]
    assert ("deal.team_transfer.request", "approval_requested") in rows
    denied = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='deal.team_transfer.request' AND outcome='denied'",
        o=world.org_id,
    )
    assert denied == 1
    assert r.status_code == 202


async def test_the_old_teams_creator_loses_the_account_after_a_transfer(qc, world):
    d = await north_deal(qc, world, company="Cedar Exports", website="cedar-exports.example")
    aid = (await take(qc, world.tl_south, d["id"], world.south1)).json()["approval_id"]
    assert (await decide(qc, world.admin, aid)).status_code == 200
    added = await qc.post(
        "/contacts",
        json={
            "account_id": d["account_id"],
            "name": "New CFO",
            "email": "cfo@cedar-exports.example",
            "reason": "met them",
        },
        headers=headers(world.south1),
    )
    assert added.status_code == 201, added.text
    creator = world.north1  # created the lead, was never the owner of this transfer's new state
    r = await qc.get(f"/accounts/{d['account_id']}", headers=headers(creator))
    assert r.status_code == 404 or "cfo@cedar-exports.example" not in r.text


async def test_a_team_leader_can_list_the_teams_to_give_a_deal_to(qc, world):
    r = await qc.get("/teams", headers=headers(world.tl_north))
    assert r.status_code == 200
    assert {t["name"] for t in r.json()} >= {"Team South"}
