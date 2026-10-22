# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 stage engine over HTTP: moves, exit criteria, proof, verification, overrides."""

import asyncio

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.platform.audit.models import AuditLog
from tests.helpers import dispatch_all, headers, notifications_for
from tests.modules.journey import (
    add_doc,
    ago,
    detail,
    due,
    log,
    make_file,
    make_sent_proposal,
    move,
    new_deal,
    patch,
    run_to,
    tick,
)
from tests.modules.test_leads_api import audit_keys


async def audit_rows(entity_id, key):
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == str(entity_id), AuditLog.action_key == key
                )
            )
        ).scalars()
        return [r for r in rows if r.outcome == "ok"]


# ------------------------------------------------------------------------------ the journey
async def test_a_deal_walks_from_lead_to_won_on_onboarding(client, world, admin_engine):
    deal, owner = await run_to(client, world, admin_engine, "won")
    d = await detail(client, owner, deal["id"])
    assert d["status"] == "won" and d["stage_key"] == "onboarding" and d["won_at"]
    assert d["value_paise"] == 600000
    visits = [v["stage_key"] for v in d["visits"]]
    assert visits == [
        "lead", "qualification", "meeting_scheduled", "prospecting", "proposal",
        "revision", "closure", "onboarding",
    ]  # fmt: skip
    assert all(c["ticked"] for c in d["progress"]["criteria"])
    await dispatch_all()
    for who in (owner, world.tl_north):
        assert any(n.type == "deal.won" for n in await notifications_for(who.id))
    # the deal left the board but its onboarding criteria were still tickable
    board = (await client.get("/board", headers=headers(owner))).json()
    assert deal["id"] not in {c["id"] for col in board["columns"] for c in col["cards"]}
    won = (await client.get("/stages/won/deals", headers=headers(owner))).json()
    assert [r["id"] for r in won["rows"]] == [deal["id"]]
    assert len(await audit_rows(deal["id"], "deal.move_forward")) == 7


async def test_only_the_owner_ticks_or_moves_forward(client, world):
    deal, owner = await new_deal(client, world)
    await log(client, owner, deal["id"], "call")
    for other in (world.tl_north, world.admin, world.sa):
        r = await client.post(
            f"/deals/{deal['id']}/criteria/contact_verified/tick", json={}, headers=headers(other)
        )
        assert r.status_code == 403, (other.role, r.text)
        r = await client.post(
            f"/deals/{deal['id']}/move", json={"to_stage": "qualification"}, headers=headers(other)
        )
        assert r.status_code == 403, (other.role, r.text)
    rows = await audit_keys(deal["id"])
    assert ("criterion.tick", "denied") in rows and ("deal.move_forward", "denied") in rows
    await tick(client, owner, deal["id"], "contact_verified")  # the owner can


async def test_tick_needs_proof_and_move_needs_every_criterion(client, world):
    deal, owner = await new_deal(client, world)
    r = await tick(client, owner, deal["id"], "contact_verified", expect=422)
    assert r.json()["code"] == "proof_required" and r.json()["details"]["missing"]
    r = await move(client, owner, deal["id"], "qualification", expect=422)
    body = r.json()
    assert body["code"] == "criteria_incomplete"
    assert {m["key"] for m in body["details"]["missing"]} == {
        "basic_info", "company_identified", "contact_verified", "fits_profile",
    }  # fmt: skip
    await log(client, owner, deal["id"], "call")
    await tick(client, owner, deal["id"], "basic_info")
    await tick(client, owner, deal["id"], "company_identified")
    await tick(client, owner, deal["id"], "contact_verified")
    await tick(client, owner, deal["id"], "contact_verified", expect=409)  # already ticked
    await tick(client, owner, deal["id"], "no_such_key", expect=404)
    await tick(client, owner, deal["id"], "fits_profile", expect=422)  # no fit questions answered
    fit = (await client.get("/settings/fit-checklist", headers=headers(world.admin))).json()
    await patch(client, owner, deal["id"], fit_checklist={f["id"]: True for f in fit})
    await tick(client, owner, deal["id"], "fits_profile")
    await move(client, owner, deal["id"], "qualification")
    # one stage at a time: no jumping without an override
    r = await move(client, owner, deal["id"], "prospecting", expect=403)


