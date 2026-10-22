# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Close as Lost, reopen, delete and restore, bulk actions, next steps, health and Today."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.deals import health
from app.modules.deals.today import refresh_health
from tests.helpers import dispatch_all, headers, notifications_for
from tests.modules.journey import detail, due, log, move, new_deal, run_to
from tests.modules.test_leads_api import audit_keys, configure, create


async def lose(client, actor, did, expect=200, reason="Price", **kw):
    r = await client.post(
        f"/deals/{did}/lose", json={"lost_reason": reason, **kw}, headers=headers(actor)
    )
    assert r.status_code == expect, r.text
    return r


# ------------------------------------------------------------------------------ lost and reopen
async def test_lost_reason_from_the_list_and_who_may_close(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    await lose(client, owner, did, expect=422, reason="Because")  # not on the list
    await lose(client, world.tl_south, did, expect=403)
    r = await lose(client, owner, did, notes="Chose a rival")
    d = r.json()
    assert (
        d["status"] == "lost" and d["lost_reason"] == "Price" and d["lost_notes"] == "Chose a rival"
    )
    assert d["lost_from_stage"] == "Lead"
    await lose(client, owner, did, expect=409)  # already closed
    assert ("deal.lose", "ok") in await audit_keys(did)
    # a closed deal leaves the board but stays on the Lost page, with its history
    board = (await client.get("/board", headers=headers(owner))).json()
    assert did not in {c["id"] for col in board["columns"] for c in col["cards"]}
    page = (await client.get("/stages/lost/deals", headers=headers(owner))).json()
    assert page["rows"][0]["extra"]["lost_reason"] == "Price"


async def test_team_leader_closes_and_owner_is_told_reopen_goes_back_to_the_stage(
    client, world, admin_engine
):
    deal, owner = await run_to(client, world, admin_engine, "qualification")
    did = deal["id"]
    await lose(client, world.tl_north, did, reason="Timing")
    await dispatch_all()
    assert any(n.type == "deal.lost" for n in await notifications_for(owner.id))
    # a rep cannot reopen; a Team Leader can, with a reason
    r = await client.post(f"/deals/{did}/reopen", json={"reason": "x"}, headers=headers(owner))
    assert r.status_code == 403
    r = await client.post(f"/deals/{did}/reopen", json={}, headers=headers(world.tl_north))
    assert r.status_code == 422
    r = await client.post(
        f"/deals/{did}/reopen", json={"reason": "Customer is back"}, headers=headers(world.tl_north)
    )
    d = r.json()
    assert r.status_code == 200 and d["status"] == "open" and d["stage_key"] == "qualification"
    assert not any(c["ticked"] for c in d["progress"]["criteria"])  # a fresh checklist
    assert d["lost_reason"] is None
    await dispatch_all()
    assert any(n.type == "deal.reopened" for n in await notifications_for(owner.id))
    r = await client.post(
        f"/deals/{did}/reopen", json={"reason": "again"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 409


async def test_closing_a_deal_frees_the_cap_and_waiting_leads_get_assigned(client, world):
    await configure(client, world, cap=1)
    a = await create(client, world.north1)
    b = await create(client, world.north1)
    c = await create(client, world.north1)  # 2 reps x cap 1: the third waits
    assert c["status"] == "pooled"
    owner_a = world.north1 if a["owner"]["id"] == str(world.north1.id) else world.north2
    await lose(client, owner_a, a["id"])
    await dispatch_all()
    got = (await client.get(f"/deals/{c['id']}", headers=headers(world.admin))).json()
    assert got["status"] == "open" and got["owner"]["id"] == str(owner_a.id)
    _ = b


# ------------------------------------------------------------------------------ delete and restore
async def test_delete_and_restore_never_erase(client, world, admin_engine):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    for who in (owner, world.tl_south):
        r = await client.post(f"/deals/{did}/delete", json={"reason": "x"}, headers=headers(who))
        assert r.status_code in (403, 404)
    r = await client.post(f"/deals/{did}/delete", json={}, headers=headers(world.tl_north))
    assert r.status_code == 422
    r = await client.post(
        f"/deals/{did}/delete", json={"reason": "Test data"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 204
    assert (await client.get(f"/deals/{did}", headers=headers(owner))).status_code == 404
    gone = (await client.get("/deleted/deals", headers=headers(world.tl_north))).json()
    assert [d["id"] for d in gone] == [did] and gone[0]["deleted_reason"] == "Test data"
    assert (await client.get("/deleted/deals", headers=headers(owner))).status_code == 403
    assert (await client.get("/deleted/deals", headers=headers(world.tl_south))).json() == []
    async with admin_engine.begin() as c:  # the row is still there
        n = (
            await c.execute(text("SELECT count(*) FROM deals WHERE id=:d"), {"d": did})
        ).scalar_one()
    assert n == 1
    r = await client.post(
        f"/deals/{did}/restore",
        json={"reason": "Deleted by mistake"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200 and r.json()["id"] == did
    assert (await client.get(f"/deals/{did}", headers=headers(owner))).status_code == 200
    keys = await audit_keys(did)
    assert ("deal.delete", "ok") in keys and ("deal.restore", "ok") in keys


# ------------------------------------------------------------------------------ bulk
async def test_bulk_actions_report_each_deal(client, world, admin_engine):
    await configure(client, world)
    deals = [await create(client, world.north1) for _ in range(3)]
    other = await create(client, world.south1)
    async with admin_engine.begin() as c:  # a deal that belongs to the South team
        await c.execute(
            text("UPDATE deals SET owner_id=:u, team_id=:t WHERE id=:d"),
            {"u": world.south1.id, "t": world.south_team, "d": other["id"]},
        )
    ids = [d["id"] for d in deals] + [other["id"]]
    # a rep has no bulk rights
    r = await client.post(
        "/deals/bulk/lose",
        json={"deal_ids": ids, "lost_reason": "Price"},
        headers=headers(world.north1),
    )
    assert r.status_code == 200 and not r.json()["done"] and len(r.json()["failed"]) == 4
    # the North team leader can only touch North deals
    r = await client.post(
        "/deals/bulk/snooze",
        json={"deal_ids": ids, "reason": "holiday"},
        headers=headers(world.tl_north),
    )
    out = r.json()
    assert (
        len(out["done"]) == 3
        and len(out["failed"]) == 1
        and out["failed"][0]["deal_id"] == other["id"]
    )
    d = await detail(client, world.admin, deals[0]["id"])
    assert d["next_step"]["snooze_count"] == 1
    r = await client.post(
        "/deals/bulk/lose",
        json={"deal_ids": ids, "lost_reason": "Timing", "reason": "x"},
        headers=headers(world.tl_north),
    )
    assert len(r.json()["done"]) == 3
    # bulk owner change inside the team
    fresh = [await create(client, world.north1) for _ in range(2)]
    r = await client.post(
        "/deals/bulk/reassign",
        json={
            "deal_ids": [f["id"] for f in fresh],
            "user_id": str(world.north2.id),
            "reason": "balance",
        },
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200, r.text
    out = r.json()  # one of the two may already be theirs: that one is reported, not moved
    assert len(out["done"]) + len(out["failed"]) == 2
    assert all(f["code"] == "conflict" for f in out["failed"])
    owners = {(await detail(client, world.admin, f["id"]))["owner"]["id"] for f in fresh}
    assert owners == {str(world.north2.id)}
    # bulk stage moves do not exist for anyone
    assert (
        await client.post(
            "/deals/bulk/move",
            json={"deal_ids": ids, "to_stage": "qualification"},
            headers=headers(world.admin),
        )
    ).status_code in (404, 405, 422)


# ------------------------------------------------------------------------------ next steps
async def test_done_asks_for_the_next_step_and_snooze_is_counted(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    step = (await detail(client, owner, did))["next_step"]
    r = await client.post(f"/next-steps/{step['id']}/done", json={}, headers=headers(owner))
    assert r.status_code == 422  # the next step is required
    r = await client.post(
        f"/next-steps/{step['id']}/done",
        json={"next": {"text": "Send quote", "due_date": due(2), "lane": "waiting_on_them"}},
        headers=headers(owner),
    )
    assert r.status_code == 204
    d = await detail(client, owner, did)
    assert d["next_step"]["text"] == "Send quote" and d["next_step"]["lane"] == "waiting_on_them"
    sid = d["next_step"]["id"]
    for n in (1, 2):
        r = await client.post(f"/next-steps/{sid}/snooze", json={}, headers=headers(owner))
        assert r.status_code == 204
        assert (await detail(client, owner, did))["next_step"]["snooze_count"] == n
    assert (
        await client.post(f"/next-steps/{sid}/replied", json={}, headers=headers(owner))
    ).status_code == 204
    d = await detail(client, owner, did)
    assert d["next_step"]["lane"] == "your_move"
    assert (
        await client.post(f"/next-steps/{sid}/replied", json={}, headers=headers(owner))
    ).status_code == 409
    assert (
        await client.post(
            f"/next-steps/{sid}/snooze",
            json={},
            headers=headers(world.north2 if owner.id == world.north1.id else world.north1),
        )
    ).status_code in (403, 404)


async def test_team_leader_assigns_a_task_and_it_shows_on_the_reps_today(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    r = await client.post(
        f"/deals/{did}/next-step",
        json={"text": "Prepare the demo", "due_date": due(1), "responsible_id": str(owner.id)},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 204, r.text
    other_team = await client.post(
        f"/deals/{did}/next-step",
        json={"text": "x", "due_date": due(1), "responsible_id": str(world.south1.id)},
        headers=headers(world.tl_north),
    )
    assert other_team.status_code == 403
    await dispatch_all()
    today = (await client.get("/today", headers=headers(owner))).json()
    assert [r["text"] for r in today["your_move"]] == ["Prepare the demo"]
    assert today["your_move"][0]["assigned_by"] is None  # owner is also the responsible person
    # assigning to someone who is not the owner records who asked
    other = world.north2 if owner.id == world.north1.id else world.north1
    r = await client.post(
        f"/deals/{did}/next-step",
        json={"text": "Cover the call", "due_date": due(1), "responsible_id": str(other.id)},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 204
    await dispatch_all()
    assert any(n.type == "task.assigned" for n in await notifications_for(other.id))
    t2 = (await client.get("/today", headers=headers(other))).json()
    assert t2["your_move"][0]["assigned_by"] == world.tl_north.name


async def test_who_can_open_whose_today(client, world):
    deal, owner = await new_deal(client, world)
    mate = world.north2 if owner.id == world.north1.id else world.north1
    assert (
        await client.get("/today", params={"user_id": str(mate.id)}, headers=headers(owner))
    ).status_code == 404
    assert (
        await client.get(
            "/today", params={"user_id": str(owner.id)}, headers=headers(world.tl_north)
        )
    ).status_code == 200
    assert (
        await client.get(
            "/today", params={"user_id": str(owner.id)}, headers=headers(world.tl_south)
        )
    ).status_code == 404
    assert (
        await client.get("/today", params={"user_id": str(owner.id)}, headers=headers(world.admin))
    ).status_code == 200
    assert (
        await client.get(
            "/today", params={"user_id": str(world.sa.id)}, headers=headers(world.tl_north)
        )
    ).status_code == 404
    assert (
        await client.get(
            "/today", params={"user_id": str(world.sa.id)}, headers=headers(world.admin)
        )
    ).status_code == 200
    new = (await client.get("/today", headers=headers(owner))).json()
    assert [r["deal_id"] for r in new["new_leads"]] == [
        deal["id"]
    ]  # a newly assigned lead shows up


# ------------------------------------------------------------------------------ health
def _deal(**kw):
    base = dict(status="open", last_activity_at=None, assigned_at=None, created_at=None,
                expected_close_date=None, health="on_track", flags={})  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def _step(due_date, lane="your_move", since=None):
    return SimpleNamespace(due_date=due_date, lane=lane, lane_since=since)


def utc(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_stale_counts_ist_calendar_days_across_midnight():
    last = utc(2026, 3, 1, 18, 29)  # 23:59 IST on 1 March
    d = _deal(last_activity_at=last, created_at=last)
    # 23:59 IST on 8 March: seven IST days later -> stale
    assert health.compute(d, None, utc(2026, 3, 8, 18, 29))[0] == "stale"
    # 22:00 IST on 7 March: still six IST days -> not stale
    assert health.compute(d, None, utc(2026, 3, 7, 16, 30))[0] == "on_track"
    last2 = utc(2026, 3, 1, 18, 31)  # 00:01 IST on 2 March
    d2 = _deal(last_activity_at=last2, created_at=last2)
    assert (
        health.compute(d2, None, utc(2026, 3, 8, 18, 29))[0] == "on_track"
    )  # 23:59 IST on 8th: 6 days
    assert (
        health.compute(d2, None, utc(2026, 3, 8, 18, 31))[0] == "stale"
    )  # 00:01 IST on 9th: 7 days


def test_nudge_at_two_days_and_at_risk_when_overdue():
    from datetime import date

    since = utc(2026, 3, 1, 18, 29)  # 23:59 IST on 1 March
    d = _deal(last_activity_at=utc(2026, 3, 2, 0, 0), created_at=since)
    waiting = _step(date(2026, 3, 10), "waiting_on_them", since)
    assert health.compute(d, waiting, utc(2026, 3, 3, 18, 29))[1]["nudge_due"] is True
    assert (
        health.compute(d, waiting, utc(2026, 3, 3, 18, 0))[1]["nudge_due"] is True
    )  # 23:30 IST on the 3rd
    assert (
        health.compute(d, waiting, utc(2026, 3, 2, 18, 0))[1]["nudge_due"] is False
    )  # 23:30 IST on the 2nd: one IST day
    overdue = _step(date(2026, 3, 2))
    assert (
        health.compute(d, overdue, utc(2026, 3, 2, 12, 0))[0] == "on_track"
    )  # due today is not overdue
    assert (
        health.compute(d, overdue, utc(2026, 3, 2, 18, 31))[0] == "at_risk"
    )  # 00:01 IST on the 3rd
    # stale outranks at risk
    old = _deal(last_activity_at=utc(2026, 2, 1, 0, 0), created_at=utc(2026, 2, 1, 0, 0))
    assert health.compute(old, overdue, utc(2026, 3, 5, 0, 0))[0] == "stale"
    # slipped: the close date has passed
    s = _deal(
        last_activity_at=utc(2026, 3, 4, 0, 0),
        created_at=since,
        expected_close_date=date(2026, 3, 1),
    )
    assert health.compute(s, None, utc(2026, 3, 5, 0, 0))[1]["slipped"] is True
    assert health.compute(_deal(status="won"), None, utc(2026, 3, 5, 0, 0)) == (
        "on_track",
        {"nudge_due": False, "slipped": False},
    )


async def test_daily_refresh_flags_stale_deals_and_sends_one_digest(client, world, admin_engine):
    deal, owner = await new_deal(client, world)
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE deals SET last_activity_at = now() - interval '9 days', assigned_at = now() - interval '9 days' WHERE id=:d"
            ),
            {"d": deal["id"]},
        )
    out = await refresh_health(get_sessionmaker())
    assert out["changed"] >= 1
    d = await detail(client, owner, deal["id"])
    assert d["health"] == "stale"
    await dispatch_all()
    notes = [n for n in await notifications_for(owner.id) if n.type == "deal.stale"]
    assert len(notes) == 1
    await refresh_health(get_sessionmaker())  # running again the same day adds nothing
    await dispatch_all()
    assert len([n for n in await notifications_for(owner.id) if n.type == "deal.stale"]) == 1
    today = (await client.get("/today", headers=headers(owner))).json()
    assert [r["deal_id"] for r in today["stale"]] == [deal["id"]]
    assert datetime.now(UTC) - timedelta(days=0) is not None
    _ = (log, move)
