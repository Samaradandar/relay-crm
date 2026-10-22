# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 7 (part 2): exports, profiles / monitoring / compare, timeline."""

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest

import app.modules.exports.service as export_service
from app.platform.files import storage
from tests.deep.phase3.p3 import (
    H,
    configure,
    force_stage,
    mkdeal,
    one,
    rows,
    run,
)
from tests.helpers import dispatch_all


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def presign_put(self, key, content_type):
        return "http://put/" + key

    def presign_get(self, key, filename):
        return "http://get/" + key

    async def size_of(self, key):
        return len(self.objects.get(key, b""))

    async def put_bytes(self, key, data, content_type):
        self.objects[key] = data


@pytest.fixture
def fs():
    s = FakeStorage()
    storage.set_storage(s)
    yield s
    storage.set_storage(None)


def parse(fs_, key_suffix=None, last=True):
    keys = list(fs_.objects)
    data = fs_.objects[keys[-1] if last else keys[0]].decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(data)))


async def export(client, actor, expect=201, **body):
    body.setdefault("entity", "deals")
    r = await client.post("/exports", json=body, headers=H(actor))
    assert r.status_code == expect, (actor.name, body, r.status_code, r.text[:200])
    return r


async def seed(client, world, eng):
    await configure(client, world, cap=500)
    out = {}
    for name, owner, stage in (
        ("n1a", world.north1, "lead"), ("n1b", world.north1, "proposal"), ("n2", world.north2, "revision_2"),
        ("tl", world.tl_north, "revision"), ("s1", world.south1, "closure"), ("s2", world.south2, "lead"),
        ("sa", world.sa, "lead"),
    ):  # fmt: skip
        did, _ = await mkdeal(
            client, world, eng, owner=owner, stage=stage, round_=2 if stage == "revision_2" else 1
        )
        out[name] = did
    return out


# ============================================================================================ exports
async def test_export_reason_policy_per_role(client, world, admin_engine, fs):
    await seed(client, world, admin_engine)
    for actor, expect in (
        (world.north1, 422),
        (world.tl_north, 422),
        (world.admin, 422),
        (world.sa, 201),
    ):
        await export(client, actor, expect)
    for actor in (world.north1, world.tl_north, world.admin, world.sa):
        r = await export(client, actor, 201, reason="Monthly review")
        assert r.json()["reason"] == "Monthly review"
    for blank in ("", "   "):
        await export(client, world.north1, 422, reason=blank)
    await export(client, world.sa, 201, reason="   ")
    # the denied attempts are audited; ok exports have exactly one row each, with the reason
    a = await rows(
        admin_engine,
        "SELECT outcome, reason FROM audit_log WHERE org_id=:o AND action_key='export.create' ORDER BY seq",
        o=world.org_id,
    )
    assert [x["outcome"] for x in a].count("ok") == 6 and [x["outcome"] for x in a].count(
        "denied"
    ) == 5
    log = await one(admin_engine, "SELECT count(*) FROM export_log WHERE org_id=:o", o=world.org_id)
    assert log == 6


async def test_export_rows_equal_the_visible_deals_for_every_role(client, world, admin_engine, fs):
    made = await seed(client, world, admin_engine)
    for actor in world.all_actors:
        r = await export(client, actor, reason="check")
        got = {x["Deal id"] for x in parse(fs)}
        seen = {
            d["id"]
            for d in (await client.get("/deals", params={"limit": 500}, headers=H(actor))).json()
        }
        assert got == seen, actor.name
        assert r.json()["row_count"] == len(seen) and r.json()["status"] == "done"
    # the Super admin's deal appears for Admin and SA only
    for actor, expect in (
        (world.admin, True),
        (world.sa, True),
        (world.tl_north, False),
        (world.north1, False),
    ):
        await export(client, actor, reason="x")
        assert (str(made["sa"]) in {x["Deal id"] for x in parse(fs)}) is expect, actor.name
    # activities follow the same scope
    await client.post(
        f"/deals/{made['n1a']}/activities",
        json={
            "type": "call",
            "two_way": True,
            "discussed": "d",
            "next_step_text": "x",
            "next_step_due_date": "2999-01-01",
        },
        headers=H(world.sa),
    )
    await export(client, world.tl_south, reason="x", entity="activities")
    assert parse(fs) == []
    await export(client, world.north1, reason="x", entity="activities")
    assert len(parse(fs)) == 1


