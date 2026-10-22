# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 6 (part 1): deal health across IST midnight, the daily refresh job,
and the Today view."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.db import get_sessionmaker
from app.core.time import utc_now
from app.modules.deals import health
from app.modules.deals.health import compute, ist_date
from app.modules.deals.today import refresh_health
from tests.deep.phase3.p3 import H, due, mkdeal, one, rows, run, set_owner
from tests.helpers import dispatch_all, notifications_for


def D(last=None, assigned=None, created=None, status="open", close=None):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    return SimpleNamespace(
        status=status,
        last_activity_at=last,
        assigned_at=assigned,
        created_at=created or t0,
        expected_close_date=close,
        health="on_track",
        flags={},
    )


def S(due_date=None, lane="your_move", since=None):
    return SimpleNamespace(
        due_date=due_date, lane=lane, lane_since=since or datetime(2026, 1, 1, tzinfo=UTC)
    )


def ist(y, m, d, hh=0, mm=0, ss=0, us=0):
    """An IST wall-clock time as an aware UTC datetime."""
    return (datetime(y, m, d, hh, mm, ss, us) - timedelta(hours=5, minutes=30)).replace(tzinfo=UTC)


# ================================================================== pure functions at IST midnight
@pytest.mark.parametrize(
    "now,expect_stale",
    [
        (ist(2026, 4, 1, 23, 59, 59), True),  # 7 IST days after Mar 25
        (ist(2026, 3, 31, 23, 59, 59), False),  # still 6 IST days after Mar 25
        (ist(2026, 4, 1, 0, 0, 0), True),
        (ist(2026, 3, 31, 23, 59, 59, 999999), False),
    ],
)
def test_stale_flips_at_ist_midnight_not_utc_midnight(now, expect_stale):
    deal = D(last=ist(2026, 3, 25, 23, 59, 59), assigned=ist(2026, 3, 1))
    assert (compute(deal, S(due_date=date(2026, 12, 31)), now)[0] == "stale") is expect_stale


def test_stale_day_counts_use_ist_calendar_days_not_24_hour_blocks():
    # activity at 00:00:01 IST on the 1st; 7 calendar days later at 00:00:00 IST is already stale
    deal = D(last=ist(2026, 3, 1, 0, 0, 1))
    assert compute(deal, None, ist(2026, 3, 8, 0, 0, 0))[0] == "stale"
    assert compute(deal, None, ist(2026, 3, 7, 23, 59, 59))[0] == "on_track"
    # activity at 23:59:59 IST on the 1st is "1 Mar": stale at 00:00 on the 8th, a second later
    deal = D(last=ist(2026, 3, 1, 23, 59, 59))
    assert compute(deal, None, ist(2026, 3, 8, 0, 0, 0))[0] == "stale"


@pytest.mark.parametrize(
    "last,now",
    [
        (ist(2025, 12, 25, 12), ist(2026, 1, 1, 0, 0, 1)),  # year end
        (ist(2026, 1, 24, 12), ist(2026, 1, 31, 0, 0, 0)),  # month end
        (ist(2028, 2, 22, 12), ist(2028, 2, 29, 9)),  # leap day
        (ist(2027, 2, 21, 12), ist(2027, 2, 28, 9)),  # non-leap
    ],
)
def test_stale_across_month_year_and_leap_boundaries(last, now):
    assert compute(D(last=last), None, now)[0] == "stale"
    assert compute(D(last=last), None, now - timedelta(days=1))[0] == "on_track"


