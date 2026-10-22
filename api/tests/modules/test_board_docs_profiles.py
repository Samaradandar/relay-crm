# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Board, stage pages and metrics (scope), documents, profiles, exports and the account timeline."""

import csv
import io
import json

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.exports import service as export_service
from app.platform.files import storage
from tests.helpers import dispatch_all, headers
from tests.modules.journey import add_doc, detail, due, log, make_file, move, new_deal, run_to, tick
from tests.modules.test_leads_api import actor_by_id, audit_keys, configure, create


async def populate(client, world):
    """Four deals in North and one in South, spread over stages."""
    await configure(client, world)
    deals = [await create(client, world.north1) for _ in range(4)]
    await move(client, world.admin, deals[1]["id"], "qualification", override=True, reason="seed")
    await move(client, world.admin, deals[2]["id"], "proposal", override=True, reason="seed")
    await move(client, world.admin, deals[3]["id"], "revision", override=True, reason="seed")
    south = await create(client, world.south1)
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE deals SET owner_id=:u, team_id=:t WHERE id=:d"),
            {"u": world.south1.id, "t": world.south_team, "d": south["id"]},
        )
        await s.commit()
    return deals, south


def card_ids(board):
    return {c["id"] for col in board["columns"] for c in col["cards"]}


# ------------------------------------------------------------------------------ board
async def test_board_columns_scope_and_no_forecast(client, world):
    deals, south = await populate(client, world)
    admin = (await client.get("/board", headers=headers(world.admin))).json()
    assert [c["key"] for c in admin["columns"]] == [
        "lead", "qualification", "meeting_scheduled", "prospecting", "proposal", "revision", "closure",
    ]  # fmt: skip
    assert card_ids(admin) == {d["id"] for d in deals} | {south["id"]}
    assert admin["summary"]["open_deals"] == 5
    assert (
        "weighted" not in json.dumps(admin).lower() and "forecast" not in json.dumps(admin).lower()
    )
    rev = next(c for c in admin["columns"] if c["key"] == "revision")
    assert (
        rev["count"] == 1
        and rev["probability_pct"] == 10
        and rev["cards"][0]["revision_round"] == 1
    )
    # a rep sees only their own deals; each Team Leader only their team's; Admin everything
    owner_ids = {d["id"] for d in deals if d["owner"]["id"] == str(world.north1.id)}
    mine = await client.get("/board", headers=headers(world.north1))
    assert card_ids(mine.json()) == {
        d["id"]
        for d in (
            await client.get("/deals", params={"limit": 100}, headers=headers(world.north1))
        ).json()
    }
    assert owner_ids <= card_ids(mine.json()) or not owner_ids
    assert card_ids((await client.get("/board", headers=headers(world.tl_north))).json()) == {
        d["id"] for d in deals
    }
    assert card_ids((await client.get("/board", headers=headers(world.tl_south))).json()) == {
        south["id"]
    }
    # a deal owned by a Super admin is hidden from Team Leaders and reps, visible to Admin
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE deals SET owner_id=:u WHERE id=:d"),
            {"u": world.sa.id, "d": deals[0]["id"]},
        )
        await s.commit()
    assert deals[0]["id"] not in card_ids(
        (await client.get("/board", headers=headers(world.tl_north))).json()
    )
    assert deals[0]["id"] in card_ids(
        (await client.get("/board", headers=headers(world.admin))).json()
    )


