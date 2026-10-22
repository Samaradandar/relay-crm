# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""QA plan acceptance tests, one section per item of the release test plan (15 Oct 2026):
roles and permissions, pipeline logic, Lead Pool and assignment, data integrity, soft delete,
API status codes and shapes, and one end-to-end journey. Everything goes through the real HTTP
API with real tokens, the way the web app (or someone calling the API directly) would."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import text

from app.core.config import get_settings
from tests.helpers import headers
from tests.modules.journey import (
    add_doc,
    detail,
    due,
    log,
    make_file,
    move,
    new_deal,
    patch,
    run_to,
    tick,
)
from tests.modules.test_leads_api import configure, create, lead

ERROR_KEYS = {"code", "message", "details"}


def stage_keys(d):
    return [s["key"] for s in d["progress"]["stages"]]


# ============================================================ 1. roles and permissions
async def test_rep_cannot_see_or_edit_another_reps_deal_even_by_direct_api_calls(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    other = world.north2 if owner.id == world.north1.id else world.north1  # same team, not owner
    for actor in (other, world.south1):
        h = headers(actor)
        reads = [
            await client.get(f"/deals/{did}", headers=h),
            await client.get(f"/deals/{did}/activities", headers=h),
            await client.get(f"/history/deal/{did}", headers=h),
        ]
        writes = [
            await client.patch(f"/deals/{did}", json={"pain_point": "x"}, headers=h),
            await client.post(f"/deals/{did}/criteria/basic_info/tick", json={}, headers=h),
            await client.post(f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=h),
            await client.post(f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=h),
            await client.post(f"/deals/{did}/delete", json={"reason": "x"}, headers=h),
        ]
        for r in reads + writes:
            assert r.status_code in (403, 404), (actor.name, r.request.url, r.status_code)
            assert deal["company"] not in r.text  # nothing about the deal leaks
            assert ERROR_KEYS <= set(r.json())
        listed = (await client.get("/deals", headers=h)).json()
        assert did not in {d["id"] for d in listed}
    # the owner, their Team Leader, Admin and Super admin do see it
    for actor in (owner, world.tl_north, world.admin, world.sa):
        assert (await client.get(f"/deals/{did}", headers=headers(actor))).status_code == 200
    # the other team's leader does not
    assert (await client.get(f"/deals/{did}", headers=headers(world.tl_south))).status_code == 404


async def test_rep_cannot_use_admin_endpoints(client, world):
    h = headers(world.north1)
    calls = [
        client.put("/settings/assignment", json={"default_team_id": str(world.north_team),
                                                  "default_cap": 5, "reason": "x"}, headers=h),
        client.get("/admin/failed-jobs", headers=h),
        client.get("/audit", headers=h),
        client.put("/products", json={"products": [], "reason": "x"}, headers=h),
        client.get("/pipelines/new_business/versions", headers=h),
        client.patch(f"/users/{world.north2.id}", json={"is_active": False, "reason": "x"}, headers=h),
    ]  # fmt: skip
    for r in await asyncio.gather(*calls):
        assert r.status_code in (403, 404), (r.request.method, r.request.url, r.status_code)
    # and the Admin may use them
    assert (await client.get("/audit", headers=headers(world.admin))).status_code == 200
    assert (await client.get("/admin/failed-jobs", headers=headers(world.sa))).status_code == 200


# ============================================================ 7 + 10. API auth: 401s, expiry
async def test_missing_bad_and_expired_tokens_get_401_in_the_error_envelope(client, world):
    secret = get_settings().dev_jwt_secret
    now = datetime.now(UTC)

    def tok(**claims):
        base = {"sub": f"dev:{world.north1.email}", "email": world.north1.email, "iat": now}
        return jwt.encode({**base, **claims}, secret, algorithm="HS256")

    cases = {
        "none": {},
        "garbage": {"Authorization": "Bearer not-a-jwt"},
        "expired": {"Authorization": "Bearer " + tok(exp=now - timedelta(minutes=1))},
        "no exp": {"Authorization": "Bearer " + tok()},
        "wrong key": {"Authorization": "Bearer " + jwt.encode(
            {"sub": "dev:x", "email": world.north1.email, "iat": now, "exp": now + timedelta(hours=1)},
            "another-secret-that-is-long-enough-32b", algorithm="HS256")},
    }  # fmt: skip
    for name, h in cases.items():
        for path in ("/me", "/deals", "/pool"):
            r = await client.get(path, headers=h)
            assert r.status_code == 401, (name, path, r.status_code)
            assert ERROR_KEYS <= set(r.json()) and r.json()["code"] == "unauthenticated"
    # a valid token works
    good = {"Authorization": "Bearer " + tok(exp=now + timedelta(hours=1))}
    assert (await client.get("/me", headers=good)).status_code == 200


async def test_response_shapes_of_the_main_endpoints(client, world):
    deal, owner = await new_deal(client, world)
    h = headers(owner)
    d = await detail(client, owner, deal["id"])
    for key in (
        "id",
        "company",
        "status",
        "stage_key",
        "owner",
        "next_step",
        "progress",
        "contacts",
    ):
        assert key in d, key
    assert {"stages", "current", "criteria"} <= set(d["progress"])
    row = next(x for x in (await client.get("/deals", headers=h)).json() if x["id"] == deal["id"])
    assert {"id", "company", "status", "stage_key", "owner", "value_paise"} <= set(row)
    me = (await client.get("/me", headers=h)).json()
    assert {"id", "email", "role"} <= set(me)
    bad = await client.post("/leads", json={"company": "x"}, headers=h)  # missing fields
    assert bad.status_code == 422 and bad.json()["code"] == "validation_error"
    assert bad.json()["details"]["errors"]  # which fields failed
    missing = await client.get(f"/deals/{uuid.uuid4()}", headers=h)
    assert missing.status_code == 404 and ERROR_KEYS <= set(missing.json())


# ============================================================ 2. pipeline logic
async def test_nine_stages_and_only_valid_transitions(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    d = await detail(client, owner, did)
    assert stage_keys(d) == ["lead", "qualification", "meeting_scheduled", "prospecting",
                             "proposal", "revision", "revision_2", "closure", "onboarding"]  # fmt: skip
    # skipping ahead is refused (only an override may skip, and a rep cannot override); so is
    # leaving with unticked criteria; an unknown stage too
    r = await move(client, owner, did, "prospecting", expect=403)
    assert r.json()["details"]["key"] == "deal.override_or_skip"
    r = await move(client, owner, did, "qualification", expect=422)
    assert r.json()["code"] == "criteria_incomplete"
    bad = await client.post(
        f"/deals/{did}/move", json={"to_stage": "no_such_stage"}, headers=headers(owner)
    )
    assert bad.status_code in (404, 422), bad.status_code
    assert (await detail(client, owner, did))["stage_key"] == "lead"


async def test_exit_criteria_are_owner_only_and_admin_verifies_and_overrides(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    for actor in (world.tl_north, world.admin):  # supervisors cannot tick for the owner
        r = await client.post(
            f"/deals/{did}/criteria/basic_info/tick", json={}, headers=headers(actor)
        )
        assert r.status_code == 403, (actor.name, r.status_code)
    await tick(client, owner, did, "basic_info")
    # the owner cannot verify their own tick; the Admin can
    r = await client.post(f"/deals/{did}/criteria/basic_info/verify", json={"decision": "verify"},
                          headers=headers(owner))  # fmt: skip
    assert r.status_code == 403
    r = await client.post(f"/deals/{did}/criteria/basic_info/verify", json={"decision": "verify"},
                          headers=headers(world.admin))  # fmt: skip
    assert r.status_code == 422 and r.json()["code"] == "reason_required"  # an Admin gives a reason
    r = await client.post(f"/deals/{did}/criteria/basic_info/verify",
                          json={"decision": "verify", "reason": "QA checked"}, headers=headers(world.admin))  # fmt: skip
    assert r.status_code == 204
    c = next(
        x
        for x in (await detail(client, owner, did))["progress"]["criteria"]
        if x["key"] == "basic_info"
    )
    assert c["status"] == "verified" and c["verified_by"]
    # a rep cannot override; the Admin can, with a reason, and it is recorded
    await move(client, owner, did, "qualification", expect=403, override=True, reason="please")
    r = await move(
        client, world.admin, did, "qualification", override=True, reason="QA: approved by phone"
    )
    assert r.json()["kind"] == "override" and "contact_verified" in r.json()["overridden"]
    assert (await detail(client, owner, did))["stage_key"] == "qualification"


async def test_won_fires_on_entering_onboarding(client, world, admin_engine):
    deal, owner = await run_to(client, world, admin_engine, "closure")
    did = deal["id"]
    before = await detail(client, owner, did)
    assert before["status"] == "open" and before["won_at"] is None
    fid = await make_file(admin_engine, world, did, owner)
    await add_doc(client, owner, did, "agreement", "countersigned", fid)
    await patch(client, owner, did, billing_contact="accounts@acme.in", subscription_start_date=due(15),
                contract_term="12 months", billing_frequency="monthly", renewal_date=due(380),
                advance_amount_paise=50000, advance_received_by="Neha Kapoor", advance_received_contact="neha@acme.in", advance_received_on=due(0))  # fmt: skip
    await add_doc(client, owner, did, "advance", "received", await make_file(admin_engine, world, did, owner))
    for key in ("agreement_countersigned", "advance_received", "onboarding_info"):
        await tick(client, owner, did, key)
    r = await move(client, owner, did, "onboarding")
    assert r.json()["won"] is True
    after = await detail(client, owner, did)
    assert after["status"] == "won" and after["won_at"] and after["probability_pct"] == 100


# ============================================================ 3. Lead Pool and assignment
async def _settings(client, world, *, rules=(), caps=None, cap=25):
    r = await client.put("/settings/assignment", json={
        "default_team_id": str(world.north_team), "default_cap": cap, "rules": list(rules),
        "groups": [], "caps": {str(k): v for k, v in (caps or {}).items()}, "reason": "QA",
    }, headers=headers(world.admin))  # fmt: skip
    assert r.status_code == 200, r.text


async def _admin_lead(client, world, **kw):
    r = await client.post("/leads", json=lead(reason="QA", **kw), headers=headers(world.admin))
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_matching_rule_wins_then_round_robin_takes_the_rest(client, world):
    rule = {"name": "Referrals to South 1", "priority": 1, "match": {"lead_source": "Referral"},
            "target_type": "user", "target_id": str(world.south1.id)}  # fmt: skip
    await _settings(client, world, rules=[rule])
    by_rule = await _admin_lead(client, world, lead_source="Referral")
    assert by_rule["owner"]["id"] == str(world.south1.id) and by_rule["assignment_method"] == "rule"
    rr = [await _admin_lead(client, world) for _ in range(4)]
    assert {d["assignment_method"] for d in rr} == {"round_robin"}
    owners = [d["owner"]["id"] for d in rr]
    assert set(owners) == {str(world.north1.id), str(world.north2.id)}  # fair: 2 and 2
    assert owners.count(str(world.north1.id)) == 2


async def test_per_rep_cap_then_everyone_at_cap_waits_in_the_pool(client, world):
    await _settings(client, world, caps={world.north1.id: 0, world.north2.id: 1})
    first = await _admin_lead(client, world)
    assert first["owner"]["id"] == str(world.north2.id)  # North 1 is at cap (0)
    second = await _admin_lead(client, world)  # now both are at cap
    assert second["owner"] is None and second["status"] == "pooled"
    assert second["waiting_reason"] == "all_reps_at_cap"
    pool = (await client.get("/pool", headers=headers(world.admin))).json()
    assert second["id"] in {p["deal_id"] for p in pool}


async def test_a_deactivated_rep_gets_no_new_leads(client, world):
    await _settings(client, world)
    r = await client.patch(f"/users/{world.north1.id}", json={"is_active": False, "reason": "QA left"},
                           headers=headers(world.admin))  # fmt: skip
    assert r.status_code == 200
    owners = {(await _admin_lead(client, world))["owner"]["id"] for _ in range(3)}
    assert owners == {str(world.north2.id)}


async def test_concurrent_leads_never_push_a_rep_over_the_cap(client, world, admin_engine):
    await _settings(client, world, caps={world.north1.id: 2, world.north2.id: 2})
    made = await asyncio.gather(*[_admin_lead(client, world) for _ in range(10)])
    owned = [d for d in made if d["owner"]]
    assert len(owned) == 4  # exactly the free capacity, no more
    async with admin_engine.connect() as c:
        counts = dict((await c.execute(text(
            "SELECT owner_id::text, count(*) FROM deals WHERE status='open' AND owner_id = ANY(:ids) "
            "GROUP BY 1"), {"ids": [world.north1.id, world.north2.id]})).all())  # fmt: skip
    assert counts == {str(world.north1.id): 2, str(world.north2.id): 2}
    assert sum(1 for d in made if d["status"] == "pooled") == 6


# ============================================================ 4. data integrity
async def test_duplicate_leads_are_blocked_and_required_fields_enforced(client, world):
    await configure(client, world)
    site = f"https://{uuid.uuid4().hex[:8]}-dup.in"
    await create(client, world.north1, website=site)
    again = await client.post("/leads", json=lead(website=site), headers=headers(world.north2))
    assert again.status_code == 409 and again.json()["code"] == "duplicate_blocked"
    for broken in ({"company": ""}, {"contact_name": ""}, {"lead_source": "Carrier pigeon"},
                   {"email": None, "phone": None}):  # fmt: skip
        r = await client.post("/leads", json={**lead(), **broken}, headers=headers(world.north1))
        assert r.status_code == 422, (broken, r.status_code, r.text)


async def test_activity_history_is_never_lost_on_edit_or_delete(client, world, admin_engine):
    deal, owner = await new_deal(client, world)
    a = await log(client, owner, deal["id"], discussed="First version")
    h = headers(owner)
    r = await client.patch(
        f"/activities/{a['id']}", json={"discussed": "Second version", "reason": "typo"}, headers=h
    )
    assert r.status_code == 200, r.text
    versions = (await client.get(f"/activities/{a['id']}/versions", headers=h)).json()
    assert "First version" in str(versions)  # the old text is kept
    # a rep cannot delete history; their Team Leader can (soft delete, with a reason)
    r = await client.delete(f"/activities/{a['id']}", params={"reason": "x"}, headers=h)
    assert r.status_code == 403
    r = await client.delete(f"/activities/{a['id']}", params={"reason": "logged twice"},
                            headers=headers(world.tl_north))  # fmt: skip
    assert r.status_code == 204, r.text
    async with admin_engine.connect() as c:
        row = (await c.execute(text("SELECT deleted_at, deleted_reason FROM activities WHERE id=:i"),
                               {"i": a["id"]})).one()  # fmt: skip
        audit = set((await c.execute(text(
            "SELECT action_key FROM audit_log WHERE entity_type='deal' AND entity_id=:d "
            "AND action_key LIKE 'activity.%'"), {"d": deal["id"]})).scalars())  # fmt: skip
    assert row[0] is not None and row[1] == "logged twice"  # soft-deleted, not erased
    assert {"activity.log", "activity.edit_own", "activity.delete"} <= audit  # each step recorded


# ============================================================ 5. soft delete and restore
async def test_deleted_deal_is_hidden_kept_and_restorable(client, world, admin_engine):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    await log(client, owner, did)
    # the owner (a rep) cannot delete; the Team Leader can, with a reason
    assert (
        await client.post(f"/deals/{did}/delete", json={"reason": "x"}, headers=headers(owner))
    ).status_code == 403
    r = await client.post(
        f"/deals/{did}/delete", json={"reason": "QA duplicate"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 204, r.text
    # hidden from every page
    assert (await client.get(f"/deals/{did}", headers=headers(owner))).status_code == 404
    assert did not in {
        d["id"] for d in (await client.get("/deals", headers=headers(world.admin))).json()
    }
    gone = (await client.get("/deleted/deals", headers=headers(world.tl_north))).json()
    assert did in {d["id"] for d in gone}
    # kept: the row, its activities and its audit trail are all still in the database
    async with admin_engine.connect() as c:
        kept = (await c.execute(text("SELECT deleted_at IS NOT NULL, (SELECT count(*) FROM activities "
                                     "WHERE deal_id=:d) FROM deals WHERE id=:d"), {"d": did})).one()  # fmt: skip
    assert kept[0] is True and kept[1] >= 1
    # restorable, back exactly where it was
    r = await client.post(
        f"/deals/{did}/restore", json={"reason": "QA restore"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 200, r.text
    back = await detail(client, owner, did)
    assert back["stage_key"] == "lead" and back["owner"]["id"] == str(owner.id)
    assert len((await client.get(f"/deals/{did}/activities", headers=headers(owner))).json()) >= 1


# ============================================================ 8. end to end
async def test_end_to_end_create_assign_stages_activity_onboarding(client, world, admin_engine):
    """Create a lead (as a rep) -> the Team Leader assigns it -> every stage with real proof and
    logged activities -> Onboarding: Won, with the whole trail in the deal's history."""
    deal, owner = await run_to(client, world, admin_engine, "onboarding_done")
    d = await detail(client, owner, deal["id"])
    assert d["stage_key"] == "onboarding" and d["status"] == "won"
    assert all(c["ticked"] for c in d["progress"]["criteria"])
    acts = (await client.get(f"/deals/{deal['id']}/activities", headers=headers(owner))).json()
    assert {"call", "meeting", "kickoff"} <= {a["type"] for a in acts}
    hist = (await client.get(f"/history/deal/{deal['id']}", headers=headers(world.admin),
                             params={"limit": 200})).json()  # fmt: skip
    actions = {h["action"] for h in hist}
    assert {"lead.create", "deal.move"} & actions and len(hist) >= 20
    docs = (await client.get(f"/deals/{deal['id']}/documents", headers=headers(owner))).json()
    assert {x["kind"] for x in docs} >= {"proposal", "agreement", "invoice"}
    _ = add_doc  # (documents above were attached by run_to through the same API)


# ============================================================ 17. bulk CSV import
async def test_csv_import_finishes_with_bad_rows_and_never_creates_twice(client, world):
    """A file with good, duplicate and invalid rows must finish (a skipped row used to be read
    again forever, so the run never returned) and importing the same file again adds nothing."""
    await configure(client, world)
    tag = uuid.uuid4().hex[:6]
    rows = [
        f"Bakery {tag},https://bakery-{tag}.in,Lata Joshi,lata@bakery-{tag}.in,Website",
        f"Clinic {tag},https://clinic-{tag}.in,Arun Pillai,arun@clinic-{tag}.in,Referral",
        f"Bakery {tag},https://bakery-{tag}.in,Lata Joshi,lata@bakery-{tag}.in,Website",  # dup
        f"Ghost {tag},https://ghost-{tag}.in,,,Website",  # no contact at all: invalid
    ]
    body = "Company,Website,Contact,Email,Source\n" + "\n".join(rows) + "\n"
    mapping = {"company": "Company", "website": "Website", "contact_name": "Contact",
               "email": "Email", "lead_source": "Source"}  # fmt: skip
    h = headers(world.admin)

    async def run_once():
        up = await client.post(
            "/imports", files={"file": ("q.csv", body.encode(), "text/csv")}, headers=h
        )
        assert up.status_code == 201, up.text
        iid = up.json()["id"]
        prev = await client.put(f"/imports/{iid}/mapping", json={"mapping": mapping}, headers=h)
        assert prev.status_code == 200, prev.text
        run = await asyncio.wait_for(
            client.post(f"/imports/{iid}/run", json={"reason": "QA import"}, headers=h), timeout=60
        )
        assert run.status_code == 200, run.text
        assert (await client.get(f"/imports/{iid}", headers=h)).json()["status"] == "done"
        return prev.json()["counts"], run.json()

    counts, result = await run_once()
    assert counts["ready"] == 2 and counts["duplicate"] == 1 and counts["invalid"] == 1
    assert result["created"] == 2
    _, again = await run_once()
    assert again["created"] == 0  # the same file a second time creates no new leads
    companies = [d["company"] for d in (await client.get("/deals", headers=h)).json()]
    assert sorted(c for c in companies if c.endswith(tag)) == [f"Bakery {tag}", f"Clinic {tag}"]


# ============================================================ contact type: influencer / decision maker
async def test_contact_type_defaults_to_influencer_and_can_change_at_any_stage(client, world):
    """A lead's contact is an influencer unless the form says decision maker; the type shows
    on the deal and the deal list, can be changed at any time by someone who may edit the deal
    (and not by a rep who does not own it), and every change is in the history."""
    plain, owner = await new_deal(client, world)
    d = await detail(client, owner, plain["id"])
    assert d["contacts"][0]["is_decision_maker"] is False  # default: influencer
    assert plain["contact"]["is_decision_maker"] is False

    marked, owner2 = await new_deal(client, world, contact_is_decision_maker=True)
    assert marked["contact"]["is_decision_maker"] is True
    d2 = await detail(client, owner2, marked["id"])
    cid = d2["contacts"][0]["id"]
    assert d2["contacts"][0]["is_decision_maker"] is True
    row = next(
        x
        for x in (await client.get("/deals", headers=headers(owner2))).json()
        if x["id"] == marked["id"]
    )
    assert row["contact"]["is_decision_maker"] is True  # the list (board, stage pages) knows too

    # change it later: owner, then back; a rep who does not own the deal cannot
    stranger = world.south1
    r = await client.patch(
        f"/contacts/{cid}", json={"is_decision_maker": False}, headers=headers(stranger)
    )
    assert r.status_code in (403, 404)
    for value in (False, True):
        r = await client.patch(
            f"/contacts/{cid}", json={"is_decision_maker": value}, headers=headers(owner2)
        )
        assert r.status_code == 200, r.text
        assert (await detail(client, owner2, marked["id"]))["contacts"][0][
            "is_decision_maker"
        ] is value
    # the Team Leader (supervisor) may change it too, and the whole trail is recorded
    r = await client.patch(
        f"/contacts/{cid}", json={"is_decision_maker": False}, headers=headers(world.tl_north)
    )
    assert r.status_code == 200, r.text
    resp = await client.get("/audit?entity_type=contact&limit=100", headers=headers(world.admin))
    assert resp.status_code == 200, resp.text
    flips = [
        h for h in resp.json()
        if h["entity_id"] == cid and h["old"] and h["new"]
        and h["old"].get("is_decision_maker") != h["new"].get("is_decision_maker")
    ]  # fmt: skip
    assert len(flips) == 3
