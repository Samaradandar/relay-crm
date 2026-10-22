# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 3: exit criteria, proof of every type, tick / untick / verify /
reject, and "Proof missing" by every route (PRD 10)."""

from datetime import UTC, datetime, timedelta

from tests.deep.phase3.p3 import (
    H,
    Rig,
    ago,
    audit_rows,
    due,
    events_after,
    in_days,
    max_event,
    max_seq,
    one,
    rows,
    run,
    set_owner,
)
from tests.helpers import dispatch_all, notifications_for


def missing_labels(r) -> str:
    return " | ".join(m["label"] for m in r.json()["details"]["missing"]).lower()


# ===================================================================================== fields
async def test_fields_proof_empty_whitespace_and_any_groups(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("qualification", prove_before=False)
    d = rig.did
    # nothing documented: refused with a useful list
    r = await rig.tick("need_documented", 422)
    assert r.json()["code"] == "proof_required" and "pain point" in missing_labels(r)
    for blank in ("", "   ", "\t\n"):
        await rig.patch(pain_point=blank)
        r = await rig.tick("need_documented", 422)
        assert "pain point" in missing_labels(r), repr(blank)
    await rig.patch(pain_point="x")  # one character is enough
    await rig.tick("need_documented", 204)
    await rig.tick("need_documented", 409)  # already ticked
    # lead stage: basic_info needs phone OR email (an "any" group)
    from tests.deep.phase3.p3 import force_stage

    await force_stage(admin_engine, world.org_id, d, "lead", ticker=rig.owner)
    await rig.tick("basic_info", 204)
    await rig.req("POST", f"/deals/{d}/criteria/basic_info/untick", 204, json={"reason": "retest"})
    contact = await one(admin_engine, "SELECT primary_contact_id FROM deals WHERE id=:d", d=d)
    await run(
        admin_engine, "UPDATE contacts SET email=NULL, phone_e164=NULL WHERE id=:c", c=contact
    )
    r = await rig.tick("basic_info", 422)
    assert "contact phone or contact email" in missing_labels(r)
    await run(admin_engine, "UPDATE contacts SET phone_e164='+919812345678' WHERE id=:c", c=contact)
    await rig.tick("basic_info", 204)
    await rig.req("POST", f"/deals/{d}/criteria/basic_info/untick", 204, json={"reason": "retest"})
    # the primary contact deleted: contact fields count as missing
    await run(admin_engine, "UPDATE contacts SET deleted_at=now() WHERE id=:c", c=contact)
    r = await rig.tick("basic_info", 422)
    assert "contact name" in missing_labels(r)


async def test_zero_amounts_count_as_filled_risk(client, world, admin_engine):
    """PRD 10 says 'budget confirmed (amount + source)'. A budget of 0 paise, 0 expected minutes and
    a final price of 0 are accepted as proof. Documented as a RISK in the report (no strict test)."""
    rig = await Rig(client, world, admin_engine).start("prospecting", prove_before=True)
    await rig.patch(
        budget_amount_paise=0,
        budget_source="Annual",
        expected_close_date=due(10),
        expected_close_reason="x",
        reason="re-planned",
    )
    await rig.tick("budget_confirmed", 204)  # a zero budget is "confirmed"
    await rig.patch(expected_minutes_per_month=0, pricing_model="usage", plan="G")
    await rig.req(
        "POST",
        f"/deals/{rig.did}/lines",
        201,
        json={"product_id": rig.product["id"], "quantity": 1},
    )
    await rig.tick("pricing_selected", 204)  # zero minutes per month is "selected"


# ==================================================================================== activity
async def test_activity_proof_types_two_way_notes_attendees_and_clock(
    client, world, admin_engine, monkeypatch
):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    d = rig.did
    # a call the customer did not answer is not "contact verified"
    await rig.log("call", two_way=False)
    r = await rig.tick("contact_verified", 422)
    assert "two-way" in missing_labels(r)
    # a note, however two-way it says it is, is not a call/email/meeting
    await rig.log("note", two_way=True)
    await rig.tick("contact_verified", 422)
    await rig.log("walkthrough", two_way=True)
    await rig.tick("contact_verified", 422)
    # an activity of another deal does not count, nor does a hint pointing at it
    other = Rig(client, world, admin_engine)
    await other.start("lead", prove_before=False)
    foreign = (await other.log("call"))["id"]
    r = await rig.tick("contact_verified", 422, proof_ref={"activity_id": foreign})
    assert "does not qualify" in missing_labels(r)
    # a hint pointing at an unrelated (non-qualifying) activity of this deal is refused too
    acts = (await rig.req("GET", f"/deals/{d}/activities", 200)).json()
    note_id = next(a["id"] for a in acts if a["type"] == "note")
    r = await rig.tick("contact_verified", 422, proof_ref={"activity_id": note_id})
    assert "does not qualify" in missing_labels(r)
    r = await rig.tick("contact_verified", 422, proof_ref={"activity_id": "not-a-uuid"})
    assert r.status_code == 422
    # exactly "now": a call dated exactly at the (controlled) current instant counts as completed
    fixed = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    import app.modules.pipeline.engine.proofs as proofs

    monkeypatch.setattr(proofs, "utc_now", lambda: fixed)
    good = (await rig.log("call", two_way=True))["id"]
    await run(admin_engine, "UPDATE activities SET occurred_at=:t WHERE id=:a", t=fixed, a=good)
    r = await rig.tick("contact_verified", 204, proof_ref={"activity_id": good})
    ref = await one(
        admin_engine,
        "SELECT proof_ref->>'activity_id' FROM deal_criteria WHERE deal_id=:d AND criterion_key='contact_verified'",
        d=d,
    )
    assert ref == good
    await rig.req(
        "POST", f"/deals/{d}/criteria/contact_verified/untick", 204, json={"reason": "again"}
    )
    # one microsecond later than "now" is not completed yet
    await run(
        admin_engine,
        "UPDATE activities SET occurred_at=:t WHERE id=:a",
        t=fixed + timedelta(microseconds=1),
        a=good,
    )
    await run(
        admin_engine,
        "UPDATE activities SET deleted_at=now() WHERE deal_id=:d AND id<>:a",
        d=d,
        a=good,
    )
    await rig.tick("contact_verified", 422)
    # soft-deleted activity never counts
    await run(
        admin_engine,
        "UPDATE activities SET occurred_at=:t, deleted_at=now() WHERE id=:a",
        t=fixed - timedelta(hours=1),
        a=good,
    )
    await rig.tick("contact_verified", 422)


async def test_meeting_booked_or_held_needs_attendees_and_only_held_passes_the_next_stage(
    client, world, admin_engine
):
    """Qualification takes a meeting that is BOOKED or HELD, with attendees; Meeting Scheduled
    takes only a HELD one with notes and attendees. A postponed or cancelled meeting never counts."""
    rig = await Rig(client, world, admin_engine).start("qualification", prove_before=False)
    # a booking with nobody listed is not enough
    m = (await rig.log("meeting", occurred_at=in_days(5), attendees=[]))["id"]
    r = await rig.tick("meeting_scheduled", 422)
    assert "attendees" in missing_labels(r)
    await run(admin_engine, "UPDATE activities SET attendees='[\"Asha\"]'::jsonb WHERE id=:a", a=m)
    await rig.tick("meeting_scheduled", 204)  # a booked meeting arranges it
    # cancelling the only meeting takes the proof away (and says so), re-booking brings it back
    await rig.req(
        "POST", f"/activities/{m}/meeting/cancel", 200,
        json={"reason_code": "we_cancelled", "why": "Owner is travelling"},
    )  # fmt: skip
    assert (await rig.crit("meeting_scheduled"))["proof_missing"] is True
    m2 = (await rig.log("meeting", occurred_at=in_days(7), attendees=["Asha"]))["id"]
    assert (await rig.crit("meeting_scheduled"))["proof_missing"] is False
    # a held meeting also arranges it, but a booked one does not satisfy "held"
    rig2 = await Rig(client, world, admin_engine).start("meeting_scheduled", prove_before=False)
    booked = (await rig2.log("meeting", occurred_at=in_days(2), attendees=["Asha"]))["id"]
    r = await rig2.tick("meeting_completed", 422)
    assert "held" in missing_labels(r)
    # held but nobody listed, or notes blank (set behind the API's back): still not enough
    held = (await rig2.log("meeting", occurred_at=ago(3), discussed="x", attendees=[]))["id"]
    r = await rig2.tick("meeting_completed", 422)
    assert "held" in missing_labels(r)
    await run(
        admin_engine,
        "UPDATE activities SET attendees='[\"Asha\"]'::jsonb, discussed='   ' WHERE id=:a",
        a=held,
    )
    await rig2.tick("meeting_completed", 422)
    await run(admin_engine, "UPDATE activities SET discussed='real notes' WHERE id=:a", a=held)
    await rig2.tick("meeting_completed", 204)
    assert booked and m2 and rig.did != rig2.did


# ============================================================================= checklist / tag / lines / documents
async def test_checklist_proof_no_items_item_added_after_ticking_and_deactivated(
    client, world, admin_engine
):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    d = rig.did
    # no fit items configured at all: cannot be proved, and the message says why
    await client.put(
        "/settings/fit-checklist", json={"items": [], "reason": "x"}, headers=H(world.admin)
    )
    r = await rig.tick("fits_profile", 422)
    assert "no items" in missing_labels(r)
    # configure two items and tick them all
    r = await client.put(
        "/settings/fit-checklist",
        json={"items": [{"label": "Real company"}, {"label": "Reachable"}], "reason": "x"},
        headers=H(world.admin),
    )
    items = [i for i in r.json() if i["active"]]
    assert len(items) == 2
    await rig.patch(fit_checklist={items[0]["id"]: True})
    r = await rig.tick("fits_profile", 422)
    assert "reachable" in missing_labels(r) and "real company" not in missing_labels(r)
    await rig.patch(fit_checklist={items[1]["id"]: True})
    await rig.tick("fits_profile", 204)
    # un-ticking an item behind the tick -> proof missing in the same request
    await rig.patch(fit_checklist={items[1]["id"]: False})
    assert (await rig.crit("fits_profile"))["proof_missing"] is True
    await rig.patch(fit_checklist={items[1]["id"]: True})
    assert (await rig.crit("fits_profile"))["proof_missing"] is False  # returns with the proof
    # a new item added after ticking: ticked deals now lack proof for it (flag only appears on the next recheck)
    new = await client.put(
        "/settings/fit-checklist",
        json={
            "items": [{"id": i["id"], "label": i["label"]} for i in items]
            + [{"label": "Budget owner known"}],
            "reason": "x",
        },
        headers=H(world.admin),
    )
    assert len([i for i in new.json() if i["active"]]) == 3
    await rig.patch(use_case="anything")  # triggers a recheck
    assert (await rig.crit("fits_profile"))["proof_missing"] is True
    # deactivating the item removes it from what is required
    keep = [
        {"id": i["id"], "label": i["label"]}
        for i in new.json()
        if i["active"] and i["label"] != "Budget owner known"
    ]
    await client.put(
        "/settings/fit-checklist", json={"items": keep, "reason": "x"}, headers=H(world.admin)
    )
    await rig.patch(use_case="anything else")
    assert (await rig.crit("fits_profile"))["proof_missing"] is False
    assert d


async def test_contact_tag_and_lines_and_documents(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("prospecting")
    d = rig.did
    await rig.tick("decision_maker", 204)
    await rig.tick("pricing_selected", 204)
    # untag the decision maker -> flagged immediately
    await rig.req(
        "PATCH", f"/contacts/{rig.ids['contact']}", 200, json={"is_decision_maker": False}
    )
    assert (await rig.crit("decision_maker"))["proof_missing"] is True
    # delete the only line -> pricing proof missing
    await rig.req(
        "DELETE",
        f"/deals/{d}/lines/{rig.ids['line']}",
        200,
        actor=world.admin,
        params={"reason": "customer dropped it"},
    )
    c = await rig.crit("pricing_selected")
    assert c["ticked"] and c["proof_missing"] is True
    # lines: zero lines cannot be ticked once the tick is removed
    await rig.req(
        "POST", f"/deals/{d}/criteria/pricing_selected/untick", 204, json={"reason": "redo"}
    )
    r = await rig.tick("pricing_selected", 422)
    assert "product" in missing_labels(r)
    # adding a line with a pending discount still counts as a line
    r = await rig.req(
        "POST",
        f"/deals/{d}/lines",
        None,
        json={
            "product_id": rig.product["id"],
            "quantity": 10,
            "discount_pct": "5",
            "reason": "competitor",
        },
    )
    assert r.status_code in (201, 202)
    await rig.tick("pricing_selected", 204)


async def test_untag_decision_maker_raises_proof_missing(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("prospecting")
    await rig.tick("decision_maker", 204)
    await rig.req(
        "PATCH", f"/contacts/{rig.ids['contact']}", 200, json={"is_decision_maker": False}
    )
    c = await rig.crit("decision_maker")
    assert c["ticked"] is True and c["proof_missing"] is True
    d = await rig.deal()
    assert d["flags"].get("proof_missing") is True


async def test_document_proof_kind_status_file_deleted(client, world, admin_engine):
    from tests.modules.journey import add_doc, make_file

    rig = await Rig(client, world, admin_engine).start("revision", prove_self=False)
    d = rig.did
    # revision.agreement_sent: agreement, sent or countersigned, WITH a file
    docs = (await rig.req("GET", f"/deals/{d}/documents", 200)).json()
    assert [x for x in docs if x["kind"] == "agreement"] == []
    r = await rig.tick("agreement_sent", 422)
    assert "agreement" in missing_labels(r) and "attach the file" in missing_labels(r)
    # a draft agreement is not enough
    await add_doc(client, rig.owner, d, "agreement", "draft")
    await rig.tick("agreement_sent", 422)
    # a different kind (invoice, sent, with a file) is not enough
    fid = await make_file(admin_engine, world, d, rig.owner)
    await add_doc(client, rig.owner, d, "invoice", "sent", fid)
    await rig.tick("agreement_sent", 422)
    # a sent agreement without a file is refused at creation; with a file it works
    await add_doc(client, rig.owner, d, "agreement", "sent", None, expect=422)
    # a file uploaded to ANOTHER deal cannot be attached
    other = Rig(client, world, admin_engine)
    await other.start("lead", prove_before=False)
    foreign = await make_file(admin_engine, world, other.did, rig.owner)
    r = await add_doc(client, rig.owner, d, "agreement", "sent", foreign, expect=422)
    assert r["code"] == "validation_error"
    # nor a file that is still pending
    pend = await make_file(admin_engine, world, d, rig.owner)
    await run(admin_engine, "UPDATE files SET status='pending' WHERE id=:f", f=pend)
    await add_doc(client, rig.owner, d, "agreement", "sent", pend, expect=422)
    good = await add_doc(client, rig.owner, d, "agreement", "sent", fid)
    await run(admin_engine, "UPDATE documents SET deleted_at=now() WHERE id=:i", i=good["id"])
    await rig.tick("agreement_sent", 422)  # soft-deleted
    await run(admin_engine, "UPDATE documents SET deleted_at=NULL WHERE id=:i", i=good["id"])
    await rig.tick("agreement_sent", 204)
    # the file detached from a SENT agreement afterwards (Team Leader edit): proof must go missing
    r = await client.patch(
        f"/documents/{good['id']}",
        json={"file_id": None, "reason": "wrong file"},
        headers=H(world.tl_north),
    )
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        c = await rig.crit("agreement_sent")
        assert c["proof_missing"] is True


async def test_BUG_P3_11_signed_agreement_can_lose_its_file(client, world, admin_engine):
    from tests.modules.journey import add_doc, make_file

    rig = await Rig(client, world, admin_engine).start("closure")
    fid = await make_file(admin_engine, world, rig.did, rig.owner)
    doc = await add_doc(client, rig.owner, rig.did, "agreement", "countersigned", fid)
    r = await client.patch(
        f"/documents/{doc['id']}",
        json={"file_id": None, "reason": "tidy"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 422, "a countersigned agreement now has no file"


async def test_also_proofs_need_both_parts(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("onboarding", prove_self=False)
    d = rig.did
    # kickoff_done = kickoff activity AND go-live date
    await rig.log("kickoff", occurred_at=ago(1))
    r = await rig.tick("kickoff_done", 422)
    assert "go-live date" in missing_labels(r) and "kickoff" not in missing_labels(r)
    await rig.patch(go_live_date=due(7))
    await rig.tick("kickoff_done", 204)
    # taking the go-live date away flags the tick even though the activity is still there
    await rig.patch(go_live_date=None)
    assert (await rig.crit("kickoff_done"))["proof_missing"] is True
    assert d


# ================================================================================ tick rules
async def test_tick_is_owner_only_for_every_role(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    d = rig.did
    await rig.log("call")
    await rig.log("note", two_way=False)
    for actor in (
        world.north2,
        world.south1,
        world.tl_north,
        world.tl_south,
        world.admin,
        world.sa,
    ):
        mark = await max_seq(admin_engine)
        r = await rig.tick("contact_verified", 403, actor)
        assert r.json()["code"] in ("permission_denied", "out_of_scope"), actor.name
        a = [x for x in await audit_rows(admin_engine, d) if x["seq"] > mark]
        assert [(x["action_key"], x["outcome"]) for x in a] == [("criterion.tick", "denied")]
    assert await one(admin_engine, "SELECT count(*) FROM deal_criteria WHERE deal_id=:d", d=d) == 0
    # a TL / Admin / SA who OWNS the deal can tick it (an Admin with a reason)
    for actor, expect in ((world.tl_north, 204), (world.admin, 422), (world.sa, 204)):
        await set_owner(admin_engine, d, actor)
        r = await rig.tick("contact_verified", expect, actor)
        if expect == 422:
            assert r.json()["code"] == "reason_required"
            await rig.tick("contact_verified", 204, actor, reason="own deal")
        await run(admin_engine, "DELETE FROM deal_criteria WHERE deal_id=:d", d=d)
    # unknown key, unknown deal, deal not open
    await set_owner(admin_engine, d, rig.owner)
    r = await rig.tick("no_such_key", 404)
    assert r.json()["code"] == "not_found"
    r = await rig.req(
        "POST", "/deals/00000000-0000-0000-0000-000000000000/criteria/x/tick", 404, json={}
    )
    await rig.req("POST", f"/deals/{d}/lose", 200, json={"lost_reason": "Price"})
    r = await rig.tick("contact_verified", 409)
    assert r.json()["code"] == "conflict"
    # extra fields are refused
    r = await rig.req(
        "POST", f"/deals/{d}/criteria/contact_verified/tick", 422, json={"verified": True}
    )


async def test_tick_audit_and_outbox_one_each(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    await rig.log("call")
    mark, ev = await max_seq(admin_engine), await max_event(admin_engine)
    await rig.tick("contact_verified", 204)
    a = [x for x in await audit_rows(admin_engine, rig.did) if x["seq"] > mark]
    assert len(a) == 1 and a[0]["action_key"] == "criterion.tick" and a[0]["outcome"] == "ok"
    assert a[0]["new_value"]["stage"] == "lead" and a[0]["new_value"]["proof"]["activity_id"]
    evs = [e["type"] for e in await events_after(admin_engine, ev, rig.did)]
    assert evs == ["deal.updated"]
    # concurrent ticks of the same criterion: one winner, the rest 409
    import asyncio

    rig2 = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    await rig2.log("call")
    res = await asyncio.gather(*(rig2.tick("contact_verified", None) for _ in range(8)))
    codes = sorted(r.status_code for r in res)
    assert codes == [204] + [409] * 7, codes
    assert (
        await one(admin_engine, "SELECT count(*) FROM deal_criteria WHERE deal_id=:d", d=rig2.did)
        == 1
    )


# ================================================================== untick, verify, reject
async def test_untick_rules_and_reasons(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    d = rig.did
    await rig.log("call")
    await rig.tick("contact_verified", 204)
    url = f"/deals/{d}/criteria/contact_verified/untick"
    r = await rig.req("POST", url, 422, json={})
    assert r.json()["code"] == "reason_required"  # important action: a rep needs a reason
    await rig.req("POST", url, 422, json={"reason": "  "})
    await rig.req("POST", url, 403, actor=world.tl_north, json={"reason": "x"})  # not their deal
    await rig.req("POST", url, 403, actor=world.admin, json={"reason": "x"})
    mark = await max_seq(admin_engine)
    await rig.req("POST", url, 204, json={"reason": "tick was wrong"})
    a = [x for x in await audit_rows(admin_engine, d) if x["seq"] > mark and x["outcome"] == "ok"]
    assert [(x["action_key"], x["reason"]) for x in a] == [("criterion.untick", "tick was wrong")]
    await rig.req("POST", url, 404, json={"reason": "again"})  # not ticked any more
    # Super admin owner: reason optional
    await set_owner(admin_engine, d, world.sa)
    await rig.tick("contact_verified", 204, world.sa)
    await rig.req("POST", url, 204, actor=world.sa, json={})


async def test_verify_and_reject_flow_with_notifications(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    d = rig.did
    await rig.log("call")
    await rig.tick("contact_verified", 204)
    vurl = f"/deals/{d}/criteria/contact_verified/verify"
    # who may verify: TL of the team, Admin, SA; not a rep, not another team's TL
    for actor in (world.north1, world.north2, world.south1, world.tl_south):
        await rig.req("POST", vurl, 403, actor=actor, json={"decision": "verify"})
    await rig.req("POST", vurl, 422, json={"decision": "maybe"}, actor=world.tl_north)
    await rig.req("POST", vurl, 422, json={"decision": "verify", "extra": 1}, actor=world.tl_north)
    await rig.req(
        "POST", vurl, 422, actor=world.admin, json={"decision": "verify"}
    )  # Admin must give a reason
    await rig.req("POST", vurl, 204, actor=world.tl_north, json={"decision": "verify"})
    row = (
        await rows(
            admin_engine,
            "SELECT verify_status, verified_by FROM deal_criteria WHERE deal_id=:d",
            d=d,
        )
    )[0]
    assert row == {"verify_status": "verified", "verified_by": world.tl_north.id}
    await rig.req("POST", vurl, 204, actor=world.admin, json={"decision": "verify", "reason": "ok"})
    await rig.req("POST", vurl, 204, actor=world.sa, json={"decision": "verify"})
    # reject needs a reason (every role below Super admin; the engine insists for all)
    await rig.req("POST", vurl, 422, actor=world.tl_north, json={"decision": "reject"})
    await rig.req(
        "POST", vurl, 422, actor=world.tl_north, json={"decision": "reject", "reason": "  "}
    )
    await rig.req(
        "POST", vurl, 403, actor=world.tl_south, json={"decision": "reject", "reason": "x"}
    )
    ev = await max_event(admin_engine)
    mark = await max_seq(admin_engine)
    await rig.req(
        "POST",
        vurl,
        204,
        actor=world.tl_north,
        json={"decision": "reject", "reason": "Call went to voicemail"},
    )
    assert (
        await one(admin_engine, "SELECT count(*) FROM deal_criteria WHERE deal_id=:d", d=d) == 0
    )  # tick removed
    a = [x for x in await audit_rows(admin_engine, d) if x["seq"] > mark]
    assert [(x["action_key"], x["outcome"], x["reason"]) for x in a] == [
        ("criterion.reject", "ok", "Call went to voicemail")
    ]
    # stage not passed yet: no review flag
    assert (await rig.deal())["flags"].get("review_needed") is None
    types = [e["type"] for e in await events_after(admin_engine, ev, d)]
    assert "criterion.rejected" in types and "deal.review_needed" not in types
    await dispatch_all()
    await dispatch_all()
    mine = [n for n in await notifications_for(rig.owner.id) if n.type == "criterion.rejected"]
    assert len(mine) == 1 and "voicemail" in mine[0].body
    # reject of a tick that does not exist
    await rig.req("POST", vurl, 404, actor=world.tl_north, json={"decision": "verify"})


async def test_reject_after_the_deal_moved_on_flags_review_and_review_done_clears(
    client, world, admin_engine
):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    d = rig.did
    await rig.log("call")
    await rig.patch(fit_checklist={f["id"]: True for f in rig.fit})
    for k in ("basic_info", "company_identified", "contact_verified", "fits_profile"):
        await rig.tick(k, 204)
    await rig.req("POST", f"/deals/{d}/move", 200, json={"to_stage": "qualification"})
    entry = await one(
        admin_engine,
        "SELECT id FROM deal_stage_entries WHERE deal_id=:d AND stage_key='lead' ORDER BY entered_at LIMIT 1",
        d=d,
    )
    ev = await max_event(admin_engine)
    # rejecting without naming the visit picks the latest tick of that key; here only one exists
    await rig.req(
        "POST",
        f"/deals/{d}/criteria/basic_info/verify",
        204,
        actor=world.admin,
        json={"decision": "reject", "reason": "no phone", "entry_id": str(entry)},
    )
    assert (await rig.deal())["flags"].get("review_needed") is True
    await dispatch_all()
    await dispatch_all()
    for who in (rig.owner, world.tl_north):
        got = [n for n in await notifications_for(who.id) if n.type == "deal.review_needed"]
        assert len(got) == 1, who.name  # owner and TL, once each
    types = [e["type"] for e in await events_after(admin_engine, ev, d)]
    assert types.count("deal.review_needed") == 1
    # review-done: roles, reason, only when flagged
    url = f"/deals/{d}/review-done"
    await rig.req("POST", url, 403, json={})  # the rep cannot clear it
    await rig.req("POST", url, 403, actor=world.tl_south, json={})
    await rig.req("POST", url, 204, actor=world.tl_north, json={})
    assert (await rig.deal())["flags"].get("review_needed") is None
    await rig.req("POST", url, 409, actor=world.tl_north, json={})
    # a wrong entry id is "not ticked"
    await rig.req(
        "POST",
        f"/deals/{d}/criteria/basic_info/verify",
        404,
        actor=world.admin,
        json={
            "decision": "reject",
            "reason": "x",
            "entry_id": "00000000-0000-0000-0000-000000000000",
        },
    )


# =============================================================================== proof missing
async def _proof_missing_scenario(
    client, world, eng, stage, key, remove, restore, who=None, notes=1
):
    """Tick `key`, remove its proof with `remove`, check the flag in the same request, that moving
    forward is blocked, that it notifies once, and that `restore` clears it."""
    rig = await Rig(client, world, eng).start(stage)
    await rig.tick(key, 204)
    ev = await max_event(eng)
    await remove(rig)
    c = await rig.crit(key)
    assert c["ticked"] and c["proof_missing"] is True, "flag must be set by the same request"
    d = await rig.deal()
    assert d["flags"].get("proof_missing") is True
    r = await rig.req(
        "POST", f"/deals/{rig.did}/move", 422, json={"to_stage": "x" if False else _NEXT[stage]}
    )
    assert r.json()["code"] == "criteria_incomplete"
    assert {m["reason"] for m in r.json()["details"]["missing"] if m["key"] == key} == {
        "proof missing"
    }
    types = [e["type"] for e in await events_after(eng, ev, rig.did)]
    assert types.count("deal.proof_missing") == 1
    await dispatch_all()
    await dispatch_all()
    for u in (rig.owner, world.tl_north):
        got = [n for n in await notifications_for(u.id) if n.type == "deal.proof_missing"]
        assert len(got) == notes, (u.name, len(got))
    # a further unrelated change must not notify again
    await rig.patch(use_case="unrelated change")
    assert [e["type"] for e in await events_after(eng, ev, rig.did)].count(
        "deal.proof_missing"
    ) == 1
    await restore(rig)
    c = await rig.crit(key)
    assert c["ticked"] and c["proof_missing"] is False
    assert (await rig.deal())["flags"].get("proof_missing") is None
    return rig


_NEXT = {"lead": "qualification", "qualification": "meeting_scheduled", "meeting_scheduled": "prospecting", "prospecting": "proposal"}  # fmt: skip


async def test_proof_missing_when_the_activity_is_deleted_then_restored(
    client, world, admin_engine
):
    async def remove(rig):
        acts = (await rig.req("GET", f"/deals/{rig.did}/activities", 200)).json()
        for a in acts:
            r = await rig.req(
                "DELETE",
                f"/activities/{a['id']}",
                None,
                actor=world.tl_north,
                params={"reason": "dup"},
            )
            assert r.status_code == 204, r.text

    async def restore(rig):
        await rig.log("email")

    await _proof_missing_scenario(
        client, world, admin_engine, "lead", "contact_verified", remove, restore
    )


async def test_proof_missing_when_the_activity_is_edited(client, world, admin_engine):
    async def remove(rig):
        a = (await rig.req("GET", f"/deals/{rig.did}/activities", 200)).json()[0]
        rig.ids["a"] = a["id"]
        await rig.req(
            "PATCH", f"/activities/{a['id']}", 200, json={"two_way": False, "reason": "mis-logged"}
        )

    async def restore(rig):
        await rig.req(
            "PATCH", f"/activities/{rig.ids['a']}", 200, json={"two_way": True, "reason": "fixed"}
        )

    await _proof_missing_scenario(
        client, world, admin_engine, "lead", "contact_verified", remove, restore
    )


async def test_proof_missing_when_a_field_is_emptied(client, world, admin_engine):
    async def remove(rig):
        await rig.patch(pain_point=None)

    async def restore(rig):
        await rig.patch(pain_point="back again")

    await _proof_missing_scenario(
        client, world, admin_engine, "qualification", "need_documented", remove, restore
    )


async def test_proof_missing_when_the_fit_list_changes(client, world, admin_engine):
    async def remove(rig):
        await rig.patch(fit_checklist={rig.fit[0]["id"]: False})

    async def restore(rig):
        await rig.patch(fit_checklist={rig.fit[0]["id"]: True})

    await _proof_missing_scenario(
        client, world, admin_engine, "lead", "fits_profile", remove, restore
    )


async def test_proof_missing_when_the_last_line_is_deleted(client, world, admin_engine):
    async def remove(rig):
        await rig.req(
            "DELETE",
            f"/deals/{rig.did}/lines/{rig.ids['line']}",
            200,
            actor=world.admin,
            params={"reason": "r"},
        )

    async def restore(rig):
        await rig.req(
            "POST",
            f"/deals/{rig.did}/lines",
            201,
            json={"product_id": rig.product["id"], "quantity": 5},
        )

    await _proof_missing_scenario(
        client, world, admin_engine, "prospecting", "pricing_selected", remove, restore
    )


async def test_proof_missing_when_a_document_is_deleted_or_edited(client, world, admin_engine):
    from tests.modules.journey import add_doc

    async def remove(rig):
        await rig.req(
            "DELETE",
            f"/documents/{rig.ids['agreement']}",
            204,
            params={"reason": "wrong one"},
            actor=world.tl_north,
        )

    async def restore(rig):
        from tests.modules.journey import make_file

        fid = await make_file(admin_engine, world, rig.did, rig.owner)
        await add_doc(client, rig.owner, rig.did, "agreement", "sent", fid)

    async def next_stage(rig):
        return None

    _NEXT["revision"] = "closure"
    rig = await _proof_missing_scenario(
        client, world, admin_engine, "revision", "agreement_sent", remove, restore
    )
    # edit route: the agreement is changed back to a draft by a Team Leader
    docs = (await rig.req("GET", f"/deals/{rig.did}/documents", 200)).json()
    r = await client.patch(
        f"/documents/{docs[0]['id']}",
        json={"status": "draft", "reason": "back to draft"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    assert (await rig.crit("agreement_sent"))["proof_missing"] is True
    r = await client.patch(
        f"/documents/{docs[0]['id']}",
        json={"status": "sent", "reason": "sent again"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200
    assert (await rig.crit("agreement_sent"))["proof_missing"] is False


async def test_proof_missing_rows_are_flagged_on_every_old_stage_too(client, world, admin_engine):
    """The deal has moved on; the earlier stage's proof is removed: the warning still shows."""
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    await rig.log("call")
    await rig.patch(fit_checklist={f["id"]: True for f in rig.fit})
    for k in ("basic_info", "company_identified", "contact_verified", "fits_profile"):
        await rig.tick(k, 204)
    await rig.req("POST", f"/deals/{rig.did}/move", 200, json={"to_stage": "qualification"})
    acts = (await rig.req("GET", f"/deals/{rig.did}/activities", 200)).json()
    await rig.req(
        "DELETE",
        f"/activities/{acts[0]['id']}",
        204,
        actor=world.tl_north,
        params={"reason": "dup"},
    )
    d = await rig.deal()
    assert d["flags"].get("proof_missing") is True