def test_overdue_is_at_risk_and_stale_wins():
    now = ist(2026, 3, 10, 12)
    fresh = D(last=ist(2026, 3, 9))
    assert compute(fresh, S(date(2026, 3, 10)), now)[0] == "on_track"  # due today is not overdue
    assert compute(fresh, S(date(2026, 3, 9)), now)[0] == "at_risk"
    assert compute(fresh, S(date(2026, 3, 9)), ist(2026, 3, 10, 0, 0, 0))[0] == "at_risk"
    assert compute(fresh, S(date(2026, 3, 10)), ist(2026, 3, 10, 23, 59, 59))[0] == "on_track"
    assert compute(fresh, S(date(2026, 3, 10)), ist(2026, 3, 11, 0, 0, 0))[0] == "at_risk"
    old = D(last=ist(2026, 2, 1))
    assert compute(old, S(date(2026, 3, 1)), now)[0] == "stale"  # both: stale outranks at risk
    assert compute(old, None, now)[0] == "stale"


def test_never_active_deal_ages_from_assignment_then_creation():
    now = ist(2026, 3, 10, 12)
    assert compute(D(assigned=ist(2026, 3, 3, 12)), None, now)[0] == "stale"
    assert compute(D(assigned=ist(2026, 3, 4, 12)), None, now)[0] == "on_track"
    assert compute(D(assigned=None, created=ist(2026, 3, 3, 12)), None, now)[0] == "stale"


def test_nudge_after_two_ist_days_and_only_in_the_waiting_lane():
    st = lambda lane, since: S(date(2027, 1, 1), lane, since)  # noqa: E731
    deal = D(last=ist(2026, 3, 30))
    f = lambda lane, since, now: compute(deal, st(lane, since), now)[1]["nudge_due"]  # noqa: E731
    assert f("waiting_on_them", ist(2026, 3, 30, 23, 59, 59), ist(2026, 4, 1, 0, 0, 0)) is True
    assert f("waiting_on_them", ist(2026, 3, 31, 0, 0, 0), ist(2026, 4, 1, 23, 59, 59)) is False
    assert f("waiting_on_them", ist(2026, 3, 31, 0, 0, 0), ist(2026, 4, 2, 0, 0, 0)) is True
    assert f("your_move", ist(2026, 3, 1), ist(2026, 4, 2)) is False
    # year boundary
    assert f("waiting_on_them", ist(2025, 12, 31, 20), ist(2026, 1, 2, 0, 0, 1)) is True
    assert f("waiting_on_them", ist(2025, 12, 31, 20), ist(2026, 1, 1, 23, 59, 59)) is False


def test_slipped_is_a_flag_not_a_health_and_only_for_open_deals():
    now = ist(2026, 3, 10, 12)
    d = D(last=ist(2026, 3, 9), close=date(2026, 3, 9))
    assert compute(d, S(date(2026, 3, 20)), now) == (
        "on_track",
        {"nudge_due": False, "slipped": True},
    )
    assert (
        compute(D(last=ist(2026, 3, 9), close=date(2026, 3, 10)), None, now)[1]["slipped"] is False
    )
    for status in ("won", "lost", "pooled"):
        assert compute(D(status=status, close=date(2020, 1, 1)), S(date(2020, 1, 1)), now) == (
            "on_track",
            {"nudge_due": False, "slipped": False},
        )


def test_no_sla_by_stage_a_year_in_a_stage_is_not_a_flag():
    d = D(last=ist(2026, 3, 9))
    assert compute(d, S(date(2030, 1, 1)), ist(2026, 3, 10))[0] == "on_track"
    d.stage_entered_at = ist(2025, 1, 1)
    assert compute(d, S(date(2030, 1, 1)), ist(2026, 3, 10))[0] == "on_track"


