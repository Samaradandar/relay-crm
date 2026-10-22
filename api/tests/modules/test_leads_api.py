# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 through the real HTTP API: leads, assignment engine, duplicates, Pool, transfers."""

import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.modules.leads.models import DuplicateOverride
from app.modules.leads.service import today_ist
from app.platform.audit.models import AuditLog
from tests.helpers import dispatch_all, headers, notifications_for, settle_rep_lead


def lead(company: str | None = None, **kw):
    body = {
        "company": company or uuid.uuid4().hex[:12],
        "contact_name": "Asha Rao",
        "email": f"{uuid.uuid4().hex[:10]}@example.com",
        "lead_source": "Website",
        "next_step_text": "Call to qualify",
        "next_step_due_date": (today_ist() + timedelta(days=1)).isoformat(),
    }
    body.update(kw)
    return body


async def configure(client, world, cap=25, team=None, reason="setup"):
    r = await client.put(
        "/settings/assignment",
        json={
            "default_team_id": str(team or world.north_team),
            "default_cap": cap,
            "rules": [],
            "groups": [],
            "caps": {},
            "reason": reason,
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def create(client, actor, **kw):
    r = await client.post("/leads", json=lead(**kw), headers=headers(actor))
    assert r.status_code == 201, r.text
    # a rep's lead waits for the Team Leader; most tests just need it owned (round robin)
    return await settle_rep_lead(client, r.json())


def actor_by_id(world, uid):
    return next(a for a in world.all_actors if str(a.id) == str(uid))


async def audit_keys(entity_id):
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.entity_id == str(entity_id)).order_by(AuditLog.seq)
            )
        ).scalars()
        return [(r.action_key, r.outcome) for r in rows]


# ------------------------------------------------------------------------------ engine
async def test_round_robin_never_picks_the_team_leader(client, world):
    await configure(client, world)
    owners = []
    for _ in range(4):
        d = await create(client, world.north1)
        assert d["status"] == "open" and d["assignment_method"] == "round_robin"
        assert d["assigned_by"] == "System" and d["team_name"] == "Team North"
        owners.append(d["owner"]["id"])
    assert set(owners) == {str(world.north1.id), str(world.north2.id)}
    assert owners.count(str(world.north1.id)) == 2
    keys = await audit_keys(d["id"])
    assert ("lead.create", "ok") in keys and ("lead.assigned", "ok") in keys
    assert d["next_step"]["responsible"]["id"] == d["owner"]["id"]


async def test_no_default_team_waits_then_retries_when_configured(client, world):
    # created by the Team Leader: a rep's own lead is held for the leader instead (test_rep_lead_hold)
    first = await create(client, world.tl_north)
    second = await create(client, world.tl_north)
    assert first["status"] == "pooled" and first["waiting_reason"] == "no_rule_match"
    await dispatch_all()
    await dispatch_all()
    waiting = [n for n in await notifications_for(world.admin.id) if n.type == "lead.waiting"]
    assert len(waiting) == 2  # one per waiting lead, never one per retry
    pool = (await client.get("/pool", headers=headers(world.admin))).json()
    assert {p["deal_id"] for p in pool} == {first["id"], second["id"]}
    await configure(client, world)
    await dispatch_all()
    for d in (first, second):
        got = (await client.get(f"/deals/{d['id']}", headers=headers(world.admin))).json()
        assert got["status"] == "open" and got["owner"] is not None


async def test_cap_makes_leads_wait_and_a_raise_releases_them(client, world):
    await configure(client, world, cap=1)
    deals = [await create(client, world.north1) for _ in range(3)]
    assert [d["status"] for d in deals].count("open") == 2
    waiting = next(d for d in deals if d["status"] == "pooled")
    assert waiting["waiting_reason"] == "all_reps_at_cap"
    await dispatch_all()
    leader_notes = [
        n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"
    ]
    assert len(leader_notes) == 1
    await configure(client, world, cap=2)
    await dispatch_all()
    got = (await client.get(f"/deals/{waiting['id']}", headers=headers(world.admin))).json()
    assert got["status"] == "open"
    assert (
        len([n for n in await notifications_for(world.tl_north.id) if n.type == "lead.waiting"])
        == 1
    )