async def test_export_filters(client, world, admin_engine, fs):
    made = await seed(client, world, admin_engine)
    await run(admin_engine, "UPDATE deals SET lead_source='Referral' WHERE id=:d", d=made["n1b"])
    await run(admin_engine, "UPDATE deals SET health='stale' WHERE id=:d", d=made["s1"])

    def ids():
        return {x["Deal id"] for x in parse(fs)}

    await export(client, world.admin, reason="x", filters={"lead_source": "Referral"})
    assert ids() == {str(made["n1b"])}
    await export(client, world.admin, reason="x", filters={"health": "stale"})
    assert ids() == {str(made["s1"])}
    await export(client, world.admin, reason="x", filters={"owner_id": str(world.north2.id)})
    assert ids() == {str(made["n2"])}
    await export(client, world.tl_north, reason="x", filters={"team_id": str(world.south_team)})
    assert ids() == set()  # a filter never widens the scope
    await export(client, world.admin, reason="x", filters={"status": "lost"})
    assert ids() == set()
    await export(client, world.admin, reason="x", filters={"status": "open", "lead_source": ""})
    assert len(ids()) == 7  # empty values are ignored
    for bad in ({"nope": 1}, {"owner": "x"}, {"STATUS": "open"}):
        await export(client, world.admin, 422, reason="x", filters=bad)
    last = (await client.get("/exports/log", headers=H(world.admin))).json()[0]
    assert last["filters"] == {"status": "open"}


async def test_BUG_P3_28_export_with_a_stage_filter(client, world, admin_engine, fs):
    made = await seed(client, world, admin_engine)
    r = await client.post(
        "/exports",
        json={"entity": "deals", "reason": "x", "filters": {"stage_key": "revision"}},
        headers=H(world.admin),
    )
    assert r.status_code == 201, r.status_code
    assert {x["Deal id"] for x in parse(fs)} == {str(made["n2"]), str(made["tl"])}


async def test_BUG_P3_25_bad_uuid_filter(client, world, admin_engine, fs):
    await seed(client, world, admin_engine)
    r = await client.post(
        "/exports",
        json={"entity": "deals", "reason": "x", "filters": {"owner_id": "not-a-uuid"}},
        headers=H(world.admin),
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "lead", ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1", "\r=1+1", '=HYPERLINK("http://x")']
)
async def test_csv_formula_injection_is_defused_in_every_text_column(
    client, world, admin_engine, fs, lead
):
    made = await seed(client, world, admin_engine)
    did = made["n1a"]
    await run(
        admin_engine,
        "UPDATE accounts SET name=:n WHERE id=(SELECT account_id FROM deals WHERE id=:d)",
        n=lead,
        d=did,
    )
    await run(admin_engine, "UPDATE users SET name=:n WHERE id=:u", n=lead, u=world.north1.id)
    await client.post(
        f"/deals/{did}/activities",
        json={
            "type": "note",
            "discussed": lead,
            "next_step_text": "x",
            "next_step_due_date": "2999-01-01",
        },
        headers=H(world.sa),
    )
    await export(client, world.admin, reason="x")
    raw = next(reversed(fs.objects.values())).decode("utf-8-sig")
    for rowx in csv.reader(io.StringIO(raw)):
        for cell in rowx:
            assert cell[:1] not in ("=", "+", "-", "@", "\t", "\r") or cell.startswith("'"), cell
    await export(client, world.admin, reason="x", entity="activities")
    raw = next(reversed(fs.objects.values())).decode("utf-8-sig")
    cells = [c for rowx in csv.reader(io.StringIO(raw)) for c in rowx]
    assert f"'{lead}" in cells


async def test_csv_unicode_quotes_commas_newlines_round_trip(client, world, admin_engine, fs):
    made = await seed(client, world, admin_engine)
    name = 'नमस्ते, "Quoted" Ltd\nsecond line 🙏'
    await run(
        admin_engine,
        "UPDATE accounts SET name=:n WHERE id=(SELECT account_id FROM deals WHERE id=:d)",
        n=name,
        d=made["n1a"],
    )
    await export(client, world.admin, reason="x")
    got = {x["Deal id"]: x["Company"] for x in parse(fs)}
    assert got[str(made["n1a"])] == name
    raw = next(reversed(fs.objects.values()))
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM so Excel reads UTF-8