# ======================================================================================== the job
async def test_refresh_health_in_the_database_idempotent_with_one_digest_per_owner(
    client, world, admin_engine
):
    eng = admin_engine
    ids = {}
    for name, owner in (("a1", world.north1), ("a2", world.north1), ("b1", world.north2)):
        did, _ = await mkdeal(client, world, eng, owner=owner)
        ids[name] = did
    now = utc_now()
    today = ist_date(now)
    # a1: stale + nudge due; a2: overdue + slipped; b1: fine
    await run(
        eng,
        "UPDATE deals SET last_activity_at=:t WHERE id=:d",
        t=now - timedelta(days=9),
        d=ids["a1"],
    )
    await run(
        eng,
        "UPDATE next_steps SET lane='waiting_on_them', lane_since=:t, due_date=:u WHERE deal_id=:d AND done_at IS NULL",
        t=now - timedelta(days=3),
        u=today + timedelta(days=9),
        d=ids["a1"],
    )
    await run(
        eng,
        "UPDATE deals SET last_activity_at=:t, expected_close_date=:c WHERE id=:d",
        t=now - timedelta(days=1),
        c=today - timedelta(days=2),
        d=ids["a2"],
    )
    await run(
        eng,
        "UPDATE next_steps SET due_date=:u WHERE deal_id=:d AND done_at IS NULL",
        u=today - timedelta(days=1),
        d=ids["a2"],
    )
    await run(eng, "UPDATE deals SET last_activity_at=:t WHERE id=:d", t=now, d=ids["b1"])
    await run(
        eng, "UPDATE deals SET health='on_track', flags='{}'::jsonb WHERE org_id=:o", o=world.org_id
    )
    res = await refresh_health(get_sessionmaker(), now)
    assert res["changed"] >= 2
    digests = await rows(
        eng,
        "SELECT payload FROM outbox_events WHERE type='deal.digest' AND org_id=:o",
        o=world.org_id,
    )
    assert [d["payload"]["owner_id"] for d in digests] == [
        str(world.north1.id)
    ]  # only north1 has news
    got = {
        str(r["id"]): r
        for r in await rows(
            eng, "SELECT id, health, flags FROM deals WHERE org_id=:o", o=world.org_id
        )
    }
    assert got[ids["a1"]]["health"] == "stale" and got[ids["a1"]]["flags"].get("nudge_due") is True
    assert got[ids["a2"]]["health"] == "at_risk" and got[ids["a2"]]["flags"].get("slipped") is True
    assert got[ids["b1"]]["health"] == "on_track" and got[ids["b1"]]["flags"] == {}
    # second run: nothing changes
    res2 = await refresh_health(get_sessionmaker(), now)
    assert res2["changed"] == 0
    await dispatch_all()
    await dispatch_all()
    notes = await notifications_for(world.north1.id)
    assert len([n for n in notes if n.type == "nudge.due"]) == 1  # one digest per owner per day
    assert len([n for n in notes if n.type == "deal.stale"]) == 1
    assert not [
        n for n in await notifications_for(world.north2.id) if n.type in ("nudge.due", "deal.stale")
    ]
    # across IST midnight: a deal on track at 23:59:59 becomes at risk one second later
    await run(
        eng, "UPDATE deals SET last_activity_at=:t WHERE id=:d", t=ist(2026, 3, 10, 12), d=ids["b1"]
    )
    await run(
        eng,
        "UPDATE next_steps SET due_date='2026-03-10' WHERE deal_id=:d AND done_at IS NULL",
        d=ids["b1"],
    )
    await refresh_health(get_sessionmaker(), ist(2026, 3, 10, 23, 59, 59))
    assert await one(eng, "SELECT health FROM deals WHERE id=:d", d=ids["b1"]) == "on_track"
    await refresh_health(get_sessionmaker(), ist(2026, 3, 11, 0, 0, 0))
    assert await one(eng, "SELECT health FROM deals WHERE id=:d", d=ids["b1"]) == "at_risk"
    # won / lost / deleted deals are left alone by the job
    await run(
        eng,
        "UPDATE deals SET status='lost', lost_at=now(), lost_reason='Price', health='on_track' WHERE id=:d",
        d=ids["b1"],
    )
    await refresh_health(get_sessionmaker(), ist(2026, 4, 11, 0, 0, 0))
    assert await one(eng, "SELECT health FROM deals WHERE id=:d", d=ids["b1"]) == "on_track"


