# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 5: activities and structured interaction notes."""

from datetime import UTC, datetime, timedelta

import pytest

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

T0 = datetime(2026, 5, 4, 8, 30, 0, tzinfo=UTC)


def body(type_="call", **kw):
    b = {"type": type_, "two_way": True, "discussed": "Talked it through", "next_step_text": "Follow up", "next_step_due_date": due(2)}  # fmt: skip
    b.update(kw)
    return b


async def post(client, actor, did, expect=201, **kw):
    r = await client.post(f"/deals/{did}/activities", json=body(**kw), headers=H(actor))
    assert r.status_code == expect, (r.status_code, r.text[:300])
    return r


# ============================================================================= validation
@pytest.mark.parametrize("bad", ["phone", "CALL", "", "visit", "walk-through"])
async def test_type_list(client, world, admin_engine, bad):
    did, owner = await mkdeal(client, world, admin_engine)
    await post(client, owner, did, 422, type_=bad)


@pytest.mark.parametrize("type_", ["call", "email", "meeting"])
async def test_two_way_is_required_for_calls_emails_meetings(client, world, admin_engine, type_):
    did, owner = await mkdeal(client, world, admin_engine)
    r = await client.post(
        f"/deals/{did}/activities", json={**body(type_), "two_way": None}, headers=H(owner)
    )
    assert r.status_code == 422 and "responded" in r.text
    r = await client.post(
        f"/deals/{did}/activities",
        json={k: v for k, v in body(type_).items() if k != "two_way"},
        headers=H(owner),
    )
    assert r.status_code == 422
    await post(client, owner, did, two_way=False, type_=type_)  # "no" is an answer


@pytest.mark.parametrize("type_", ["discovery", "walkthrough", "kickoff", "note"])
async def test_two_way_is_not_needed_for_other_types_and_is_false(
    client, world, admin_engine, type_
):
    did, owner = await mkdeal(client, world, admin_engine)
    r = await client.post(
        f"/deals/{did}/activities", json={**body(type_), "two_way": None}, headers=H(owner)
    )
    assert r.status_code == 201 and r.json()["two_way"] is False
    r = await post(client, owner, did, type_=type_, two_way=True)
    assert r.json()["two_way"] is False  # the flag is ignored for types that cannot be two-way


@pytest.mark.parametrize("disc", ["", "   ", "\n\t "])
async def test_discussed_is_required(client, world, admin_engine, disc):
    did, owner = await mkdeal(client, world, admin_engine)
    await post(client, owner, did, 422, discussed=disc)