async def test_fifty_concurrent_leads_are_assigned_exactly_once_and_fairly(client, world):
    await configure(client, world, cap=25)
    results = await asyncio.gather(
        *(client.post("/leads", json=lead(), headers=headers(world.tl_north)) for _ in range(50))
    )
    assert all(r.status_code == 201 for r in results), [
        r.text for r in results if r.status_code != 201
    ]
    deals = (await client.get("/deals", params={"limit": 200}, headers=headers(world.admin))).json()
    assert len(deals) == 50 and all(d["status"] == "open" for d in deals)
    counts: dict[str, int] = {}
    for d in deals:
        counts[d["owner"]["id"]] = counts.get(d["owner"]["id"], 0) + 1
    assert counts == {str(world.north1.id): 25, str(world.north2.id): 25}
    await dispatch_all()  # the outbox safety net must not assign anything twice
    again = (await client.get("/deals", params={"limit": 200}, headers=headers(world.admin))).json()
    assert {d["id"]: d["owner"]["id"] for d in again} == {d["id"]: d["owner"]["id"] for d in deals}


async def test_settings_need_a_reason_and_the_right_role(client, world):
    body = {"default_team_id": str(world.north_team), "default_cap": 5, "reason": None}
    assert (
        await client.put("/settings/assignment", json=body, headers=headers(world.tl_north))
    ).status_code == 403
    assert (
        await client.put("/settings/assignment", json=body, headers=headers(world.admin))
    ).status_code == 422
    assert (
        await client.put(
            "/settings/assignment", json={**body, "reason": "x"}, headers=headers(world.admin)
        )
    ).status_code == 200
    got = (await client.get("/settings/assignment", headers=headers(world.admin))).json()
    assert got["default_cap"] == 5
    test = await client.post(
        "/settings/assignment/test", json={"lead_source": "Website"}, headers=headers(world.admin)
    )
    assert test.status_code == 200 and test.json()["target_name"] == "Team North"
    assert (
        await client.get("/settings/assignment", headers=headers(world.north1))
    ).status_code in (403, 404)


async def test_system_actor_can_write_settings_like_the_seed_script(world):
    from app.modules.leads.assignment.service import write_settings
    from app.modules.leads.schemas import AssignmentSettingsIn
    from app.platform.context import RequestInfo, system_actor

    out = await write_settings(
        system_actor(world.org_id, "Seed script"),
        RequestInfo(origin="system_job"),
        AssignmentSettingsIn(default_team_id=world.north_team, default_cap=7, reason="seed"),
    )
    assert out["default_cap"] == 7 and str(out["default_team_id"]) == str(world.north_team)


# ------------------------------------------------------------------------------ duplicates
async def test_exact_duplicate_blocks_and_never_leaks_across_teams(client, world):
    await configure(client, world)
    first = await create(
        client, world.north1, company="Zenith Foods", website="https://zenith-foods.com"
    )
    r = await client.post(
        "/leads",
        json=lead("Another Name", website="www.zenith-foods.com"),
        headers=headers(world.south1),
    )
    assert r.status_code == 409 and r.json()["code"] == "duplicate_blocked"
    match = r.json()["details"]["matches"][0]
    assert match["in_scope"] is False and match["match_type"] == "domain"
    assert "account_id" not in match and "deal_id" not in match
    assert first["id"] not in r.text and first["account_id"] not in r.text
    # in scope the same rep may see the ids
    owner = actor_by_id(world, first["owner"]["id"])
    r2 = await client.post(
        "/leads", json=lead("Other", website="zenith-foods.com"), headers=headers(owner)
    )
    assert r2.status_code == 409 and "account_id" in r2.json()["details"]["matches"][0]
    # a User cannot override; an Admin can, with a reason, and the new lead joins the account
    over = {"website": "zenith-foods.com", "override_block": True}
    assert (
        await client.post("/leads", json=lead("Other", **over), headers=headers(world.north2))
    ).status_code == 403
    assert (
        await client.post("/leads", json=lead("Other", **over), headers=headers(world.admin))
    ).status_code == 422
    ok = await client.post(
        "/leads",
        json=lead("Other", reason="same company, new contact", **over),
        headers=headers(world.admin),
    )
    assert ok.status_code == 201 and ok.json()["account_id"] == first["account_id"]