async def test_untick_needs_a_reason_and_removes_the_tick(client, world):
    deal, owner = await new_deal(client, world)
    await log(client, owner, deal["id"], "call")
    await tick(client, owner, deal["id"], "basic_info")
    r = await client.post(
        f"/deals/{deal['id']}/criteria/basic_info/untick", json={}, headers=headers(owner)
    )
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await client.post(
        f"/deals/{deal['id']}/criteria/basic_info/untick",
        json={"reason": "typo"},
        headers=headers(owner),
    )
    assert r.status_code == 204
    d = await detail(client, owner, deal["id"])
    assert not next(c for c in d["progress"]["criteria"] if c["key"] == "basic_info")["ticked"]
    assert len(await audit_rows(deal["id"], "criterion.untick")) == 1


# ------------------------------------------------------------------------------ revisions
async def test_revision_is_mandatory_and_round_two_needs_a_reason(client, world, admin_engine):
    deal, owner = await run_to(client, world, admin_engine, "proposal")
    did = deal["id"]
    r = await move(client, owner, did, "closure", expect=403)  # no skipping Revision
    await log(
        client,
        owner,
        did,
        "meeting",
        occurred_at=ago(1),
        attendees=["Asha"],
        discussed="Walkthrough",
    )
    r = await client.post(
        f"/deals/{did}/proposal-rounds", json={"feedback": "accepted"}, headers=headers(owner)
    )
    assert r.status_code == 201, r.text
    await make_sent_proposal(admin_engine, world, did, owner)
    await patch(client, owner, did, decision_date=due(10))
    for key in ("walkthrough_logged", "feedback_recorded", "proposal_emailed", "decision_date_set"):
        await tick(client, owner, did, key)
    await move(client, owner, did, "revision")
    r = await move(client, owner, did, "revision_2", expect=422)
    assert r.json()["code"] == "reason_required"
    r = await move(client, owner, did, "revision_2", reason="Customer wants new SLA terms")
    d = await detail(client, owner, did)
    assert d["stage_key"] == "revision_2" and d["revision_round"] == 2
    await move(client, owner, did, "revision_2", reason="Third round of legal edits")
    d = await detail(client, owner, did)
    assert d["stage_key"] == "revision_2" and d["revision_round"] == 3
    col = next(
        c
        for c in (await client.get("/board", headers=headers(owner))).json()["columns"]
        if c["key"] == "revision"
    )
    assert col["cards"][0]["revision_round"] == 3  # R1 and R2+ share one column
    # moving on from Revision 2 goes to Closure once its criteria are ticked
    fid = await make_file(admin_engine, world, did, owner)
    await patch(client, owner, did, final_price_paise=1, scope="s", agreed_by="a")
    await add_doc(client, owner, did, "agreement", "sent", fid)
    await tick(client, owner, did, "terms_accepted")
    await tick(client, owner, did, "agreement_sent")
    await move(client, owner, did, "closure")


