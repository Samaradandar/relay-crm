# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 4 (second half): edit rules, Undo, next steps, snooze, replied,
nudge, task assignment, Today for new leads."""

from datetime import timedelta

import pytest

from app.core.time import utc_now
from app.modules.deals.health import ist_date
from tests.deep.phase3.invariants import check_invariants
from tests.deep.phase3.p3 import (
    H,
    audit_rows,
    due,
    force_stage,
    max_seq,
    mkdeal,
    one,
    rows,
    run,
    set_owner,
)
from tests.helpers import dispatch_all, notifications_for


async def patch(client, actor, did, expect=200, **body):
    r = await client.patch(f"/deals/{did}", json=body, headers=H(actor))
    assert r.status_code == expect, (r.status_code, r.text[:300])
    return r


# =================================================================== expected close date
async def test_expected_close_date_rules_per_role(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    # first time: free, no reason, for the owner
    await patch(client, owner, did, expected_close_date=due(30))
    # change: reason (User, TL, Admin), optional for the Super admin
    await patch(client, owner, did, 422, expected_close_date=due(31))
    await patch(
        client, owner, did, expected_close_date=due(31), reason="customer moved the budget meeting"
    )
    await patch(
        client, world.north2, did, 403, expected_close_date=due(32), reason="x"
    )  # not the owner
    await patch(client, world.tl_south, did, 403, expected_close_date=due(32), reason="x")
    await patch(client, world.tl_north, did, 422, expected_close_date=due(32))
    await patch(client, world.tl_north, did, expected_close_date=due(32), reason="team call")
    await patch(client, world.admin, did, 422, expected_close_date=due(33))
    await patch(client, world.admin, did, expected_close_date=due(33), reason="ops")
    await patch(client, world.sa, did, expected_close_date=due(34))
    a = [
        x
        for x in await audit_rows(eng, did)
        if x["action_key"] == "deal.close_date.change" and x["outcome"] == "ok"
    ]
    assert len(a) == 4 and all(x["reason"] or x["actor_id"] == world.sa.id for x in a)
    # same value again: nothing changes, no reason needed, no audit row
    mark = await max_seq(eng)
    await patch(client, owner, did, expected_close_date=due(34))
    assert [x for x in await audit_rows(eng, did) if x["seq"] > mark] == []


async def test_expected_close_date_cannot_be_emptied_from_prospecting_on(
    client, world, admin_engine
):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    await patch(client, owner, did, expected_close_date=due(30))
    # in Lead / Qualification / Meeting it may be emptied (with the reason a change needs)
    await patch(client, owner, did, expected_close_date=None, reason="not sure yet")
    await patch(client, owner, did, expected_close_date=due(30))
    for stage in ("prospecting", "proposal", "revision", "closure", "onboarding"):
        await force_stage(eng, world.org_id, did, stage, ticker=owner)
        r = await patch(client, world.admin, did, 422, expected_close_date=None, reason="clearing")
        assert (
            r.json()["code"] == "validation_error"
            and "required from Prospecting" in r.json()["message"]
        ), stage
    assert await one(eng, "SELECT expected_close_date IS NOT NULL FROM deals WHERE id=:d", d=did)


async def test_pricing_fields_after_prospecting_need_a_reason(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    await force_stage(eng, world.org_id, did, "prospecting", ticker=owner)
    # in Prospecting itself they are ordinary fields
    await patch(
        client, owner, did, pricing_model="usage", plan="Growth", expected_minutes_per_month=1000
    )
    await patch(client, owner, did, plan="Scale", expected_minutes_per_month=2000)
    await force_stage(eng, world.org_id, did, "proposal", ticker=owner)
    for field, value in (
        ("pricing_model", "fixed"),
        ("plan", "Pro"),
        ("expected_minutes_per_month", 5000),
    ):
        r = await patch(client, owner, did, 422, **{field: value})
        assert r.json()["code"] == "reason_required", field
        await patch(client, owner, did, **{field: value, "reason": "customer renegotiated"})
        await patch(
            client,
            world.north2,
            did,
            403,
            **{field: value + 1 if isinstance(value, int) else value + "x", "reason": "x"},
        )
    # an unrelated field in the same stage stays free
    await patch(client, owner, did, use_case="free to change")
    # boundaries of the number
    await patch(client, owner, did, 422, expected_minutes_per_month=-1, reason="x")
    await patch(client, owner, did, expected_minutes_per_month=0, reason="zero")
    a = [
        x
        for x in await audit_rows(eng, did)
        if x["action_key"] == "deal.pricing.change_after_prospecting" and x["outcome"] == "ok"
    ]
    assert len(a) == 4


async def test_lead_source_after_the_lead_stage(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    await patch(client, owner, did, lead_source="Referral")  # in Lead: the owner may
    await patch(client, owner, did, 422, lead_source="Nonsense")
    await patch(client, owner, did, 422, lead_source=None)
    await force_stage(eng, world.org_id, did, "qualification", ticker=owner)
    await patch(
        client, owner, did, 403, lead_source="Website", reason="typo"
    )  # a rep may not any more
    await patch(client, world.tl_south, did, 403, lead_source="Website", reason="x")
    await patch(client, world.tl_north, did, 422, lead_source="Website")
    await patch(client, world.tl_north, did, lead_source="Website", reason="mis-entered")
    await patch(client, world.admin, did, 422, lead_source="Event")
    await patch(client, world.admin, did, lead_source="Event", reason="correction")
    await patch(client, world.sa, did, lead_source="LinkedIn")
    keys = [
        x["action_key"]
        for x in await audit_rows(eng, did)
        if x["outcome"] == "ok" and x["action_key"].startswith("deal.source")
    ]
    assert keys == ["deal.source.change_after_lead"] * 3


async def test_locked_and_forbidden_fields_are_refused(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    for field, value in (
        ("created_at", "2020-01-01T00:00:00Z"), ("created_by", str(world.sa.id)), ("generated_by", str(world.sa.id)),
        ("owner_id", str(world.sa.id)), ("team_id", str(world.south_team)), ("value_paise", 1), ("status", "won"),
        ("stage_key", "onboarding"), ("lost_reason", "x"), ("won_at", "2020-01-01T00:00:00Z"), ("health", "stale"),
        ("flags", {"proof_missing": True}), ("data", {"x": 1}), ("id", "x"), ("org_id", str(world.org_id)),
        ("version", 99), ("revision_round", 9), ("deleted_at", "2020-01-01T00:00:00Z"),
    ):  # fmt: skip
        r = await client.patch(f"/deals/{did}", json={field: value}, headers=H(world.sa))
        assert r.status_code == 422, (field, r.status_code)
    assert (await client.patch(f"/deals/{did}", json={}, headers=H(owner))).status_code == 422
    row = (
        await rows(
            admin_engine,
            "SELECT status, stage_key, value_paise, owner_id FROM deals WHERE id=:d",
            d=did,
        )
    )[0]
    assert (row["status"], row["stage_key"], row["value_paise"], row["owner_id"]) == (
        "open",
        "lead",
        0,
        owner.id,
    )


async def test_expected_version_conflicts_and_lost_deals(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    v = (await client.get(f"/deals/{did}", headers=H(owner))).json()["version"]
    await patch(client, owner, did, 409, use_case="a", expected_version=v + 5)
    r = await patch(client, owner, did, use_case="a", expected_version=v)
    assert r.json()["version"] == v + 1
    await patch(client, owner, did, 409, use_case="b", expected_version=v)  # stale
    await patch(
        client, owner, did, 409 if False else 200, use_case="a"
    )  # same value: no change, no conflict
    await client.post(f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(owner))
    r = await patch(client, owner, did, 409, use_case="c")
    assert "Reopen" in r.json()["message"]


async def test_fit_checklist_ids_and_custom_field_ids(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    fit = (await client.get("/settings/fit-checklist", headers=H(world.admin))).json()
    await patch(client, owner, did, fit_checklist={fit[0]["id"]: True})
    await patch(client, owner, did, 422, fit_checklist={"not-a-uuid": True})
    await patch(
        client, owner, did, 422, fit_checklist={"00000000-0000-0000-0000-000000000000": True}
    )
    # a hidden item cannot be ticked any more
    keep = [{"id": x["id"], "label": x["label"]} for x in fit[1:]]
    await client.put(
        "/settings/fit-checklist", json={"items": keep, "reason": "x"}, headers=H(world.admin)
    )
    await patch(client, owner, did, 422, fit_checklist={fit[0]["id"]: True})
    det = (await client.get(f"/deals/{did}", headers=H(owner))).json()
    assert [f["id"] for f in det["fit"]] == [x["id"] for x in fit[1:]]


# ================================================================================= undo
async def test_undo_window_owner_only_and_changed_again(client, world, admin_engine, monkeypatch):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    r = await patch(client, owner, did, use_case="first")
    audit_id = r.json()["last_audit_id"]
    # someone else cannot undo it
    for actor in (world.north2, world.tl_north, world.admin):
        r = await client.post(f"/deals/{did}/undo", json={"audit_id": audit_id}, headers=H(actor))
        assert r.status_code in (403, 404) and r.status_code != 200, (actor.name, r.status_code)
    # changed again: cannot undo safely
    await patch(client, world.tl_north, did, use_case="second")
    r = await client.post(f"/deals/{did}/undo", json={"audit_id": audit_id}, headers=H(owner))
    assert r.status_code == 409 and "changed again" in r.json()["message"]
    # fresh edit: undo works once and writes its own audit row; the original row is untouched
    r = await patch(client, owner, did, use_case="third")
    aid = r.json()["last_audit_id"]
    before_rows = await audit_rows(eng, did)
    r = await client.post(f"/deals/{did}/undo", json={"audit_id": aid}, headers=H(owner))
    assert r.status_code == 200 and r.json()["use_case"] == "second"
    after = await audit_rows(eng, did)
    assert len(after) == len(before_rows) + 1 and after[-1]["action_key"] == "deal.undo"
    assert after[: len(before_rows)] == before_rows
    assert (
        await client.post(f"/deals/{did}/undo", json={"audit_id": aid}, headers=H(owner))
    ).status_code == 409
    # undo of an undo row, of a row of another deal, of a random id
    assert (
        await client.post(
            f"/deals/{did}/undo", json={"audit_id": str(after[-1]["seq"])}, headers=H(owner)
        )
    ).status_code == 422
    other, o2 = await mkdeal(client, world, eng, owner=world.north2)
    rr = await patch(client, o2, other, use_case="zzz")
    assert (
        await client.post(
            f"/deals/{did}/undo", json={"audit_id": rr.json()["last_audit_id"]}, headers=H(owner)
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/deals/{did}/undo",
            json={"audit_id": "00000000-0000-0000-0000-000000000000"},
            headers=H(owner),
        )
    ).status_code == 404
    # the 10-second window: 9 s still fine, 11 s refused (clock controlled)
    import app.modules.deals.service as svc

    r = await patch(client, owner, did, use_case="timed")
    aid = r.json()["last_audit_id"]
    real = utc_now()
    monkeypatch.setattr(svc, "utc_now", lambda: real + timedelta(seconds=11))
    r = await client.post(f"/deals/{did}/undo", json={"audit_id": aid}, headers=H(owner))
    assert r.status_code == 409 and "10-second" in r.json()["message"]
    monkeypatch.setattr(svc, "utc_now", lambda: real + timedelta(seconds=9))
    r = await client.post(f"/deals/{did}/undo", json={"audit_id": aid}, headers=H(owner))
    assert r.status_code == 200
    assert not await check_invariants(eng, world.org_id)


async def test_undo_restores_new_fields_next_step_and_proof_flags(client, world, admin_engine):
    from tests.deep.phase3.p3 import Rig

    rig = await Rig(client, world, admin_engine).start("qualification")
    await rig.tick("need_documented", 204)
    r = await patch(client, rig.owner, rig.did, pain_point=None)
    assert (await rig.deal())["flags"].get("proof_missing") is True
    r = await client.post(
        f"/deals/{rig.did}/undo", json={"audit_id": r.json()["last_audit_id"]}, headers=H(rig.owner)
    )
    assert r.status_code == 200 and r.json()["pain_point"]
    assert (await rig.crit("need_documented"))["proof_missing"] is False
    # next step text and date are undone together with the other fields
    before = (await rig.deal())["next_step"]
    r = await patch(
        client, rig.owner, rig.did, next_step_text="Brand new step", next_step_due_date=due(5)
    )
    assert r.json()["next_step"]["text"] == "Brand new step"
    r = await client.post(
        f"/deals/{rig.did}/undo", json={"audit_id": r.json()["last_audit_id"]}, headers=H(rig.owner)
    )
    assert r.json()["next_step"]["text"] == before["text"]


# ============================================================================ next steps
async def test_next_step_cannot_be_emptied_or_dated_in_the_past(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    await patch(client, owner, did, 422, next_step_text=None)
    await patch(client, owner, did, 422, next_step_due_date=None)
    await patch(client, owner, did, 422, next_step_text="")
    await patch(client, owner, did, 422, next_step_text="x" * 501)
    await patch(client, owner, did, 422, next_step_due_date=due(-1))
    await patch(client, owner, did, next_step_due_date=due(0))  # today is fine
    await patch(client, owner, did, next_step_text="x" * 500)
    for body in (
        {"text": "  ", "due_date": due(1)},
        {"text": "ok", "due_date": due(-1)},
        {"text": "ok"},
        {"due_date": due(1)},
        {"text": "ok", "due_date": due(1), "lane": "elsewhere"},
    ):
        r = await client.post(f"/deals/{did}/next-step", json=body, headers=H(owner))
        assert r.status_code == 422, body
    assert not await check_invariants(admin_engine, world.org_id)


async def test_next_step_set_done_roles_and_one_open_step(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    new = {"text": "Send proposal", "due_date": due(3)}
    for actor, code in ((world.north2, 403), (world.south1, 403), (world.tl_south, 403)):
        r = await client.post(f"/deals/{did}/next-step", json=new, headers=H(actor))
        assert r.status_code == code, actor.name
    assert (
        await client.post(f"/deals/{did}/next-step", json=new, headers=H(owner))
    ).status_code == 204
    assert (
        await client.post(f"/deals/{did}/next-step", json=new, headers=H(world.tl_north))
    ).status_code == 204
    assert (
        await client.post(f"/deals/{did}/next-step", json=new, headers=H(world.admin))
    ).status_code == 422  # Admin needs a reason
    assert (
        await client.post(
            f"/deals/{did}/next-step", json={**new, "reason": "ops"}, headers=H(world.admin)
        )
    ).status_code == 204
    assert (
        await client.post(f"/deals/{did}/next-step", json=new, headers=H(world.sa))
    ).status_code == 204
    assert (
        await one(
            eng, "SELECT count(*) FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did
        )
        == 1
    )
    sid = await one(eng, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did)
    # Done always comes with the next step
    r = await client.post(f"/next-steps/{sid}/done", json={}, headers=H(owner))
    assert r.status_code == 422
    r = await client.post(
        f"/next-steps/{sid}/done", json={"next": {"text": "", "due_date": due(2)}}, headers=H(owner)
    )
    assert r.status_code == 422
    r = await client.post(
        f"/next-steps/{sid}/done",
        json={"next": {"text": "Call back", "due_date": due(-3)}},
        headers=H(owner),
    )
    assert r.status_code == 422
    assert await one(
        eng, "SELECT done_at IS NULL FROM next_steps WHERE id=:s", s=sid
    )  # nothing changed
    for actor in (world.north2, world.tl_south):
        r = await client.post(
            f"/next-steps/{sid}/done",
            json={"next": {"text": "x", "due_date": due(2)}},
            headers=H(actor),
        )
        assert r.status_code == 403
    r = await client.post(
        f"/next-steps/{sid}/done",
        json={"next": {"text": "Call back", "due_date": due(2)}},
        headers=H(owner),
    )
    assert r.status_code == 204
    r = await client.post(
        f"/next-steps/{sid}/done",
        json={"next": {"text": "Call back", "due_date": due(2)}},
        headers=H(owner),
    )
    assert r.status_code == 409  # already done
    assert (
        await one(
            eng, "SELECT count(*) FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did
        )
        == 1
    )
    # unknown step, other org's step id
    r = await client.post(
        "/next-steps/00000000-0000-0000-0000-000000000000/done",
        json={"next": {"text": "x", "due_date": due(2)}},
        headers=H(owner),
    )
    assert r.status_code == 404
    assert not await check_invariants(eng, world.org_id)


async def test_snooze_from_past_due_from_future_due_and_the_count(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    sid = await one(eng, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did)
    today = ist_date(utc_now())
    # past due: +2 days from today
    await run(
        eng, "UPDATE next_steps SET due_date=:t WHERE id=:s", t=today - timedelta(days=10), s=sid
    )
    mark = await max_seq(eng)
    assert (
        await client.post(f"/next-steps/{sid}/snooze", json={}, headers=H(owner))
    ).status_code == 204
    due_now = await one(eng, "SELECT due_date FROM next_steps WHERE id=:s", s=sid)
    assert due_now == today + timedelta(days=2)
    # future due: +2 days from that date
    await run(
        eng, "UPDATE next_steps SET due_date=:t WHERE id=:s", t=today + timedelta(days=5), s=sid
    )
    assert (
        await client.post(f"/next-steps/{sid}/snooze", json={}, headers=H(owner))
    ).status_code == 204
    assert await one(
        eng, "SELECT due_date FROM next_steps WHERE id=:s", s=sid
    ) == today + timedelta(days=7)
    # due today: +2 from today; no limit, every snooze counted and logged
    await run(eng, "UPDATE next_steps SET due_date=:t WHERE id=:s", t=today, s=sid)
    for _ in range(6):
        assert (
            await client.post(f"/next-steps/{sid}/snooze", json={}, headers=H(owner))
        ).status_code == 204
    assert await one(eng, "SELECT snooze_count FROM next_steps WHERE id=:s", s=sid) == 8
    a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert len(a) == 8 and all(
        x["action_key"] == "deal.snooze_nudge_replied" and x["note"] == "Snoozed +2 days" for x in a
    )
    # who may snooze
    for actor, code in (
        (world.north2, 403),
        (world.south1, 403),
        (world.tl_south, 403),
        (world.admin, 422),
    ):
        assert (
            await client.post(f"/next-steps/{sid}/snooze", json={}, headers=H(actor))
        ).status_code == code, actor.name
    assert (
        await client.post(
            f"/next-steps/{sid}/snooze", json={"reason": "ops"}, headers=H(world.admin)
        )
    ).status_code == 204
    assert (
        await client.post(f"/next-steps/{sid}/snooze", json={}, headers=H(world.tl_north))
    ).status_code == 204
    assert (
        await client.post(f"/next-steps/{sid}/snooze", json={}, headers=H(world.sa))
    ).status_code == 204
    # snooze on a lost deal / a done step
    await client.post(f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(owner))
    assert (
        await client.post(f"/next-steps/{sid}/snooze", json={}, headers=H(owner))
    ).status_code == 409


async def test_replied_nudge_and_waiting_lane(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    sid = await one(eng, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did)
    # not waiting: replied and nudge are refused
    assert (
        await client.post(f"/next-steps/{sid}/replied", json={}, headers=H(owner))
    ).status_code == 409
    assert (
        await client.post(f"/next-steps/{sid}/nudge", json={}, headers=H(owner))
    ).status_code == 409
    r = await client.post(
        f"/deals/{did}/next-step",
        json={"text": "Wait for the PO", "due_date": due(3), "lane": "waiting_on_them"},
        headers=H(owner),
    )
    assert r.status_code == 204
    sid = await one(eng, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did)
    # waiting 3 IST days: nudge due
    await run(eng, "UPDATE next_steps SET lane_since=now() - interval '3 days' WHERE id=:s", s=sid)
    from app.core.db import get_sessionmaker
    from app.modules.deals.today import refresh_health

    await refresh_health(get_sessionmaker())
    assert (await client.get(f"/deals/{did}", headers=H(owner))).json()["flags"].get(
        "nudge_due"
    ) is True
    today = (await client.get("/today", headers=H(owner))).json()
    assert today["counts"]["nudge_due"] == 1 and [
        x["deal_id"] for x in today["waiting_on_them"]
    ] == [did]
    # nudging restarts the wait: the flag clears
    assert (
        await client.post(f"/next-steps/{sid}/nudge", json={}, headers=H(owner))
    ).status_code == 204
    assert (await client.get(f"/deals/{did}", headers=H(owner))).json()["flags"].get(
        "nudge_due"
    ) is None
    # "they replied" moves it back to Your move
    assert (
        await client.post(f"/next-steps/{sid}/replied", json={}, headers=H(world.north2))
    ).status_code == 403
    assert (
        await client.post(f"/next-steps/{sid}/replied", json={}, headers=H(owner))
    ).status_code == 204
    today = (await client.get("/today", headers=H(owner))).json()
    assert [x["deal_id"] for x in today["your_move"]] == [did] and today["waiting_on_them"] == []
    assert (
        await client.post(f"/next-steps/{sid}/replied", json={}, headers=H(owner))
    ).status_code == 409


async def test_team_leader_assigns_a_task_within_the_team_only(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng, owner=world.north1)
    body = {"text": "Chase the PO", "due_date": due(2), "responsible_id": str(world.north2.id)}
    # a rep cannot hand a task to someone else; a TL cannot reach the other team; an inactive person is refused
    for actor, who, code in (
        (world.north1, world.north2, 403),
        (world.tl_north, world.south1, 403),
        (world.tl_south, world.south1, 403),  # the deal is not in tl_south's team
    ):
        r = await client.post(
            f"/deals/{did}/next-step",
            json={**body, "responsible_id": str(who.id)},
            headers=H(actor),
        )
        assert r.status_code == code, (actor.name, who.name, r.status_code)
    r = await client.post(
        f"/deals/{did}/next-step",
        json={**body, "responsible_id": "00000000-0000-0000-0000-000000000000"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 404
    await run(eng, "UPDATE users SET is_active=false WHERE id=:u", u=world.north2.id)
    r = await client.post(f"/deals/{did}/next-step", json=body, headers=H(world.tl_north))
    assert r.status_code == 422
    await run(eng, "UPDATE users SET is_active=true WHERE id=:u", u=world.north2.id)
    r = await client.post(f"/deals/{did}/next-step", json=body, headers=H(world.tl_north))
    assert r.status_code == 204
    # the task is on the rep's Today with the TL's name; the rep is notified once
    t = (await client.get("/today", headers=H(world.north2))).json()
    mine = [x for x in t["your_move"] if x["deal_id"] == did]
    assert len(mine) == 1 and mine[0]["assigned_by"] == "Tara"
    await dispatch_all()
    await dispatch_all()
    got = [n for n in await notifications_for(world.north2.id) if n.type == "task.assigned"]
    assert len(got) == 1
    # and leaves the owner's own Your move
    assert did not in [
        x["deal_id"] for x in (await client.get("/today", headers=H(owner))).json()["your_move"]
    ]
    # an Admin can assign across teams (with a reason)
    r = await client.post(
        f"/deals/{did}/next-step",
        json={**body, "responsible_id": str(world.south1.id), "reason": "cover"},
        headers=H(world.admin),
    )
    assert r.status_code == 204
    assert not await check_invariants(eng, world.org_id)


async def test_newly_assigned_lead_is_on_todays_new_leads_until_first_activity(
    client, world, admin_engine
):
    did, owner = await mkdeal(client, world, admin_engine)
    await run(admin_engine, "UPDATE deals SET assigned_at=now() WHERE id=:d", d=did)
    t = (await client.get("/today", headers=H(owner))).json()
    assert did in [x["deal_id"] for x in t["new_leads"]] and t["counts"]["new_leads"] >= 1
    await run(
        admin_engine, "UPDATE deals SET assigned_at=now() - interval '4 days' WHERE id=:d", d=did
    )
    t = (await client.get("/today", headers=H(owner))).json()
    assert did not in [x["deal_id"] for x in t["new_leads"]]
    await run(
        admin_engine,
        "UPDATE deals SET assigned_at=now(), last_activity_at=now() WHERE id=:d",
        d=did,
    )
    t = (await client.get("/today", headers=H(owner))).json()
    assert did not in [x["deal_id"] for x in t["new_leads"]]


@pytest.mark.parametrize("who", ["north2", "tl_south", "south1"])
async def test_next_step_on_foreign_deal_is_refused_for_others(client, world, admin_engine, who):
    did, owner = await mkdeal(client, world, admin_engine)
    actor = getattr(world, who)
    r = await client.post(
        f"/deals/{did}/next-step", json={"text": "hijack", "due_date": due(1)}, headers=H(actor)
    )
    assert r.status_code == 403
    sid = await one(
        admin_engine, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did
    )
    for path in ("snooze", "replied", "nudge"):
        r = await client.post(f"/next-steps/{sid}/{path}", json={}, headers=H(actor))
        assert r.status_code in (403, 409), (path, r.status_code)
    assert set_owner is not None and owner is not None