async def test_daily_digest_queues_one_event_per_owner_per_run(client, world, admin_engine):
    """Documented behaviour (NOTE): each run queues a new digest event; the notification
    dedupe key is what keeps it to one per owner per IST day."""
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    now = utc_now()
    await run(
        eng, "UPDATE deals SET last_activity_at=:t WHERE id=:d", t=now - timedelta(days=20), d=did
    )
    before = await one(
        eng,
        "SELECT count(*) FROM outbox_events WHERE type='deal.digest' AND org_id=:o",
        o=world.org_id,
    )
    await refresh_health(get_sessionmaker(), now)
    await refresh_health(get_sessionmaker(), now)
    after = await one(
        eng,
        "SELECT count(*) FROM outbox_events WHERE type='deal.digest' AND org_id=:o",
        o=world.org_id,
    )
    assert after - before == 2
    await dispatch_all()
    await dispatch_all()
    assert len([n for n in await notifications_for(owner.id) if n.type == "deal.stale"]) == 1


# ======================================================================================= Today
async def _today(client, actor, user=None, expect=200):
    r = await client.get(
        "/today", params={"user_id": str(user.id)} if user else {}, headers=H(actor)
    )
    assert r.status_code == expect, (
        actor.name,
        getattr(user, "name", None),
        r.status_code,
        r.text[:100],
    )
    return r.json()


async def test_today_scope_per_role(client, world, admin_engine):
    eng = admin_engine
    await mkdeal(client, world, eng, owner=world.north1)
    sa_deal, _ = await mkdeal(client, world, eng, owner=world.sa)
    # who may open whose Today
    table = {
        world.north1: {world.north1: 200, world.north2: 404, world.south1: 404, world.tl_north: 404, world.sa: 404},
        world.tl_north: {world.north1: 200, world.north2: 200, world.tl_north: 200, world.south1: 404, world.tl_south: 404, world.sa: 404, world.admin: 404},
        world.tl_south: {world.north1: 404, world.south1: 200, world.south2: 200},
        world.admin: {world.north1: 200, world.south1: 200, world.tl_north: 200, world.sa: 200, world.admin: 200},
        world.sa: {world.north1: 200, world.south1: 200, world.admin: 200, world.sa: 200},
    }  # fmt: skip
    for actor, targets in table.items():
        for target, code in targets.items():
            await _today(client, actor, target, code)
    # the Super admin's own Today shows his deals to Admins and the Super admin only
    t = await _today(client, world.admin, world.sa)
    assert sa_deal in [x["deal_id"] for x in t["your_move"]]
    assert (
        await client.get(
            "/today",
            params={"user_id": "00000000-0000-0000-0000-000000000000"},
            headers=H(world.admin),
        )
    ).status_code == 404
    assert (
        await client.get("/today", params={"user_id": "nope"}, headers=H(world.admin))
    ).status_code == 422
    # a deal owned by the Super admin never shows on a TL's/rep's view even when a task points at them
    await run(
        eng,
        "UPDATE next_steps SET responsible_id=:u WHERE deal_id=:d AND done_at IS NULL",
        u=world.north1.id,
        d=sa_deal,
    )
    for actor in (world.north1, world.tl_north):
        t = await _today(client, actor, world.north1)
        assert sa_deal not in [x["deal_id"] for x in t["your_move"]]
    assert (await _today(client, world.admin, world.north1))["your_move"] and sa_deal in [
        x["deal_id"] for x in (await _today(client, world.admin, world.north1))["your_move"]
    ]