async def test_next_step_is_required_and_not_in_the_past(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    for patchit in (
        {"next_step_text": ""}, {"next_step_text": "   "}, {"next_step_text": "x" * 501},
        {"next_step_due_date": due(-1)}, {"next_step_due_date": "not-a-date"}, {"next_step_due_date": None},
        {"next_step_lane": "later"}, {"next_step_responsible_id": "nope"}, {"unknown_field": 1},
    ):  # fmt: skip
        r = await client.post(
            f"/deals/{did}/activities", json={**body(), **patchit}, headers=H(owner)
        )
        assert r.status_code == 422, patchit
    for key in ("next_step_text", "next_step_due_date"):
        r = await client.post(
            f"/deals/{did}/activities",
            json={k: v for k, v in body().items() if k != key},
            headers=H(owner),
        )
        assert r.status_code == 422, key
    await post(client, owner, did, next_step_due_date=due(0))  # today
    await post(client, owner, did, next_step_text="x" * 500)
    assert (
        await one(
            admin_engine,
            "SELECT count(*) FROM next_steps WHERE deal_id=:d AND done_at IS NULL",
            d=did,
        )
        == 1
    )
    # a rejected save leaves the old next step in place
    sid = await one(
        admin_engine, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did
    )
    await post(client, owner, did, 422, next_step_due_date=due(-5))
    assert (
        await one(
            admin_engine, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did
        )
        == sid
    )


async def test_future_dates_only_for_meetings_with_a_five_minute_slack(
    client, world, admin_engine, monkeypatch
):
    did, owner = await mkdeal(client, world, admin_engine)
    import app.modules.activities.service as svc

    fixed = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(svc, "utc_now", lambda: fixed)
    for type_ in ("call", "email", "discovery", "walkthrough", "kickoff", "note"):
        await post(
            client,
            owner,
            did,
            422,
            type_=type_,
            occurred_at=(fixed + timedelta(days=1)).isoformat(),
        )
        await post(
            client, owner, did, type_=type_, occurred_at=(fixed + timedelta(minutes=5)).isoformat()
        )  # exactly the slack
        await post(
            client,
            owner,
            did,
            422,
            type_=type_,
            occurred_at=(fixed + timedelta(minutes=5, microseconds=1)).isoformat(),
        )
        await post(
            client, owner, did, type_=type_, occurred_at=(fixed - timedelta(days=400)).isoformat()
        )
    for days in (1, 30, 365):
        await post(
            client,
            owner,
            did,
            type_="meeting",
            occurred_at=(fixed + timedelta(days=days)).isoformat(),
        )
    await post(client, owner, did, 422, occurred_at="2026-13-45T00:00:00Z")
    # naive timestamps are read as UTC, others converted
    r = await post(client, owner, did, occurred_at="2026-01-01T10:00:00+05:30")
    assert r.json()["occurred_at"].startswith("2026-01-01T04:30:00")


async def test_unicode_injection_and_large_text_are_stored_verbatim(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    nasty = [
        "'; DROP TABLE activities; --",
        "<script>alert(1)</script>",
        "नमस्ते 🙏 café ☕ ‮ rtl",
        "=cmd|' /C calc'!A0",
        "a" * 200_000,
        "line1\nline2\r\n\ttab",
    ]
    for text in nasty:
        r = await post(
            client, owner, did, discussed=text, requirements=text[:500], objections=text[:100]
        )
        assert r.json()["discussed"] == text.strip()
    assert await one(
        admin_engine, "SELECT count(*) FROM activities WHERE deal_id=:d", d=did
    ) >= len(nasty)


# ============================================================================ who logs, attribution
async def test_logging_rights_and_attribution(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    for actor in (world.north2, world.south1, world.tl_south):
        await post(client, actor, did, 403)
    mark = await max_seq(eng)
    r = await post(client, world.tl_north, did)  # team leader on a team deal: attributed to the TL
    assert r.json()["attributed_to_name"] == "Tara" and r.json()["created_by_name"] == "Tara"
    await client.post(f"/deals/{did}/activities", json=body(), headers=H(world.admin))
    r = await client.post(f"/deals/{did}/activities", json=body(), headers=H(world.admin))
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await post(client, world.admin, did, reason="called for the rep")
    assert r.json()["attributed_to_name"] == "Anita"
    r = await post(client, world.sa, did)
    assert r.json()["attributed_to_name"] == "Samar"
    a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert [x["action_key"] for x in a] == ["activity.log"] * 3
    assert a[1]["reason"] == "called for the rep"
    # responsible person for the next step defaults to the owner, can be a team mate for a TL
    sid = await one(
        eng, "SELECT responsible_id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did
    )
    assert sid == world.sa.id or sid == owner.id  # whoever logged last, never empty
    # activity on an unknown / foreign deal
    await post(client, owner, "00000000-0000-0000-0000-000000000000", 404)


async def test_activities_on_won_open_lost_pooled_deleted_deals(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    await post(client, owner, did)
    await force_stage(
        eng, world.org_id, did, "onboarding", ticker=owner
    )  # won, kickoff happens here
    await post(client, owner, did, type_="kickoff")
    await client.post(
        f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(world.admin)
    )  # won deals cannot be lost
    d2, o2 = await mkdeal(client, world, eng)
    await client.post(f"/deals/{d2}/lose", json={"lost_reason": "Price"}, headers=H(o2))
    r = await post(client, o2, d2, 409)
    assert r.json()["code"] == "conflict"
    d3, o3 = await mkdeal(client, world, eng)
    await run(eng, "UPDATE deals SET status='pooled' WHERE id=:d", d=d3)
    await post(client, o3, d3, 409)
    await client.post(f"/deals/{d3}/delete", json={"reason": "dup"}, headers=H(world.tl_north))
    await post(client, o3, d3, 404)
    # reading the activities of a lost deal still works
    assert (await client.get(f"/deals/{d2}/activities", headers=H(o2))).status_code == 200


# ============================================================================== the 24-hour window
async def _logged(client, eng, actor_deal_owner, did, reason=None):
    r = await post(client, actor_deal_owner, did, **({"reason": reason} if reason else {}))
    return r.json()["id"]


@pytest.mark.parametrize(
    "delta,allowed_",
    [
        (timedelta(hours=23, minutes=59), True),
        (timedelta(hours=24), True),
        (timedelta(hours=24, microseconds=1), False),
        (timedelta(hours=24, minutes=1), False),
        (timedelta(days=30), False),
    ],
)
@pytest.mark.parametrize("who", ["user", "tl"])
async def test_edit_window_for_user_and_team_leader(
    client, world, admin_engine, monkeypatch, delta, allowed_, who
):
    eng = admin_engine
    import app.modules.activities.service as svc

    actor = world.north1 if who == "user" else world.tl_north
    did, owner = await mkdeal(client, world, eng, owner=actor)
    aid = await _logged(client, eng, actor, did)
    await run(eng, "UPDATE activities SET created_at=:t WHERE id=:a", t=T0, a=aid)
    monkeypatch.setattr(svc, "utc_now", lambda: T0 + delta)
    r = await client.patch(
        f"/activities/{aid}", json={"discussed": "edited", "reason": "typo"}, headers=H(actor)
    )
    assert r.status_code == (200 if allowed_ else 403), (who, delta, r.status_code, r.text[:100])
    if not allowed_:
        assert "24-hour" in r.json()["message"]


async def test_admin_and_super_admin_edit_their_own_without_the_window(
    client, world, admin_engine, monkeypatch
):
    eng = admin_engine
    import app.modules.activities.service as svc

    for actor in (world.admin, world.sa):
        did, _o = await mkdeal(client, world, eng)
        aid = (
            await post(client, actor, did, **({"reason": "r"} if actor is world.admin else {}))
        ).json()["id"]
        await run(eng, "UPDATE activities SET created_at=:t WHERE id=:a", t=T0, a=aid)
        monkeypatch.setattr(svc, "utc_now", lambda: T0 + timedelta(days=400))
        r = await client.patch(
            f"/activities/{aid}", json={"discussed": "years later", "reason": "r"}, headers=H(actor)
        )
        assert r.status_code == 200, (actor.name, r.text[:100])
        monkeypatch.undo()


async def test_nobody_edits_somebody_elses_activity(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    mine = (await post(client, owner, did)).json()["id"]
    theirs = (await post(client, world.tl_north, did)).json()["id"]
    for actor in (
        world.north2,
        world.south1,
        world.tl_south,
        world.tl_north,
        world.admin,
        world.sa,
    ):
        r = await client.patch(
            f"/activities/{mine}", json={"discussed": "hijacked", "reason": "x"}, headers=H(actor)
        )
        assert r.status_code == 403, (actor.name, r.status_code)
    r = await client.patch(
        f"/activities/{theirs}", json={"discussed": "hijacked", "reason": "x"}, headers=H(owner)
    )
    assert r.status_code == 403
    assert (
        await one(eng, "SELECT discussed FROM activities WHERE id=:a", a=mine)
        == "Talked it through"
    )
    assert (
        await one(
            eng,
            "SELECT count(*) FROM audit_log WHERE entity_id=:d AND action_key='activity.edit_own' AND outcome='denied'",
            d=str(did),
        )
        >= 7
    )


async def test_edit_reasons_versions_and_validation(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    aid = (await post(client, owner, did, attendees=["A"], requirements="r1")).json()["id"]
    url = f"/activities/{aid}"
    r = await client.patch(url, json={"discussed": "v2"}, headers=H(owner))
    assert (
        r.status_code == 422 and r.json()["code"] == "reason_required"
    )  # editing within 24 h still needs a reason
    for bad in ({}, {"reason": "x"}, {"discussed": "", "reason": "x"}, {"discussed": "   ", "reason": "x"}, {"two_way": None, "reason": "x"},
                {"occurred_at": None, "reason": "x"}, {"occurred_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(), "reason": "x"},
                {"type": "meeting", "reason": "x"}, {"created_by": str(world.sa.id), "reason": "x"}, {"attendees": "A", "reason": "x"}):  # fmt: skip
        r = await client.patch(url, json=bad, headers=H(owner))
        assert r.status_code == 422, bad
    mark = await max_seq(eng)
    r = await client.patch(url, json={"discussed": "v2", "reason": "typo"}, headers=H(owner))
    assert r.status_code == 200 and r.json()["version_no"] == 2 and r.json()["edited"] is True
    # the same values again: nothing happens (no new version, no audit)
    r = await client.patch(url, json={"discussed": "v2", "reason": "again"}, headers=H(owner))
    assert r.status_code == 200 and r.json()["version_no"] == 2
    r = await client.patch(
        url,
        json={"attendees": ["A", "B"], "two_way": False, "reason": "more people"},
        headers=H(owner),
    )
    assert r.json()["version_no"] == 3
    a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert [x["action_key"] for x in a] == ["activity.edit_own"] * 2
    assert a[0]["old_value"] == {"discussed": "Talked it through"} and a[0]["new_value"] == {
        "discussed": "v2"
    }
    vs = (await client.get(f"/activities/{aid}/versions", headers=H(owner))).json()
    assert [v["version_no"] for v in vs] == [1, 2, 3]
    assert vs[0]["body"]["discussed"] == "Talked it through" and vs[1]["body"]["discussed"] == "v2"
    assert vs[2]["body"]["attendees"] == ["A", "B"] and vs[2]["reason"] == "more people"
    assert vs[0]["edited_by_name"] == owner.name
    # readable by the team leader and Admin, not by an unrelated rep or another team
    assert (
        await client.get(f"/activities/{aid}/versions", headers=H(world.tl_north))
    ).status_code == 200
    assert (
        await client.get(f"/activities/{aid}/versions", headers=H(world.admin))
    ).status_code == 200
    for actor in (world.north2, world.south1, world.tl_south):
        assert (
            await client.get(f"/activities/{aid}/versions", headers=H(actor))
        ).status_code == 404
    assert (
        await client.get(
            "/activities/00000000-0000-0000-0000-000000000000/versions", headers=H(world.sa)
        )
    ).status_code == 404
    # the Super admin edits without a reason
    sa_act = (await post(client, world.sa, did)).json()["id"]
    assert (
        await client.patch(
            f"/activities/{sa_act}", json={"discussed": "sa edit"}, headers=H(world.sa)
        )
    ).status_code == 200


async def test_delete_rights_reason_and_effect(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    a1 = (await post(client, owner, did)).json()["id"]
    for actor in (owner, world.north2, world.south1, world.tl_south):
        r = await client.delete(f"/activities/{a1}", params={"reason": "x"}, headers=H(actor))
        assert r.status_code == 403, actor.name
    assert (
        await client.delete(f"/activities/{a1}", headers=H(world.tl_north))
    ).status_code == 422  # reason
    assert (
        await client.delete(f"/activities/{a1}", params={"reason": "  "}, headers=H(world.tl_north))
    ).status_code == 422
    assert (await client.delete(f"/activities/{a1}", headers=H(world.admin))).status_code == 422
    mark = await max_seq(eng)
    assert (
        await client.delete(
            f"/activities/{a1}", params={"reason": "duplicate"}, headers=H(world.tl_north)
        )
    ).status_code == 204
    a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert len(a) == 1 and a[0]["action_key"] == "activity.delete" and a[0]["reason"] == "duplicate"
    # gone from the list, kept in storage, history still readable
    assert a1 not in [
        x["id"] for x in (await client.get(f"/deals/{did}/activities", headers=H(owner))).json()
    ]
    assert await one(eng, "SELECT deleted_at IS NOT NULL FROM activities WHERE id=:a", a=a1)
    assert (
        await client.delete(
            f"/activities/{a1}", params={"reason": "again"}, headers=H(world.tl_north)
        )
    ).status_code == 404
    assert (
        await client.patch(
            f"/activities/{a1}", json={"discussed": "x", "reason": "x"}, headers=H(owner)
        )
    ).status_code == 404
    a2 = (await post(client, owner, did)).json()["id"]
    assert (
        await client.delete(f"/activities/{a2}", headers=H(world.sa))
    ).status_code == 204  # SA: no reason
    # the app role cannot erase activities
    assert (
        await one(eng, "SELECT count(*) FROM activities WHERE deal_id=:d", d=did) == 2
    )  # nothing erased


async def test_last_activity_at_follows_the_activities(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    q = lambda: one(eng, "SELECT last_activity_at FROM deals WHERE id=:d", d=did)  # noqa: E731
    assert await q() is None
    now = datetime.now(UTC)
    old = (
        await post(client, owner, did, occurred_at=(now - timedelta(days=3)).isoformat())
    ).json()["id"]
    assert abs((await q() - (now - timedelta(days=3))).total_seconds()) < 1
    # a note does not count as activity (health "stale" ignores notes)
    await post(client, owner, did, type_="note", two_way=False)
    assert abs((await q() - (now - timedelta(days=3))).total_seconds()) < 1
    # a meeting booked for later does not count yet
    await post(
        client, owner, did, type_="meeting", occurred_at=(now + timedelta(days=5)).isoformat()
    )
    assert abs((await q() - (now - timedelta(days=3))).total_seconds()) < 1
    newer = (
        await post(client, owner, did, occurred_at=(now - timedelta(days=1)).isoformat())
    ).json()["id"]
    assert abs((await q() - (now - timedelta(days=1))).total_seconds()) < 1
    # edit the date of the newest: it moves; delete it: falls back to the older one
    await client.patch(
        f"/activities/{newer}",
        json={"occurred_at": (now - timedelta(days=2)).isoformat(), "reason": "typo"},
        headers=H(owner),
    )
    assert abs((await q() - (now - timedelta(days=2))).total_seconds()) < 1
    await client.delete(f"/activities/{newer}", params={"reason": "dup"}, headers=H(world.tl_north))
    assert abs((await q() - (now - timedelta(days=3))).total_seconds()) < 1
    await client.delete(f"/activities/{old}", params={"reason": "dup"}, headers=H(world.tl_north))
    assert await q() is None
    assert not await check_invariants(eng, world.org_id)


async def test_activity_idor_and_listing(client, world, admin_engine):
    eng = admin_engine
    d1, o1 = await mkdeal(client, world, eng)
    d2, o2 = await mkdeal(client, world, eng, owner=world.south1)
    a1 = (await post(client, o1, d1)).json()["id"]
    assert (await client.get(f"/deals/{d1}/activities", headers=H(world.south1))).status_code == 404
    assert (
        await client.get(f"/deals/{d1}/activities", headers=H(world.tl_south))
    ).status_code == 404
    assert (
        await client.patch(
            f"/activities/{a1}", json={"discussed": "x", "reason": "x"}, headers=H(o2)
        )
    ).status_code in (403, 404)
    assert (
        await client.delete(f"/activities/{a1}", params={"reason": "x"}, headers=H(world.tl_south))
    ).status_code in (403, 404)
    # the Super admin's own deal is invisible to a team leader and a rep
    await set_owner(eng, d1, world.sa)
    for actor in (o1, world.tl_north):
        assert (await client.get(f"/deals/{d1}/activities", headers=H(actor))).status_code == 404
    assert (await client.get(f"/deals/{d1}/activities", headers=H(world.admin))).status_code == 200
    rows_ = (await client.get(f"/deals/{d2}/activities", headers=H(o2))).json()
    assert rows_ == []
    assert rows is not None


async def test_BUG_P3_17_nul_character_in_free_text(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    r = await client.post(
        f"/deals/{did}/activities", json=body(discussed="bad\x00text"), headers=H(owner)
    )
    assert r.status_code in (201, 422), r.status_code