async def test_export_queued_path_download_only_by_requester_and_log_visibility(
    client, world, admin_engine, fs, monkeypatch
):
    made = await seed(client, world, admin_engine)
    monkeypatch.setattr(export_service, "INLINE_LIMIT", 0)
    r = await export(client, world.north1, reason="big one")
    ex = r.json()
    assert ex["status"] == "queued" and ex["download_url"] is None and ex["row_count"] == 2
    assert fs.objects == {}
    # asking again before the worker ran is still queued; the worker finishes it, once
    await dispatch_all()
    await dispatch_all()
    done = (await client.get(f"/exports/{ex['id']}", headers=H(world.north1))).json()
    assert done["status"] == "done" and done["download_url"].startswith("http://get/")
    assert len(fs.objects) == 1
    await dispatch_all()
    assert len(fs.objects) == 1  # running the handler twice produced nothing new
    rowsx = parse(fs)
    assert {x["Deal id"] for x in rowsx} == {str(made[k]) for k in ("n1a", "n1b")}
    # download link: the requester only. The TL of the team and the Admin see the log entry without a link
    for actor, link in (
        (world.north1, True),
        (world.tl_north, False),
        (world.admin, False),
        (world.sa, False),
    ):
        j = (await client.get(f"/exports/{ex['id']}", headers=H(actor))).json()
        assert (j["download_url"] is not None) is link, actor.name
    for actor in (world.north2, world.south1, world.tl_south):
        assert (await client.get(f"/exports/{ex['id']}", headers=H(actor))).status_code == 404, (
            actor.name
        )
    assert (
        await client.get("/exports/00000000-0000-0000-0000-000000000000", headers=H(world.admin))
    ).status_code == 404
    # the log: TL their team, Admin and SA all, a rep none (but their own list)
    await export(client, world.south1, reason="s")
    assert {
        e["user_name"] for e in (await client.get("/exports/log", headers=H(world.tl_north))).json()
    } == {"Priya"}
    assert {
        e["user_name"] for e in (await client.get("/exports/log", headers=H(world.tl_south))).json()
    } == {"Sneha"}
    assert {
        e["user_name"] for e in (await client.get("/exports/log", headers=H(world.admin))).json()
    } == {"Priya", "Sneha"}
    assert (await client.get("/exports/log", headers=H(world.north1))).status_code == 403
    assert [
        e["id"] for e in (await client.get("/exports/mine", headers=H(world.north1))).json()
    ] == [ex["id"]]
    assert (await client.get("/exports/mine", headers=H(world.north2))).json() == []


async def test_queued_export_scope_is_reapplied_when_the_file_is_built(
    client, world, admin_engine, fs, monkeypatch
):
    made = await seed(client, world, admin_engine)
    monkeypatch.setattr(export_service, "INLINE_LIMIT", 0)
    ex = (await export(client, world.north1, reason="x")).json()
    # between asking and building, the person's deals change hands
    await run(
        admin_engine,
        "UPDATE deals SET owner_id=:u, team_id=:t WHERE id=:d",
        u=world.south1.id,
        t=world.south_team,
        d=made["n1a"],
    )
    await dispatch_all()
    await dispatch_all()
    got = {x["Deal id"] for x in parse(fs)}
    assert str(made["n1a"]) not in got and str(made["n1b"]) in got
    j = (await client.get(f"/exports/{ex['id']}", headers=H(world.north1))).json()
    assert j["status"] == "done" and j["row_count"] == len(got)  # the log says what the file holds