async def test_board_filters_and_paging(client, world):
    deals, south = await populate(client, world)
    lead = lambda b: next(c for c in b["columns"] if c["key"] == "lead")  # noqa: E731
    b = (await client.get("/board", params={"per_column": 1}, headers=headers(world.admin))).json()
    assert lead(b)["count"] == 2 and len(lead(b)["cards"]) == 1
    more = (
        await client.get(
            "/board/cards",
            params={"column": "lead", "offset": 1, "limit": 5},
            headers=headers(world.admin),
        )
    ).json()
    assert len(more) == 1 and more[0]["id"] != lead(b)["cards"][0]["id"]
    assert (
        await client.get("/board/cards", params={"column": "nope"}, headers=headers(world.admin))
    ).status_code == 404
    one = deals[0]
    f = (
        await client.get(
            "/board", params={"owner_id": one["owner"]["id"]}, headers=headers(world.admin)
        )
    ).json()
    assert all(c["owner"]["id"] == one["owner"]["id"] for col in f["columns"] for c in col["cards"])
    f = (
        await client.get("/board", params={"q": one["company"][:5]}, headers=headers(world.admin))
    ).json()
    assert one["id"] in card_ids(f)
    f = (
        await client.get("/board", params={"health": "stale"}, headers=headers(world.admin))
    ).json()
    assert card_ids(f) == set()
    f = (
        await client.get("/board", params={"source": "Event"}, headers=headers(world.admin))
    ).json()
    assert card_ids(f) == set()


async def test_stage_pages(client, world):
    deals, south = await populate(client, world)
    r = await client.get("/stages/lead/deals", headers=headers(world.admin))
    page = r.json()
    assert page["total"] == 2 and [c["key"] for c in page["columns"]][:2] == [
        "generated_by",
        "assigned_by",
    ]
    assert {"contact_verified", "company_identified"} <= set(page["rows"][0]["extra"])
    assert (
        await client.get(
            "/stages/lead/deals", params={"page_size": 7}, headers=headers(world.admin)
        )
    ).status_code == 422
    assert (await client.get("/stages/nope/deals", headers=headers(world.admin))).status_code == 404
    rev = (await client.get("/stages/revision/deals", headers=headers(world.admin))).json()
    assert rev["total"] == 1 and rev["rows"][0]["extra"]["round"] == 1
    by_company = (
        await client.get(
            "/stages/lead/deals",
            params={"sort": "company", "page_size": 10},
            headers=headers(world.admin),
        )
    ).json()
    names = [r["company"] for r in by_company["rows"]]
    assert names == sorted(names)
    own = (await client.get("/stages/lead/deals", headers=headers(world.north1))).json()
    assert all(r["owner"]["id"] == str(world.north1.id) for r in own["rows"])
    assert (await client.get("/stages/lead/deals", headers=headers(world.tl_south))).json()[
        "total"
    ] == 1


async def test_stage_metrics_come_from_real_history(client, world, admin_engine):
    won, owner = await run_to(client, world, admin_engine, "won")
    lost, owner2 = await run_to(client, world, admin_engine, "qualification")
    r = await client.post(
        f"/deals/{lost['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner2)
    )
    assert r.status_code == 200
    m = (await client.get("/stages/lead/metrics", headers=headers(world.admin))).json()
    assert m["deals_entered"] == 2 and m["won"] == 1 and m["lost"] == 1
    assert m["win_rate_pct"] == 50.0 and m["conversion_to_next_pct"] == 100.0
    assert m["avg_days_in_stage"] is not None and m["configured_probability_pct"] == 0
    q = (await client.get("/stages/qualification/metrics", headers=headers(world.admin))).json()
    assert q["conversion_to_next_pct"] == 50.0  # only the won deal went on
    allm = (await client.get("/stages/metrics", headers=headers(world.admin))).json()
    assert [x["key"] for x in allm][:2] == ["lead", "qualification"]
    # scoped: the other team sees nothing of this
    other = (await client.get("/stages/lead/metrics", headers=headers(world.tl_south))).json()
    assert other["deals_entered"] == 0 and other["win_rate_pct"] is None
    _ = won