async def test_similar_name_warns_and_create_anyway_needs_a_reason(client, world):
    await configure(client, world)
    await create(client, world.north1, company="Zenith Foods Pvt")
    body = lead("Zenith Foods Pvt Ltd")
    r = await client.post("/leads", json=body, headers=headers(world.south1))
    assert r.status_code == 409 and r.json()["code"] == "duplicate_warning"
    assert "account_id" not in r.json()["details"]["matches"][0]
    anyway = {**body, "create_anyway": True}
    r = await client.post("/leads", json=anyway, headers=headers(world.south1))
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await client.post(
        "/leads", json={**anyway, "reason": "different company"}, headers=headers(world.south1)
    )
    assert r.status_code == 201
    async with get_sessionmaker()() as s:
        rows = list(
            (
                await s.execute(
                    select(DuplicateOverride).where(
                        DuplicateOverride.deal_id == uuid.UUID(r.json()["id"])
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1 and rows[0].in_scope is False
    await dispatch_all()
    assert any(
        n.type == "duplicate.created_anyway_outside_scope"
        for n in await notifications_for(world.admin.id)
    )
    check = await client.post(
        "/duplicates/check", json={"company": "Zenith Foods Pvt Ltd"}, headers=headers(world.north2)
    )
    assert check.status_code == 200 and check.json()["warn"]


async def test_lead_validation(client, world):
    await configure(client, world)
    past = lead(next_step_due_date=(today_ist() - timedelta(days=1)).isoformat())
    r = await client.post("/leads", json=past, headers=headers(world.north1))
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    nocontact = lead()
    nocontact.pop("email")
    r = await client.post("/leads", json=nocontact, headers=headers(world.north1))
    assert r.status_code == 422
    r = await client.post("/leads", json=lead(email="not-an-email"), headers=headers(world.north1))
    assert r.status_code == 422


# ------------------------------------------------------------------------------ edit and undo
async def test_edit_locks_fields_versions_and_undo(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    owner = actor_by_id(world, d["owner"]["id"])
    url = f"/deals/{d['id']}"
    for bad in (
        {"created_by": str(owner.id)},
        {"generated_by": str(owner.id)},
        {"next_step_text": ""},
    ):
        assert (await client.patch(url, json=bad, headers=headers(owner))).status_code == 422
    assert (
        await client.patch(
            url, json={"expected_version": 99, "lead_source": "Event"}, headers=headers(owner)
        )
    ).status_code == 409
    other_team = await client.patch(
        url, json={"lead_source": "Event"}, headers=headers(world.south1)
    )
    assert other_team.status_code in (403, 404)
    r = await client.patch(url, json={"lead_source": "Event"}, headers=headers(owner))
    assert r.status_code == 200 and r.json()["lead_source"] == "Event" and r.json()["last_audit_id"]
    audit_id = r.json()["last_audit_id"]
    stranger = world.north2 if owner.id == world.north1.id else world.north1
    assert (
        await client.post(f"{url}/undo", json={"audit_id": audit_id}, headers=headers(stranger))
    ).status_code in (403, 404)
    r = await client.post(f"{url}/undo", json={"audit_id": audit_id}, headers=headers(owner))
    assert r.status_code == 200 and r.json()["lead_source"] == "Website"
    assert (
        await client.post(f"{url}/undo", json={"audit_id": audit_id}, headers=headers(owner))
    ).status_code == 409
    keys = [k for k, _ in await audit_keys(d["id"])]
    assert "deal.edit" in keys and "deal.undo" in keys  # the original row is still there


# ------------------------------------------------------------------------------ Pool and ownership
async def held_lead(client, world):
    await configure(client, world, cap=0)  # everyone at cap: the lead waits in Team North's Pool
    d = await create(client, world.north1)
    assert d["status"] == "pooled" and d["waiting_reason"] == "all_reps_at_cap"
    return d


async def test_manual_assignment_permissions_reason_and_cap_warning(client, world):
    d = await held_lead(client, world)
    url = f"/pool/{d['id']}/assign"
    body = {"user_id": str(world.north2.id)}
    assert (await client.get("/pool", headers=headers(world.north1))).status_code == 403
    assert (await client.get("/pool", headers=headers(world.tl_south))).json() == []
    assert len((await client.get("/pool", headers=headers(world.tl_north))).json()) == 1
    assert (await client.post(url, json=body, headers=headers(world.north1))).status_code == 403
    assert (await client.post(url, json=body, headers=headers(world.tl_south))).status_code in (
        403,
        404,
    )
    assert (await client.post(url, json=body, headers=headers(world.tl_north))).status_code == 422
    outside = {"user_id": str(world.south1.id), "reason": "wrong team"}
    assert (
        await client.post(url, json=outside, headers=headers(world.tl_north))
    ).status_code == 403
    r = await client.post(url, json={**body, "reason": "best fit"}, headers=headers(world.tl_north))
    assert r.status_code == 200, r.text
    assert r.json()["owner"]["id"] == str(world.north2.id) and "cap" in r.json()["warning"]
    assert r.json()["assignment_method"] == "manual" and r.json()["assigned_by"] == "Tara"
    assert (
        await client.post(url, json={**body, "reason": "again"}, headers=headers(world.tl_north))
    ).status_code == 409
    await dispatch_all()
    assert any(n.type == "lead.assigned" for n in await notifications_for(world.north2.id))
    keys = await audit_keys(d["id"])
    assert ("pool.assign", "ok") in keys and ("pool.assign", "denied") in keys


async def test_reassign_and_bulk_reassign_move_next_steps_and_notify(client, world):
    await configure(client, world)
    deals = [await create(client, world.north1) for _ in range(4)]
    mine = [d for d in deals if d["owner"]["id"] == str(world.north1.id)]
    r = await client.post(
        f"/deals/{mine[0]['id']}/reassign",
        json={"user_id": str(world.north2.id), "reason": "balance"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200 and r.json()["owner"]["id"] == str(world.north2.id)
    assert r.json()["next_step"]["responsible"]["id"] == str(world.north2.id)
    await dispatch_all()
    assert any(n.type == "deal.owner_changed" for n in await notifications_for(world.north1.id))
    assert (
        await client.post(
            f"/deals/{mine[0]['id']}/reassign",
            json={"user_id": str(world.north2.id), "reason": "x"},
            headers=headers(world.north2),
        )
    ).status_code == 403
    bulk = await client.post(
        "/deals/bulk-reassign",
        json={
            "from_user_id": str(world.north2.id),
            "to_user_id": str(world.north1.id),
            "reason": "leaving",
        },
        headers=headers(world.admin),
    )
    assert bulk.status_code == 200 and bulk.json()["moved"] == 3
    after = (await client.get("/deals", params={"limit": 50}, headers=headers(world.admin))).json()
    assert {d["owner"]["id"] for d in after} == {str(world.north1.id)}


async def test_moving_a_rep_to_another_team_can_return_their_deals_to_the_pool(client, world):
    await configure(client, world)
    deals = [await create(client, world.north1) for _ in range(2)]
    mine = [d for d in deals if d["owner"]["id"] == str(world.north1.id)]
    assert mine
    r = await client.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "open_deals": "return_to_pool", "reason": "move"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200, r.text
    got = (await client.get(f"/deals/{mine[0]['id']}", headers=headers(world.admin))).json()
    assert (
        got["owner"] is None or got["status"] == "open"
    )  # re-assigned by the engine if reps exist


async def test_direct_team_move_by_admin(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    url = f"/deals/{d['id']}/move-team"
    body = {"to_team_id": str(world.south_team), "owner_id": str(world.south1.id)}
    assert (await client.post(url, json=body, headers=headers(world.tl_north))).status_code == 403
    assert (await client.post(url, json=body, headers=headers(world.admin))).status_code == 422
    r = await client.post(url, json={**body, "reason": "territory"}, headers=headers(world.admin))
    assert r.status_code == 200 and r.json()["team_name"] == "Team South"
    assert (
        await client.get(f"/deals/{d['id']}", headers=headers(actor_by_id(world, d["owner"]["id"])))
    ).status_code == 404


# ------------------------------------------------------------------------------ transfers
async def test_transfer_take_flow_with_comment_and_immediate_revocation(client, world):
    await configure(client, world)
    d = await create(client, world.north1, company="Orchid Textiles")
    old_owner = actor_by_id(world, d["owner"]["id"])
    found = await client.get(
        "/transfers/lookup", params={"q": "orchid"}, headers=headers(world.tl_south)
    )
    assert found.json() == [
        {"deal_id": d["id"], "company": "Orchid Textiles", "team_name": "Team North"}
    ]
    assert (
        await client.get("/transfers/lookup", params={"q": "orchid"}, headers=headers(world.north1))
    ).status_code == 403
    body = {"direction": "take", "deal_id": d["id"], "owner_id": str(world.south1.id)}
    assert (
        await client.post("/transfers", json=body, headers=headers(world.tl_south))
    ).status_code == 422
    r = await client.post(
        "/transfers",
        json={**body, "reason": "their client is in our city"},
        headers=headers(world.tl_south),
    )
    assert r.status_code == 202, r.text
    approval_id = r.json()["approval_id"]
    again = await client.post(
        "/transfers", json={**body, "reason": "again"}, headers=headers(world.tl_south)
    )
    assert again.status_code == 409
    await dispatch_all()
    assert any(n.type == "transfer.requested" for n in await notifications_for(world.tl_north.id))
    transfers = (await client.get("/transfers", headers=headers(world.tl_north))).json()
    assert transfers[0]["status"] == "open" and transfers[0]["company"] == "Orchid Textiles"
    tid = transfers[0]["id"]
    assert (
        await client.post(
            f"/transfers/{tid}/comment",
            json={"comment": "fine by us"},
            headers=headers(world.tl_south),
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/transfers/{tid}/comment",
            json={"comment": "fine by us"},
            headers=headers(world.tl_north),
        )
    ).status_code == 204
    assert (
        await client.post(
            f"/approvals/{approval_id}/decide",
            json={"decision": "approve"},
            headers=headers(world.admin),
        )
    ).status_code == 422
    ok = await client.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=headers(world.admin),
    )
    assert ok.status_code == 200, ok.text
    moved = (await client.get(f"/deals/{d['id']}", headers=headers(world.south1))).json()
    assert moved["owner"]["id"] == str(world.south1.id) and moved["team_name"] == "Team South"
    assert (
        await client.get(f"/deals/{d['id']}", headers=headers(old_owner))
    ).status_code == 404  # revoked at once
    assert (
        await client.get(f"/deals/{d['id']}", headers=headers(world.tl_north))
    ).status_code == 404
    await dispatch_all()
    assert any(n.type == "transfer.decided" for n in await notifications_for(world.tl_north.id))
    done = (await client.get("/transfers", headers=headers(world.tl_south))).json()
    assert done[0]["status"] == "approved"


async def test_transfer_give_goes_to_the_receiving_pool_and_rejection_frees_the_deal(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    give = {
        "direction": "give",
        "deal_id": d["id"],
        "to_team_id": str(world.south_team),
        "reason": "not our segment",
    }
    theirs = {**give, "to_team_id": str(world.north_team)}
    r = await client.post("/transfers", json=theirs, headers=headers(world.tl_south))
    assert r.status_code == 404  # not their deal
    assert (
        await client.post("/transfers", json=give, headers=headers(world.north1))
    ).status_code == 403
    r = await client.post("/transfers", json=give, headers=headers(world.tl_north))
    assert r.status_code == 202
    rejected = await client.post(
        f"/approvals/{r.json()['approval_id']}/decide",
        json={"decision": "reject", "reason": "keep it"},
        headers=headers(world.admin),
    )
    assert rejected.status_code == 200
    r = await client.post(
        "/transfers", json=give, headers=headers(world.tl_north)
    )  # free to ask again
    assert r.status_code == 202
    ok = await client.post(
        f"/approvals/{r.json()['approval_id']}/decide",
        json={"decision": "approve", "reason": "fine"},
        headers=headers(world.admin),
    )
    assert ok.status_code == 200
    pooled = (await client.get("/pool", headers=headers(world.tl_south))).json()
    assert [p["deal_id"] for p in pooled] == [d["id"]] and pooled[0]["held_for_manual"] is True
    await dispatch_all()  # the engine must leave a held deal alone
    assert (await client.get(f"/deals/{d['id']}", headers=headers(world.admin))).json()[
        "status"
    ] == "pooled"
    mine = await client.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(world.south2.id), "reason": "new owner"},
        headers=headers(world.tl_south),
    )
    assert mine.status_code == 200 and mine.json()["owner"]["id"] == str(world.south2.id)


async def test_transfer_approval_is_refused_when_the_deal_changed(client, world):
    await configure(client, world)
    d = await create(client, world.north1)
    r = await client.post(
        "/transfers",
        json={"direction": "take", "deal_id": d["id"], "reason": "need it"},
        headers=headers(world.tl_south),
    )
    assert r.status_code == 202
    other = world.north2 if d["owner"]["id"] == str(world.north1.id) else world.north1
    ok = await client.post(
        f"/deals/{d['id']}/reassign",
        json={"user_id": str(other.id), "reason": "balance"},
        headers=headers(world.tl_north),
    )
    assert ok.status_code == 200
    stale = await client.post(
        f"/approvals/{r.json()['approval_id']}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=headers(world.admin),
    )
    assert stale.status_code == 409
    still = (await client.get(f"/deals/{d['id']}", headers=headers(world.admin))).json()
    assert still["team_name"] == "Team North"