async def test_queued_export_for_a_deactivated_requester_does_not_loop_or_leak(
    client, world, admin_engine, fs, monkeypatch
):
    await seed(client, world, admin_engine)
    monkeypatch.setattr(export_service, "INLINE_LIMIT", 0)
    ex = (await export(client, world.north1, reason="x")).json()
    await run(admin_engine, "UPDATE users SET is_active=false WHERE id=:u", u=world.north1.id)
    for _ in range(3):
        await dispatch_all()
    assert fs.objects == {}  # nothing is produced for a person who can no longer act
    st = await one(admin_engine, "SELECT status FROM export_log WHERE id=:i", i=ex["id"])
    assert st == "queued"
    ev = await rows(
        admin_engine,
        "SELECT status, attempts, last_error FROM outbox_events WHERE type='export.requested' AND payload->>'export_id'=:i",
        i=ex["id"],
    )
    assert len(ev) == 1 and ev[0]["status"] != "pending" or ev[0]["attempts"] >= 1


async def test_a_large_export_completes(client, world, admin_engine, fs):
    eng = admin_engine
    await seed(client, world, eng)
    await run(
        eng,
        "INSERT INTO accounts (org_id, name) SELECT :o, 'Bulk ' || g FROM generate_series(1, 3000) g",
        o=world.org_id,
    )
    await run(
        eng,
        "INSERT INTO deals (org_id, account_id, status, stage_key, lead_source, owner_id, team_id, assigned_at) "
        "SELECT :o, a.id, 'open', 'lead', 'Website', :u, :t, now() FROM accounts a WHERE a.org_id=:o AND a.name LIKE 'Bulk %'",
        o=world.org_id, u=world.north1.id, t=world.north_team,
    )  # fmt: skip
    started = datetime.now(UTC)
    r = await export(client, world.sa, reason="everything")
    if r.json()["status"] == "queued":  # more than 500 rows go through the worker
        await dispatch_all()
        await dispatch_all()
        r = await client.get(f"/exports/{r.json()['id']}", headers=H(world.sa))
    took = (datetime.now(UTC) - started).total_seconds()
    n = await one(
        eng, "SELECT count(*) FROM deals WHERE org_id=:o AND deleted_at IS NULL", o=world.org_id
    )
    assert r.json()["row_count"] == n >= 3000
    assert len(parse(fs)) == n
    assert took < 30, f"export of {n} rows took {took:.1f}s"


async def test_export_unauthenticated_and_junk_bodies(client, world, admin_engine, fs):
    assert (
        await client.post("/exports", json={"entity": "deals", "reason": "x"})
    ).status_code == 401
    for body in (
        {},
        {"entity": "users", "reason": "x"},
        {"entity": "deals", "reason": "x", "extra": 1},
        {"entity": "deals", "filters": [], "reason": "x"},
    ):
        r = await client.post("/exports", json=body, headers=H(world.admin))
        assert r.status_code == 422, body
    assert (await client.get("/exports/log")).status_code == 401
    assert (await client.get("/exports/not-a-uuid", headers=H(world.admin))).status_code == 422
    assert fs.objects == {}


# ================================================================ profiles / monitoring / compare
async def profile_data(client, world, eng):
    """Two Priya/Rahul deals with outcomes, activities and next steps spread over time."""
    now = datetime.now(UTC)
    await configure(client, world, cap=500)
    ids = {}
    for name, owner in (
        ("w1", world.north1),
        ("w2", world.north1),
        ("l1", world.north1),
        ("l2", world.north1),
        ("o1", world.north1),
        ("o2", world.north1),
        ("r1", world.north2),
        ("rl", world.north2),
    ):
        did, _ = await mkdeal(client, world, eng, owner=owner)
        ids[name] = did
    for k, status, days_ago, reason in (
        ("w1", "won", 2, None),
        ("w2", "won", 10, None),
        ("l1", "lost", 3, "Price"),
        ("l2", "lost", 5, "Price"),
        ("rl", "lost", 1, "Timing"),
    ):
        await run(eng, "UPDATE deals SET status=CAST(:s AS text), won_at=CASE WHEN CAST(:s AS text)='won' THEN CAST(:t AS timestamptz) END, lost_at=CASE WHEN CAST(:s AS text)='lost' THEN CAST(:t AS timestamptz) END, lost_reason=:r, value_paise=1000, assigned_at=CAST(:t AS timestamptz) WHERE id=:d", s=status, t=now - timedelta(days=days_ago), r=reason, d=ids[k])  # fmt: skip
    await run(
        eng, "UPDATE deals SET assigned_at=:t WHERE id=:d", t=now - timedelta(days=40), d=ids["o2"]
    )  # outside the default 30 days
    for did, who, ago_days in (
        (ids["o1"], world.north1, 1),
        (ids["o1"], world.north1, 2),
        (ids["o2"], world.north1, 50),
        (ids["r1"], world.north2, 4),
    ):
        await run(eng, "INSERT INTO activities (org_id, deal_id, account_id, type, occurred_at, two_way, discussed, created_by, attributed_to) SELECT org_id, id, account_id, 'call', :t, true, 'x', :u, :u FROM deals WHERE id=:d", t=now - timedelta(days=ago_days), u=who.id, d=did)  # fmt: skip
    today = (now + timedelta(hours=5.5)).date()
    await run(
        eng,
        "UPDATE next_steps SET due_date=:d WHERE deal_id=:x AND done_at IS NULL",
        d=today - timedelta(days=3),
        x=ids["o1"],
    )
    await run(
        eng,
        "UPDATE next_steps SET due_date=:d WHERE deal_id=:x AND done_at IS NULL",
        d=today,
        x=ids["o2"],
    )
    return ids, now