# ------------------------------------------------------------------------------ override and skip
async def test_override_rules_by_role(client, world):
    deal, owner = await new_deal(client, world)  # a North deal owned by a North rep
    did = deal["id"]
    body = {"to_stage": "qualification", "override": True}
    r = await client.post(f"/deals/{did}/move", json=body, headers=headers(owner))
    assert r.status_code == 403  # a rep never overrides
    r = await client.post(f"/deals/{did}/move", json=body, headers=headers(world.tl_south))
    assert r.status_code in (403, 404)  # another team's TL
    r = await client.post(f"/deals/{did}/move", json=body, headers=headers(world.tl_north))
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await client.post(
        f"/deals/{did}/move",
        json={**body, "reason": "Rep is on leave"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200 and r.json()["kind"] == "override"
    assert set(r.json()["overridden"]) == {
        "basic_info",
        "company_identified",
        "contact_verified",
        "fits_profile",
    }
    d = await detail(client, owner, did)
    assert d["stage_key"] == "qualification"
    await dispatch_all()
    assert any(n.type == "deal.overridden" for n in await notifications_for(owner.id))
    # a skip over several stages needs the same rights and a reason
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "prospecting", "override": True, "reason": "Already met them"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200 and r.json()["kind"] == "skip"


async def test_a_team_leader_cannot_override_their_own_deal(client, world, admin_engine):
    from tests.modules.test_leads_api import configure

    await configure(client, world)
    # make the TL the owner of a deal: Admin moves it to them directly
    d = (
        await client.post(
            "/leads",
            json=__import__("tests.modules.test_leads_api", fromlist=["lead"]).lead(),
            headers=headers(world.north1),
        )
    ).json()
    from tests.helpers import settle_rep_lead

    d = await settle_rep_lead(client, d)  # a rep's lead waits for the leader; assign it first
    r = await client.post(
        f"/deals/{d['id']}/reassign",
        json={"user_id": str(world.tl_north.id), "reason": "tl takes it"},
        headers=headers(world.admin),
    )
    if r.status_code != 200:  # reassign only targets reps; set the owner in the database instead
        from sqlalchemy import text

        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE deals SET owner_id=:u WHERE id=:d"),
                {"u": world.tl_north.id, "d": d["id"]},
            )
    r = await client.post(
        f"/deals/{d['id']}/move",
        json={"to_stage": "qualification", "override": True, "reason": "x"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 403, r.text
    r = await client.post(
        f"/deals/{d['id']}/move",
        json={"to_stage": "qualification", "override": True, "reason": "ok"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200


async def test_backward_move_needs_a_reason_and_gets_a_fresh_checklist(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    await move(
        client, world.tl_north, did, "qualification", override=True, reason="start", expect=200
    )
    r = await move(client, owner, did, "lead", expect=422)
    assert r.json()["code"] == "reason_required"
    await move(client, owner, did, "lead", reason="Wrong company")
    d = await detail(client, owner, did)
    assert d["stage_key"] == "lead" and not any(c["ticked"] for c in d["progress"]["criteria"])
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "nope"}, headers=headers(owner))
    assert r.status_code == 422


# ------------------------------------------------------------------------------ verification
async def test_verify_and_reject_with_review_needed(client, world, admin_engine):
    deal, owner = await run_to(client, world, admin_engine, "qualification")
    did = deal["id"]
    d = await detail(client, owner, did)
    lead_entry = d["visits"][0]
    # TL of another team cannot verify; own TL can
    r = await client.post(
        f"/deals/{did}/criteria/basic_info/verify",
        json={"decision": "verify", "entry_id": None},
        headers=headers(world.tl_south),
    )
    assert r.status_code in (403, 404)
    r = await client.post(
        f"/deals/{did}/criteria/basic_info/verify",
        json={"decision": "verify"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 204
    # a rejection needs a reason; on a stage the deal already left it flags the deal for review
    r = await client.post(
        f"/deals/{did}/criteria/contact_verified/verify",
        json={"decision": "reject"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 422
    r = await client.post(
        f"/deals/{did}/criteria/contact_verified/verify",
        json={"decision": "reject", "reason": "Call was voicemail"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 204, r.text
    d = await detail(client, owner, did)
    assert d["flags"].get("review_needed") is True
    await dispatch_all()
    for who in (owner, world.tl_north):
        assert any(n.type == "deal.review_needed" for n in await notifications_for(who.id))
    assert any(n.type == "criterion.rejected" for n in await notifications_for(owner.id))
    assert lead_entry["stage_key"] == "lead"
    r = await client.post(
        f"/deals/{did}/review-done",
        json={"reason": "spoke to rep"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 204
    assert not (await detail(client, owner, did))["flags"].get("review_needed")


# ------------------------------------------------------------------------------ proof missing
async def test_deleting_the_proof_marks_the_tick_and_new_proof_clears_it(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    act = await log(client, owner, did, "call")
    await tick(client, owner, did, "contact_verified")
    r = await client.delete(
        f"/activities/{act['id']}", params={"reason": "logged by mistake"}, headers=headers(owner)
    )
    assert r.status_code == 403  # a rep cannot delete; a Team Leader can
    r = await client.delete(
        f"/activities/{act['id']}",
        params={"reason": "logged by mistake"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 204, r.text
    d = await detail(client, owner, did)
    crit = next(c for c in d["progress"]["criteria"] if c["key"] == "contact_verified")
    assert crit["ticked"] and crit["proof_missing"] and d["flags"].get("proof_missing")
    r = await move(client, owner, did, "qualification", expect=422)
    assert any(m["reason"] == "proof missing" for m in r.json()["details"]["missing"])
    await dispatch_all()
    for who in (owner, world.tl_north):
        assert any(n.type == "deal.proof_missing" for n in await notifications_for(who.id))
    await log(client, owner, did, "email")  # proof is back: the warning clears by itself
    d = await detail(client, owner, did)
    assert not next(c for c in d["progress"]["criteria"] if c["key"] == "contact_verified")[
        "proof_missing"
    ]
    assert not d["flags"].get("proof_missing")


async def test_emptying_a_proof_field_flags_it(client, world, admin_engine):
    deal, owner = await run_to(client, world, admin_engine, "qualification")
    did = deal["id"]
    await patch(client, owner, did, pain_point="Missed calls")
    await tick(client, owner, did, "need_documented")
    await patch(client, owner, did, pain_point=None)
    d = await detail(client, owner, did)
    assert next(c for c in d["progress"]["criteria"] if c["key"] == "need_documented")[
        "proof_missing"
    ]
    await patch(client, owner, did, pain_point="Back again")
    d = await detail(client, owner, did)
    assert not next(c for c in d["progress"]["criteria"] if c["key"] == "need_documented")[
        "proof_missing"
    ]


# ------------------------------------------------------------------------------ concurrency
async def test_a_tick_racing_a_rejection_ends_consistent(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    await log(client, owner, did, "call")
    await tick(client, owner, did, "basic_info")
    for _ in range(3):
        r = await asyncio.gather(
            client.post(
                f"/deals/{did}/criteria/basic_info/verify",
                json={"decision": "reject", "reason": "no"},
                headers=headers(world.tl_north),
            ),
            client.post(f"/deals/{did}/criteria/basic_info/tick", json={}, headers=headers(owner)),
        )
        assert all(x.status_code in (204, 404, 409) for x in r), [x.text for x in r]
        d = await detail(client, owner, did)
        n = [c for c in d["progress"]["criteria"] if c["key"] == "basic_info" and c["ticked"]]
        assert len(n) <= 1
        if not n:
            await tick(client, owner, did, "basic_info")


async def test_two_simultaneous_moves_one_wins(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    await log(client, owner, did, "call")
    fit = (await client.get("/settings/fit-checklist", headers=headers(world.admin))).json()
    await patch(client, owner, did, fit_checklist={f["id"]: True for f in fit})
    for k in ("basic_info", "company_identified", "contact_verified", "fits_profile"):
        await tick(client, owner, did, k)
    r = await asyncio.gather(
        *(
            client.post(
                f"/deals/{did}/move",
                json={"to_stage": "qualification", "expected_stage": "lead"},
                headers=headers(owner),
            )
            for _ in range(2)
        )
    )
    assert sorted(x.status_code for x in r) == [200, 409], [x.text for x in r]
    assert (await detail(client, owner, did))["stage_key"] == "qualification"