async def test_BUG_P3_12_stale_proof_missing_flag_after_untick(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("qualification")
    await rig.tick("need_documented", 204)
    await rig.patch(pain_point=None)
    assert (await rig.deal())["flags"].get("proof_missing") is True
    await rig.req(
        "POST", f"/deals/{rig.did}/criteria/need_documented/untick", 204, json={"reason": "redo"}
    )
    d = await rig.deal()
    assert d["flags"].get("proof_missing") is None, (
        "no tick is left, yet the deal still warns 'proof missing'"
    )


async def test_BUG_P3_13_stale_proof_missing_flag_after_reject(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("qualification")
    await rig.tick("need_documented", 204)
    await rig.patch(pain_point=None)
    await rig.req(
        "POST",
        f"/deals/{rig.did}/criteria/need_documented/verify",
        204,
        actor=world.tl_north,
        json={"decision": "reject", "reason": "no proof"},
    )
    assert (await rig.deal())["flags"].get("proof_missing") is None


async def test_override_clears_proof_missing_marks_on_that_stage_but_flag_is_recomputed_later(
    client, world, admin_engine
):
    rig = await Rig(client, world, admin_engine).start("qualification")
    await rig.tick("need_documented", 204)
    await rig.patch(pain_point=None)
    r = await rig.req(
        "POST",
        f"/deals/{rig.did}/move",
        200,
        actor=world.tl_north,
        json={
            "to_stage": "meeting_scheduled",
            "override": True,
            "reason": "customer confirmed by phone",
        },
    )
    assert "need_documented" in r.json()["overridden"]
    # the old tick is now an override record, not a proof-bearing tick
    st = await one(
        admin_engine,
        "SELECT verify_status FROM deal_criteria WHERE deal_id=:d AND criterion_key='need_documented'",
        d=rig.did,
    )
    assert st == "overridden"
    await rig.patch(use_case="forces a recheck")
    assert (await rig.deal())["flags"].get("proof_missing") is None


async def test_a_held_meeting_cannot_be_dated_into_the_future_afterwards(
    client, world, admin_engine
):
    """meeting_completed ticked on a HELD meeting: the date cannot later be edited into the future
    (that would turn history into a promise), and an ordinary edit leaves the tick standing."""
    rig = await Rig(client, world, admin_engine).start("meeting_scheduled")
    await rig.tick("meeting_completed", 204)
    await rig.req(
        "PATCH",
        f"/activities/{rig.ids['meeting']}",
        422,
        json={"occurred_at": in_days(9), "reason": "typo"},
    )
    await rig.req(
        "PATCH",
        f"/activities/{rig.ids['meeting']}",
        200,
        json={"discussed": "Discovery notes, with the pricing questions", "reason": "more detail"},
    )
    assert (await rig.crit("meeting_completed"))["proof_missing"] is False


async def test_ticked_proof_can_move_to_another_qualifying_record(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    first = (await rig.log("call"))["id"]
    await rig.tick("contact_verified", 204)
    ref1 = await one(
        admin_engine,
        "SELECT proof_ref->>'activity_id' FROM deal_criteria WHERE deal_id=:d",
        d=rig.did,
    )
    assert ref1 == first
    second = (await rig.log("email"))["id"]
    await rig.req(
        "DELETE", f"/activities/{first}", 204, actor=world.tl_north, params={"reason": "dup"}
    )
    c = await rig.crit("contact_verified")
    assert c["proof_missing"] is False  # the email still proves it
    ref2 = await one(
        admin_engine,
        "SELECT proof_ref->>'activity_id' FROM deal_criteria WHERE deal_id=:d",
        d=rig.did,
    )
    assert ref2 == second


async def test_notifications_fire_again_only_after_proof_returned_and_is_lost_again(
    client, world, admin_engine
):
    rig = await Rig(client, world, admin_engine).start("qualification")
    await rig.tick("need_documented", 204)
    ev = await max_event(admin_engine)
    for _ in range(3):
        await rig.patch(pain_point=None)
        await rig.patch(pain_point="back")
    types = [e["type"] for e in await events_after(admin_engine, ev, rig.did)]
    assert types.count("deal.proof_missing") == 3  # one per loss, none for the restores
    await dispatch_all()
    await dispatch_all()
    got = [n for n in await notifications_for(rig.owner.id) if n.type == "deal.proof_missing"]
    assert len(got) == 3
    assert datetime.now(UTC) and ago(1)