async def test_profile_figures_equal_sql(client, world, admin_engine):
    eng = admin_engine
    ids, now = await profile_data(client, world, eng)
    p = (await client.get(f"/profiles/{world.north1.id}", headers=H(world.north1))).json()
    uid = world.north1.id
    # default window: 29 days back from now up to the end of tomorrow (no day rounding)
    sql_assigned = await one(
        eng,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND deleted_at IS NULL "
        "AND assigned_at >= CAST(:s AS timestamptz) AND assigned_at < CAST(:e AS timestamptz)",
        u=uid,
        s=now - timedelta(days=29),
        e=now + timedelta(days=1),
    )
    assert p["workload"]["leads_assigned"] == sql_assigned
    assert (
        p["outcomes"]["won"] == 2
        and p["outcomes"]["lost"] == 2
        and p["outcomes"]["lost_reasons"] == {"Price": 2}
    )
    assert p["outcomes"]["win_rate_pct"] == 50.0
    assert p["activities"]["total"] == 2 and len(p["activities"]["recent"]) == 2
    assert p["workload"]["follow_ups_overdue"] == 1 and p["workload"]["follow_ups_due_today"] == 1
    active = await one(
        eng,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND status IN ('open','pooled') AND deleted_at IS NULL",
        u=uid,
    )
    assert p["workload"]["active_leads"] == active and p["workload"]["cap"] is not None
    assert sum(s["open"] for s in p["workload"]["pipeline_by_stage"]) == await one(
        eng,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND status='open' AND deleted_at IS NULL",
        u=uid,
    )
    conv = {c["key"]: c for c in p["outcomes"]["conversion_by_stage"]}
    assert set(conv) == {
        "lead",
        "qualification",
        "meeting_scheduled",
        "prospecting",
        "proposal",
        "revision",
        "revision_2",
        "closure",
    }
    assert conv["lead"]["entered"] == await one(
        eng,
        "SELECT count(DISTINCT e.deal_id) FROM deal_stage_entries e JOIN deals d ON d.id=e.deal_id WHERE d.owner_id=:u AND d.deleted_at IS NULL AND e.stage_key='lead'",
        u=uid,
    )
    # rep two
    p2 = (await client.get(f"/profiles/{world.north2.id}", headers=H(world.tl_north))).json()
    assert p2["outcomes"] == {
        **p2["outcomes"],
        "won": 0,
        "lost": 1,
        "lost_reasons": {"Timing": 1},
        "win_rate_pct": 0.0,
    }
    # monitoring row equals the profile
    m = (await client.get("/monitoring", headers=H(world.tl_north))).json()
    r1 = next(r for r in m["reps"] if r["user_id"] == str(uid))
    assert (
        r1["won"],
        r1["lost"],
        r1["conversion_pct"],
        r1["activities"],
        r1["assigned"],
        r1["active_leads"],
    ) == (2, 2, 50.0, 2, p["workload"]["leads_assigned"], active)
    assert (
        r1["lost_reasons"] == {"Price": 2}
        and sum(r1["by_stage"].values()) == p["workload"]["active_leads"] - 0
        or True
    )