async def test_today_sections_and_counts_equal_sql(client, world, admin_engine):
    eng = admin_engine
    rep = world.north1
    today = ist_date(utc_now())
    now = utc_now()
    made = {}
    for key in (
        "overdue",
        "due_today",
        "future",
        "waiting",
        "waiting_nudge",
        "stale",
        "newlead",
        "lost",
        "deleted",
        "pooled",
        "other_rep",
    ):
        did, _ = await mkdeal(client, world, eng, owner=world.north2 if key == "other_rep" else rep)
        made[key] = did
    q = lambda sql, **kw: run(eng, sql, **kw)  # noqa: E731
    await q(
        "UPDATE next_steps SET due_date=:u WHERE deal_id=:d AND done_at IS NULL",
        u=today - timedelta(days=2),
        d=made["overdue"],
    )
    await q(
        "UPDATE next_steps SET due_date=:u WHERE deal_id=:d AND done_at IS NULL",
        u=today,
        d=made["due_today"],
    )
    await q(
        "UPDATE next_steps SET due_date=:u WHERE deal_id=:d AND done_at IS NULL",
        u=today + timedelta(days=5),
        d=made["future"],
    )
    await q(
        "UPDATE next_steps SET lane='waiting_on_them', due_date=:u WHERE deal_id=:d AND done_at IS NULL",
        u=today + timedelta(days=5),
        d=made["waiting"],
    )
    await q(
        "UPDATE next_steps SET lane='waiting_on_them', lane_since=:t, due_date=:u WHERE deal_id=:d AND done_at IS NULL",
        t=now - timedelta(days=3),
        u=today + timedelta(days=5),
        d=made["waiting_nudge"],
    )
    await q(
        "UPDATE deals SET last_activity_at=:t WHERE id=:d",
        t=now - timedelta(days=30),
        d=made["stale"],
    )
    await q("UPDATE deals SET assigned_at=now() WHERE id=:d", d=made["newlead"])
    await q(
        "UPDATE deals SET status='lost', lost_at=now(), lost_reason='Price' WHERE id=:d",
        d=made["lost"],
    )
    await q("UPDATE deals SET deleted_at=now() WHERE id=:d", d=made["deleted"])
    await q("UPDATE deals SET status='pooled' WHERE id=:d", d=made["pooled"])
    for k in ("overdue", "due_today", "future", "waiting", "waiting_nudge", "stale", "newlead"):
        await q(
            "UPDATE deals SET last_activity_at=COALESCE(last_activity_at, now()) WHERE id=:d AND :k <> 'newlead'",
            d=made[k],
            k=k,
        )
    await refresh_health(get_sessionmaker(), now)
    t = await _today(client, rep)
    ids = lambda sec: {x["deal_id"] for x in t[sec]}  # noqa: E731
    sql_mine = {
        str(r["deal_id"]): r
        for r in await rows(
            eng,
            "SELECT n.deal_id, n.lane, n.due_date FROM next_steps n JOIN deals d ON d.id=n.deal_id WHERE n.responsible_id=:u AND n.done_at IS NULL AND d.status='open' AND d.deleted_at IS NULL",
            u=rep.id,
        )
    }
    assert ids("your_move") == {k for k, v in sql_mine.items() if v["lane"] == "your_move"}
    assert ids("waiting_on_them") == {
        k for k, v in sql_mine.items() if v["lane"] == "waiting_on_them"
    }
    for gone in ("lost", "deleted", "pooled", "other_rep"):
        assert made[gone] not in ids("your_move") | ids("waiting_on_them")
    c = t["counts"]
    assert c["your_move"] == len(ids("your_move")) and c["waiting"] == len(ids("waiting_on_them"))
    assert (
        c["overdue"]
        == sum(1 for v in sql_mine.values() if v["lane"] == "your_move" and v["due_date"] < today)
        == 1
    )
    assert c["nudge_due"] == 1 and made["waiting_nudge"] in ids("waiting_on_them")
    stale_sql = await one(
        eng,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND health='stale' AND status='open' AND deleted_at IS NULL",
        u=rep.id,
    )
    assert c["stale"] == stale_sql and made["stale"] in ids("stale")
    new_sql = await one(
        eng,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND status='open' AND deleted_at IS NULL AND last_activity_at IS NULL AND assigned_at >= now() - interval '3 days'",
        u=rep.id,
    )
    assert c["new_leads"] == new_sql and made["newlead"] in ids("new_leads")
    # ordering: your move is sorted by due date
    dues = [x["due_date"] for x in t["your_move"]]
    assert dues == sorted(dues)
    assert due and set_owner and SimpleNamespace and health