# ------------------------------------------------------------------------------ documents
async def test_document_rules_by_role(client, world, admin_engine):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    other_deal, _o = await new_deal(client, world)
    r = await add_doc(
        client, owner, did, "agreement", "sent", expect=422
    )  # a sent agreement needs its file
    fid = await make_file(admin_engine, world, did, owner)
    foreign = await make_file(admin_engine, world, other_deal["id"], owner)
    await add_doc(
        client, owner, did, "agreement", "sent", foreign, expect=422
    )  # another deal's file
    ag = await add_doc(client, owner, did, "agreement", "sent", fid)
    inv = await add_doc(client, owner, did, "invoice", "draft", number="INV-9", amount_paise=50000)
    # edit rights
    r = await client.patch(
        f"/documents/{ag['id']}", json={"notes": "n", "reason": "x"}, headers=headers(owner)
    )
    assert r.status_code == 403
    r = await client.patch(
        f"/documents/{ag['id']}", json={"notes": "n"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 422  # a Team Leader's edit needs a reason
    r = await client.patch(
        f"/documents/{ag['id']}",
        json={"notes": "n", "reason": "fix"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200 and r.json()["notes"] == "n"
    # terminating an agreement: Admin and Super admin only
    for who in (owner, world.tl_north):
        r = await client.patch(
            f"/documents/{ag['id']}",
            json={"status": "terminated", "reason": "x"},
            headers=headers(who),
        )
        assert r.status_code == 403
    r = await client.patch(
        f"/documents/{ag['id']}", json={"status": "terminated"}, headers=headers(world.admin)
    )
    assert r.status_code == 422
    r = await client.patch(
        f"/documents/{ag['id']}",
        json={"status": "terminated", "reason": "Customer left"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200 and r.json()["status"] == "terminated"
    # an invoice edit by a Team Leader is a request to an Admin
    r = await client.patch(
        f"/documents/{inv['id']}",
        json={"number": "INV-10", "reason": "typo"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 202, r.text
    assert (
        await client.patch(
            f"/documents/{inv['id']}", json={"number": "X", "reason": "x"}, headers=headers(owner)
        )
    ).status_code == 403
    await client.post(
        f"/approvals/{r.json()['approval_id']}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=headers(world.admin),
    )
    docs = (await client.get(f"/deals/{did}/documents", headers=headers(owner))).json()
    assert next(d for d in docs if d["kind"] == "invoice")["number"] == "INV-10"
    r = await client.patch(
        f"/documents/{inv['id']}",
        json={"number": "INV-11", "reason": "direct"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    # nobody outside the deal's scope reads its documents
    assert (
        await client.get(f"/deals/{did}/documents", headers=headers(world.tl_south))
    ).status_code == 404


async def test_deleting_a_document_marks_the_dependent_tick(client, world, admin_engine):
    deal, owner = await run_to(client, world, admin_engine, "revision")
    did = deal["id"]
    from tests.modules.journey import patch

    await patch(client, owner, did, final_price_paise=1, scope="s", agreed_by="a")
    fid = await make_file(admin_engine, world, did, owner)
    ag = await add_doc(client, owner, did, "agreement", "sent", fid)
    await tick(client, owner, did, "agreement_sent")
    r = await client.delete(
        f"/documents/{ag['id']}", params={"reason": "wrong file"}, headers=headers(owner)
    )
    assert r.status_code == 403
    r = await client.delete(
        f"/documents/{ag['id']}", params={"reason": "wrong file"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 204, r.text
    crit = next(
        c
        for c in (await detail(client, owner, did))["progress"]["criteria"]
        if c["key"] == "agreement_sent"
    )
    assert crit["proof_missing"]


async def test_proposal_stage_needs_no_proposal_document_to_enter(client, world, admin_engine):
    deal, owner = await run_to(client, world, admin_engine, "prospecting")
    did = deal["id"]
    from tests.modules.journey import patch, setup_catalog

    prod = await setup_catalog(client, world)
    await patch(client, owner, did, budget_amount_paise=1, budget_source="b", expected_close_date=due(20),
                expected_close_reason="r", pricing_model="u", plan="p", expected_minutes_per_month=10)  # fmt: skip
    c = (await detail(client, owner, did))["contacts"][0]
    # the decision maker has to be reachable: name, phone and email on file
    r = await client.patch(
        f"/contacts/{c['id']}", json={"is_decision_maker": True}, headers=headers(owner)
    )
    assert r.status_code == 200
    await client.post(
        f"/deals/{did}/lines",
        json={"product_id": prod["id"], "quantity": 10},
        headers=headers(owner),
    )
    for k in ("budget_confirmed", "timeline_agreed", "pricing_selected"):
        await tick(client, owner, did, k)
    r = await tick(client, owner, did, "decision_maker", expect=422)
    assert "phone" in str(r.json())  # the contact has an email but no phone yet
    await client.patch(
        f"/contacts/{c['id']}", json={"phone": "+919800000002"}, headers=headers(owner)
    )
    await tick(client, owner, did, "decision_maker")
    await move(client, owner, did, "proposal")  # no proposal needed to get in


# ------------------------------------------------------------------------------ profiles
async def test_profiles_and_monitoring_scope(client, world, admin_engine):
    won, owner = await run_to(client, world, admin_engine, "won")
    mate = world.north2 if owner.id == world.north1.id else world.north1
    p = await client.get(f"/profiles/{owner.id}", headers=headers(owner))
    assert p.status_code == 200
    body = p.json()
    assert body["outcomes"]["won"] == 1 and body["workload"]["leads_assigned"] == 1
    assert (
        body["workload"]["active_leads"] == 0 and body["activities"]["total"] >= 4
    )  # the booked-for-later meeting is outside the period
    assert any(
        c["key"] == "lead" and c["entered"] == 1 and c["advanced"] == 1
        for c in body["outcomes"]["conversion_by_stage"]
    )
    assert (await client.get(f"/profiles/{owner.id}", headers=headers(mate))).status_code == 404
    assert (
        await client.get(f"/profiles/{owner.id}", headers=headers(world.tl_north))
    ).status_code == 200
    assert (
        await client.get(f"/profiles/{owner.id}", headers=headers(world.tl_south))
    ).status_code == 404
    assert (
        await client.get(f"/profiles/{owner.id}", headers=headers(world.admin))
    ).status_code == 200
    assert (
        await client.get(f"/profiles/{world.sa.id}", headers=headers(world.tl_north))
    ).status_code == 404
    assert (
        await client.get(f"/profiles/{world.sa.id}", headers=headers(world.admin))
    ).status_code == 200
    m = (await client.get("/monitoring", headers=headers(world.tl_north))).json()
    assert {r["name"] for r in m["reps"]} == {world.north1.name, world.north2.name}
    row = next(r for r in m["reps"] if r["user_id"] == str(owner.id))
    assert row["won"] == 1 and row["conversion_pct"] == 100.0 and row["last_interaction"]
    assert len((await client.get("/monitoring", headers=headers(world.admin))).json()["reps"]) == 4
    assert (await client.get("/monitoring", headers=headers(owner))).status_code == 403
    cmp_ = (await client.get("/teams-compare", headers=headers(world.admin))).json()
    assert {t["name"] for t in cmp_} == {"Team North", "Team South"}
    assert next(t for t in cmp_ if t["name"] == "Team North")["won"] == 1
    assert (await client.get("/teams-compare", headers=headers(world.tl_north))).status_code == 403


# ------------------------------------------------------------------------------ exports
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


async def test_exports_are_scoped_logged_and_formula_safe(client, world, monkeypatch):
    fs = FakeStorage()
    storage.set_storage(fs)
    try:
        await configure(client, world)
        evil = await create(client, world.north1, company="=HYPERLINK(1)")
        for _ in range(2):
            await create(client, world.north2)
        r = await client.post(
            "/exports", json={"entity": "deals", "filters": {}}, headers=headers(world.north1)
        )
        assert r.status_code == 422 and r.json()["code"] == "reason_required"
        r = await client.post(
            "/exports",
            json={"entity": "deals", "filters": {"nope": 1}, "reason": "x"},
            headers=headers(world.north1),
        )
        assert r.status_code == 422
        r = await client.post(
            "/exports",
            json={"entity": "deals", "reason": "Monthly review"},
            headers=headers(world.north1),
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["status"] == "done" and out["download_url"].startswith("http://get/")
        data = next(iter(fs.objects.values())).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(data)))
        mine = {
            d["id"]
            for d in (
                await client.get("/deals", params={"limit": 100}, headers=headers(world.north1))
            ).json()
        }
        assert len(rows) == len(mine) == out["row_count"]
        assert all(r_["Deal id"] in mine for r_ in rows)
        assert not any(r_["Company"].startswith("=") for r_ in rows)  # no formula injection
        _ = evil
        # super admin may export without a reason; an Admin may not
        assert (
            await client.post("/exports", json={"entity": "deals"}, headers=headers(world.admin))
        ).status_code == 422
        r = await client.post("/exports", json={"entity": "activities"}, headers=headers(world.sa))
        assert r.status_code == 201
        # the log: team leader sees the team's, admin all, a rep none; the file link is the requester's
        log_tl = (await client.get("/exports/log", headers=headers(world.tl_north))).json()
        assert {e["user_name"] for e in log_tl} == {world.north1.name}
        assert len((await client.get("/exports/log", headers=headers(world.admin))).json()) == 2
        assert (await client.get("/exports/log", headers=headers(world.north1))).status_code == 403
        assert (await client.get("/exports/mine", headers=headers(world.north1))).json()[0][
            "id"
        ] == out["id"]
        assert "download_url" not in (
            await client.get(f"/exports/{out['id']}", headers=headers(world.tl_north))
        ).json() or (
            (await client.get(f"/exports/{out['id']}", headers=headers(world.tl_north))).json()[
                "download_url"
            ]
            is None
        )
        assert (
            await client.get(f"/exports/{out['id']}", headers=headers(world.south1))
        ).status_code == 404
        assert ("export.create", "ok") in await audit_keys(out["id"])
        # a large export is queued and finished by the worker
        monkeypatch.setattr(export_service, "INLINE_LIMIT", 0)
        r = await client.post(
            "/exports",
            json={"entity": "deals", "reason": "Big one"},
            headers=headers(world.tl_north),
        )
        assert r.json()["status"] == "queued"
        await dispatch_all()
        assert (
            await client.get(f"/exports/{r.json()['id']}", headers=headers(world.tl_north))
        ).json()["status"] == "done"
        # team leader exports only their team
        data = [v for k, v in fs.objects.items() if r.json()["id"] in k][0].decode("utf-8-sig")
        assert len(list(csv.DictReader(io.StringIO(data)))) == 3
    finally:
        storage.set_storage(None)


# ------------------------------------------------------------------------------ timeline
async def test_account_timeline(client, world):
    deal, owner = await new_deal(client, world)
    await log(client, owner, deal["id"], "call")
    await move(client, world.admin, deal["id"], "qualification", override=True, reason="seed")
    r = await client.get(f"/accounts/{deal['account_id']}/timeline", headers=headers(owner))
    assert r.status_code == 200, r.text
    t = r.json()
    kinds = {e["kind"] for e in t["events"]}
    assert {"stage", "activity", "ownership"} <= kinds
    assert [b["stage_key"] for b in t["bands"]] == ["lead", "qualification"]
    assert t["bands"][0]["exit_kind"] == "override" and t["bands"][1]["to"] is None
    assert t["engagement"] and t["engagement"][0]["count"] == 1
    assert [e["at"] for e in t["events"]] == sorted(e["at"] for e in t["events"])
    assert (
        await client.get(
            f"/accounts/{deal['account_id']}/timeline", headers=headers(world.tl_south)
        )
    ).status_code == 404
    assert (
        await client.get(f"/accounts/{deal['account_id']}/timeline", headers=headers(world.admin))
    ).status_code == 200
    _ = actor_by_id