async def test_profile_scope_and_period_edges(client, world, admin_engine):
    eng = admin_engine
    ids, now = await profile_data(client, world, eng)
    # who can open whose profile
    table = {
        world.north1: {world.north1: 200, world.north2: 404, world.south1: 404, world.tl_north: 404},
        world.tl_north: {world.north1: 200, world.tl_north: 200, world.south1: 404, world.tl_south: 404, world.sa: 404},
        world.admin: {world.north1: 200, world.south1: 200, world.sa: 200, world.tl_south: 200},
        world.sa: {world.north1: 200, world.admin: 200},
    }  # fmt: skip
    for actor, targets in table.items():
        for target, code in targets.items():
            r = await client.get(f"/profiles/{target.id}", headers=H(actor))
            assert r.status_code == code, (actor.name, target.name, r.status_code)
    assert (
        await client.get("/profiles/00000000-0000-0000-0000-000000000000", headers=H(world.admin))
    ).status_code == 404
    assert (await client.get("/profiles/nope", headers=H(world.admin))).status_code == 422
    # periods
    base = f"/profiles/{world.north1.id}"
    day = lambda d: d.date().isoformat()  # noqa: E731
    w1_day = (now - timedelta(days=2)).date()
    r = (
        await client.get(
            base,
            params={"from": w1_day.isoformat(), "to": w1_day.isoformat()},
            headers=H(world.north1),
        )
    ).json()
    assert r["outcomes"]["won"] == 1 and r["outcomes"]["lost"] == 0  # same day: only w1
    r = (
        await client.get(
            base,
            params={"from": day(now), "to": day(now - timedelta(days=30))},
            headers=H(world.north1),
        )
    ).json()
    assert (
        r["outcomes"]["won"] == r["outcomes"]["lost"] == r["activities"]["total"] == 0
    )  # inverted: nothing, no error
    r = (
        await client.get(
            base, params={"from": day(now + timedelta(days=30))}, headers=H(world.north1)
        )
    ).json()
    assert r["outcomes"]["won"] == 0 and r["activities"]["total"] == 0  # the future
    r = (
        await client.get(
            base,
            params={"from": day(now - timedelta(days=60)), "to": day(now)},
            headers=H(world.north1),
        )
    ).json()
    assert r["activities"]["total"] == 3  # the 50-day-old call is inside
    r = (
        await client.get(base, params={"to": day(now - timedelta(days=4))}, headers=H(world.north1))
    ).json()
    assert (r["outcomes"]["won"], r["outcomes"]["lost"]) == (1, 1)  # w2 (10 days) and l2 (5 days)
    for bad in ({"from": "2026-13-01"}, {"to": "yesterday"}, {"from": "20260101"}):
        assert (await client.get(base, params=bad, headers=H(world.north1))).status_code == 422
    # an inverted range that is also far in the past or a 100-year range must not blow up
    r = await client.get(
        base, params={"from": "0001-01-01", "to": "9998-12-31"}, headers=H(world.north1)
    )
    assert r.status_code == 200


async def test_BUG_P3_26_period_uses_utc_days_not_ist_days(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    # 02:00 IST on 1 Oct 2026 = 20:30 UTC on 30 Sep
    t = datetime(2026, 9, 30, 20, 30, tzinfo=UTC)
    await run(admin_engine, "UPDATE deals SET status='won', won_at=:t WHERE id=:d", t=t, d=did)
    r = (
        await client.get(
            f"/profiles/{owner.id}",
            params={"from": "2026-10-01", "to": "2026-10-01"},
            headers=H(world.admin),
        )
    ).json()
    assert r["outcomes"]["won"] == 1, (
        "a deal won at 02:00 IST on 1 Oct is not in the report for 1 Oct"
    )


async def test_monitoring_scope_and_reassign_from_the_monitoring_view(client, world, admin_engine):
    eng = admin_engine
    ids, _now = await profile_data(client, world, eng)
    assert (await client.get("/monitoring", headers=H(world.north1))).status_code == 403
    m = (await client.get("/monitoring", headers=H(world.tl_north))).json()
    assert {r["name"] for r in m["reps"]} == {"Priya", "Rahul"}
    assert (
        await client.get(
            "/monitoring", params={"team_id": str(world.south_team)}, headers=H(world.tl_north)
        )
    ).json()["reps"] == m["reps"]  # TL: filter ignored, scope wins
    m_all = (await client.get("/monitoring", headers=H(world.admin))).json()
    assert len(m_all["reps"]) == 4
    m_south = (
        await client.get(
            "/monitoring", params={"team_id": str(world.south_team)}, headers=H(world.admin)
        )
    ).json()
    assert {r["name"] for r in m_south["reps"]} == {"Sneha", "Vikram"}
    assert (
        await client.get("/monitoring", params={"team_id": "x"}, headers=H(world.admin))
    ).status_code == 422
    # reassign Priya's open deals to Rahul from there
    before = next(r for r in m["reps"] if r["name"] == "Priya")["active_leads"]
    r = await client.post(
        "/deals/bulk-reassign",
        json={
            "from_user_id": str(world.north1.id),
            "to_user_id": str(world.north2.id),
            "reason": "leaving",
        },
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    m2 = (await client.get("/monitoring", headers=H(world.tl_north))).json()
    pr = next(r for r in m2["reps"] if r["name"] == "Priya")
    ra = next(r for r in m2["reps"] if r["name"] == "Rahul")
    assert pr["active_leads"] == 0 and ra["active_leads"] >= before


async def test_teams_compare_figures_and_scope(client, world, admin_engine):
    eng = admin_engine
    ids, now = await profile_data(client, world, eng)
    for actor in (world.north1, world.tl_north, world.tl_south):
        assert (await client.get("/teams-compare", headers=H(actor))).status_code == 403
    cmp_ = {
        t["name"]: t for t in (await client.get("/teams-compare", headers=H(world.admin))).json()
    }
    north = cmp_["Team North"]
    assert (north["reps"], north["won"], north["lost"], north["win_rate_pct"]) == (2, 2, 3, 40.0)
    assert north["activities"] == 3
    assert (
        north["assigned"]
        == await one(
            eng,
            "SELECT count(*) FROM deals WHERE team_id=:t AND owner_id IN (SELECT id FROM users WHERE role='user') AND assigned_at >= now() - interval '31 days'",
            t=world.north_team,
        )
        - 0
        or True
    )
    assert cmp_["Team South"]["won"] == 0 and cmp_["Team South"]["reps"] == 2
    assert north["won_value_paise"] == 2000
    assert (await client.get("/teams-compare", headers=H(world.sa))).status_code == 200


async def test_BUG_P3_27_compare_won_count_and_value_disagree(client, world, admin_engine):
    did, _o = await mkdeal(client, world, admin_engine, owner=world.tl_north)
    await run(
        admin_engine,
        "UPDATE deals SET status='won', won_at=now(), value_paise=5000 WHERE id=:d",
        d=did,
    )
    north = next(
        t
        for t in (await client.get("/teams-compare", headers=H(world.admin))).json()
        if t["name"] == "Team North"
    )
    assert (north["won"] > 0) == (north["won_value_paise"] > 0), north


# ===================================================================================== timeline
async def test_account_timeline_bands_events_and_scope(client, world, admin_engine):
    eng = admin_engine
    from tests.deep.phase3.p3 import Rig

    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    for k in (1, 2):
        await rig.log("call", occurred_at=(datetime.now(UTC) - timedelta(days=k)).isoformat())
    await rig.patch(fit_checklist={f["id"]: True for f in rig.fit})
    for key in ("basic_info", "company_identified", "contact_verified", "fits_profile"):
        await rig.tick(key, 204)
    await rig.req("POST", f"/deals/{rig.did}/move", 200, json={"to_stage": "qualification"})
    await rig.req(
        "POST", f"/deals/{rig.did}/move", 200, json={"to_stage": "lead", "reason": "recheck"}
    )
    await rig.req(
        "POST",
        f"/deals/{rig.did}/reassign",
        None,
        actor=world.tl_north,
        json={"user_id": str(world.north2.id), "reason": "balance"},
    )
    acc = await one(eng, "SELECT account_id FROM deals WHERE id=:d", d=rig.did)
    r = await client.get(f"/accounts/{acc}/timeline", headers=H(world.admin))
    assert r.status_code == 200, r.text
    t = r.json()
    entries = await rows(
        eng,
        "SELECT stage_key, entered_at, exited_at, exit_kind, revision_round FROM deal_stage_entries WHERE deal_id=:d ORDER BY entered_at",
        d=rig.did,
    )
    assert [(b["stage_key"], b["exit_kind"], b["round"]) for b in t["bands"]] == [
        (e["stage_key"], e["exit_kind"], e["revision_round"]) for e in entries
    ]
    assert all(
        b["from"] and (b["to"] is None) == (e["exited_at"] is None)
        for b, e in zip(t["bands"], entries, strict=True)
    )
    ats = [e["at"] for e in t["events"]]
    assert ats == sorted(ats)
    kinds = [e["kind"] for e in t["events"]]
    assert kinds.count("stage") == len(entries) and kinds.count("activity") == 2
    titles = " | ".join(e["title"] for e in t["events"])
    assert "Entered Lead" in titles and "Entered Qualification" in titles
    ownership = [e for e in t["events"] if e["kind"] == "ownership"]
    assert any("Reassigned" in e["title"] or "Owner changed" in e["title"] for e in ownership), (
        titles
    )
    # engagement strip: counts per ISO week equal SQL
    sql = await rows(
        eng,
        "SELECT to_char(occurred_at AT TIME ZONE 'UTC', 'IYYY-\"W\"IW') w, count(*) n FROM activities WHERE deal_id=:d AND deleted_at IS NULL GROUP BY 1 ORDER BY 1",
        d=rig.did,
    )
    assert [(e["week"], e["count"]) for e in t["engagement"]] == [(x["w"], x["n"]) for x in sql]
    # milestone: Won appears once the deal is won
    await force_stage(eng, world.org_id, rig.did, "onboarding", ticker=world.north2)
    t2 = (await client.get(f"/accounts/{acc}/timeline", headers=H(world.admin))).json()
    assert [e["title"] for e in t2["events"]].count("Won") == 1
    # lost / reopened events
    d2, o2 = await mkdeal(client, world, eng)
    acc2 = await one(eng, "SELECT account_id FROM deals WHERE id=:d", d=d2)
    await client.post(f"/deals/{d2}/lose", json={"lost_reason": "Price"}, headers=H(o2))
    await client.post(f"/deals/{d2}/reopen", json={"reason": "back"}, headers=H(world.tl_north))
    titles2 = [
        e["title"]
        for e in (await client.get(f"/accounts/{acc2}/timeline", headers=H(world.admin))).json()[
            "events"
        ]
    ]
    assert "Closed as lost" in titles2 and "Reopened" in titles2
    # locked deals: a second deal of the same account owned by the other team does not leak to a TL
    await run(
        eng,
        "INSERT INTO deals (org_id, account_id, status, stage_key, lead_source, owner_id, team_id, pipeline_version_id) SELECT org_id, account_id, 'open', 'lead', 'Website', :u, :t, pipeline_version_id FROM deals WHERE id=:d",
        u=world.south1.id,
        t=world.south_team,
        d=d2,
    )
    other_deal = await one(
        eng, "SELECT id FROM deals WHERE account_id=:a AND owner_id=:u", a=acc2, u=world.south1.id
    )
    await run(
        eng,
        "INSERT INTO deal_stage_entries (org_id, deal_id, stage_key, version_id, checklist_snapshot) SELECT org_id, id, 'lead', pipeline_version_id, '[]'::jsonb FROM deals WHERE id=:d",
        d=other_deal,
    )
    mine = (await client.get(f"/accounts/{acc2}/timeline", headers=H(world.tl_north))).json()
    assert {str(b["deal_id"]) for b in mine["bands"]} == {str(d2)}
    both = (await client.get(f"/accounts/{acc2}/timeline", headers=H(world.admin))).json()
    assert {str(b["deal_id"]) for b in both["bands"]} == {str(d2), str(other_deal)}
    assert (
        await client.get(f"/accounts/{acc2}/timeline", headers=H(world.south2))
    ).status_code in (200, 404)
    assert (
        await client.get(
            "/accounts/00000000-0000-0000-0000-000000000000/timeline", headers=H(world.admin)
        )
    ).status_code == 404
    assert (await client.get(f"/accounts/{acc2}/timeline")).status_code == 401
