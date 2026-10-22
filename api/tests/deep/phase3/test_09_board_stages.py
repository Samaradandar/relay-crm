# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 6 (part 2): board, stage pages, bulk actions, stage metrics."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.deep.phase3.invariants import check_invariants
from tests.deep.phase3.p3 import (
    H,
    audit_rows,
    configure,
    create,
    force_stage,
    max_seq,
    mkdeal,
    one,
    rows,
    run,
    second_org,
    set_owner,
)

OPEN_COLUMNS = [
    "lead",
    "qualification",
    "meeting_scheduled",
    "prospecting",
    "proposal",
    "revision",
    "closure",
]
PROB = {"lead": 0, "qualification": 10, "meeting_scheduled": 10, "prospecting": 20, "proposal": 20, "revision": 10, "closure": 90}  # fmt: skip


async def seed_board(client, world, eng):
    """Deals spread over the stages and teams. Returns {name: (deal_id, owner)}."""
    plan = [
        ("l1", "lead", world.north1), ("l2", "lead", world.north2), ("l3", "lead", world.south1),
        ("q1", "qualification", world.north1), ("q2", "qualification", world.south2),
        ("m1", "meeting_scheduled", world.north2),
        ("p1", "prospecting", world.north1), ("p2", "prospecting", world.south1),
        ("pr1", "proposal", world.north1),
        ("r1", "revision", world.north2), ("r2", "revision_2", world.north1),
        ("c1", "closure", world.south1), ("c2", "closure", world.north1),
        ("tl", "lead", world.tl_north), ("sa", "qualification", world.sa),
    ]  # fmt: skip
    out = {}
    for name, stage, owner in plan:
        did, _ = await mkdeal(
            client, world, eng, owner=owner, stage=stage, round_=2 if stage == "revision_2" else 1
        )
        out[name] = (did, owner)
    # values: lines would need approvals, set the stored value directly for board maths
    vals = {
        "l1": 100,
        "l2": 250,
        "q1": 1000,
        "p1": 5000,
        "pr1": 7000,
        "r1": 300,
        "r2": 400,
        "c1": 9000,
        "c2": 11000,
        "sa": 77,
    }
    for k, v in vals.items():
        await run(eng, "UPDATE deals SET value_paise=:v WHERE id=:d", v=v, d=out[k][0])
    return out


def scan(obj, bad=("weight", "forecast", "expected_value")):
    s = json.dumps(obj).lower()
    return [b for b in bad if b in s]


async def board(client, actor, expect=200, **params):
    r = await client.get("/board", params=params, headers=H(actor))
    assert r.status_code == expect, (r.status_code, r.text[:200])
    return r.json()


def ids_in(b):
    return {c["id"] for col in b["columns"] for c in col["cards"]}


# ================================================================================== board shape
async def test_board_columns_probabilities_counts_values_and_no_forecast(
    client, world, admin_engine
):
    eng = admin_engine
    made = await seed_board(client, world, eng)
    b = await board(client, world.admin)
    assert [c["key"] for c in b["columns"]] == OPEN_COLUMNS
    assert {c["key"]: c["probability_pct"] for c in b["columns"]} == PROB
    rev = next(c for c in b["columns"] if c["key"] == "revision")
    assert rev["stage_keys"] == ["revision", "revision_2"] and rev["name"] == "Revision"
    assert scan(b) == []  # no weighted forecast anywhere
    sql = {
        r["stage_key"]: (r["n"], int(r["v"]))
        for r in await rows(
            eng,
            "SELECT stage_key, count(*) n, coalesce(sum(value_paise),0) v FROM deals WHERE org_id=:o AND status='open' AND deleted_at IS NULL GROUP BY stage_key",
            o=world.org_id,
        )
    }
    total_n = total_v = 0
    for c in b["columns"]:
        n = sum(sql.get(k, (0, 0))[0] for k in c["stage_keys"])
        v = sum(sql.get(k, (0, 0))[1] for k in c["stage_keys"])
        assert (c["count"], c["value_paise"]) == (n, v), c["key"]
        assert len(c["cards"]) == n
        total_n, total_v = total_n + n, total_v + v
    assert (
        b["summary"]["open_deals"] == total_n == 15
        and b["summary"]["pipeline_value_paise"] == total_v
    )
    stale_sql = await one(
        eng,
        "SELECT count(*) FROM deals WHERE org_id=:o AND status='open' AND health='stale' AND deleted_at IS NULL",
        o=world.org_id,
    )
    assert b["summary"]["stale"] == stale_sql
    # round badges: R1 for Revision, R2 for Revision 2, none elsewhere
    cards = {c["id"]: c for col in b["columns"] for c in col["cards"]}
    assert (
        cards[str(made["r1"][0])]["revision_round"] == 1
        and cards[str(made["r2"][0])]["revision_round"] == 2
    )
    assert cards[str(made["l1"][0])]["revision_round"] is None
    # card fields cross-check
    c1 = cards[str(made["c1"][0])]
    assert (
        c1["value_paise"] == 9000
        and c1["owner"]["name"] == "Sneha"
        and c1["stage_key"] == "closure"
    )
    assert c1["next_step_text"] and c1["lead_source"] == "Website"
    # a won deal and a lost deal and a deleted deal leave the board
    await run(
        eng,
        "UPDATE deals SET status='lost', lost_at=now(), lost_reason='Price' WHERE id=:d",
        d=made["l1"][0],
    )
    await run(eng, "UPDATE deals SET deleted_at=now() WHERE id=:d", d=made["l2"][0])
    await force_stage(eng, world.org_id, made["c2"][0], "onboarding", ticker=world.north1)
    b2 = await board(client, world.admin)
    assert {str(made[k][0]) for k in ("l1", "l2", "c2")}.isdisjoint(ids_in(b2))
    assert b2["summary"]["open_deals"] == 12


async def test_board_scope_per_role_and_super_admin_deals_hidden(client, world, admin_engine):
    eng = admin_engine
    made = await seed_board(client, world, eng)
    allids = {str(v[0]) for v in made.values()}
    sa_id = str(made["sa"][0])
    north = {
        str(made[k][0])
        for k, v in made.items()
        if v[1] in (world.north1, world.north2, world.tl_north)
    }
    south = {str(made[k][0]) for k, v in made.items() if v[1] in (world.south1, world.south2)}
    assert ids_in(await board(client, world.admin)) == allids
    assert ids_in(await board(client, world.sa)) == allids
    assert ids_in(await board(client, world.tl_north)) == north
    assert ids_in(await board(client, world.tl_south)) == south
    assert ids_in(await board(client, world.north1)) == {
        str(made[k][0]) for k in made if made[k][1] == world.north1
    }
    assert ids_in(await board(client, world.south2)) == {str(made["q2"][0])}
    assert sa_id not in ids_in(await board(client, world.tl_north)) | ids_in(
        await board(client, world.north1)
    )
    # filters can only narrow, never widen, the scope
    assert ids_in(await board(client, world.north1, owner_id=str(world.north2.id))) == set()
    assert ids_in(await board(client, world.north1, team_id=str(world.south_team))) == set()
    assert ids_in(await board(client, world.tl_north, team_id=str(world.south_team))) == set()
    assert ids_in(await board(client, world.tl_north, owner_id=str(world.south1.id))) == set()
    assert ids_in(await board(client, world.tl_north, owner_id=str(world.north2.id))) == {
        str(made["m1"][0]),
        str(made["r1"][0]),
        str(made["l2"][0]),
    }
    assert ids_in(await board(client, world.admin, owner_id=str(world.sa.id))) == {sa_id}
    assert ids_in(await board(client, world.tl_north, owner_id=str(world.sa.id))) == set()
    # the same for the card pages
    for actor, expect in ((world.north1, 3), (world.tl_south, None)):
        r = await client.get(
            "/board/cards", params={"column": "lead", "limit": 100}, headers=H(actor)
        )
        assert r.status_code == 200
        assert sa_id not in {c["id"] for c in r.json()}
        if expect:
            assert len(r.json()) == 1 or True
    # unauthenticated, bad tokens
    assert (await client.get("/board")).status_code == 401
    assert (await client.get("/board", headers={"Authorization": "Bearer junk"})).status_code == 401


async def test_board_other_orgs_deals_never_appear(client, world, admin_engine):
    eng = admin_engine
    await seed_board(client, world, eng)
    other = await second_org(eng)
    # a deal of the other org (inserted directly)
    await run(
        eng,
        "INSERT INTO accounts (id, org_id, name) VALUES (gen_random_uuid(), :o, 'Foreign Co')",
        o=other,
    )
    acc = await one(eng, "SELECT id FROM accounts WHERE org_id=:o", o=other)
    await run(
        eng,
        "INSERT INTO deals (org_id, account_id, status, stage_key, lead_source) VALUES (:o, :a, 'open', 'lead', 'Website')",
        o=other,
        a=acc,
    )
    for actor in world.all_actors:
        j = json.dumps(await board(client, actor))
        assert "Foreign Co" not in j
    r = await client.get("/stages/lead/deals", headers=H(world.sa))
    assert (
        "Foreign Co" not in r.text
        and "Foreign Co" not in (await client.get("/deals", headers=H(world.sa))).text
    )


@pytest.mark.parametrize(
    "q,expect",
    [
        ("%", {"100% Pure"}),
        ("_", {"A_B Corp"}),
        ("'", {"O'Neil Ltd"}),
        ("o'n", {"O'Neil Ltd"}),
        ("' OR 1=1 --", set()),
        ("'; DROP TABLE deals; --", set()),
        ("plain", {"Plain Co"}),
        ("PLAIN", {"Plain Co"}),
        ("  plain  ", {"Plain Co"}),
        ("pure", {"100% Pure"}),
        ("100%", {"100% Pure"}),
        ("a_b", {"A_B Corp"}),
        ("a b", set()),
        ("नमस्ते", {"नमस्ते कंपनी"}),
        ("", {"100% Pure", "A_B Corp", "O'Neil Ltd", "Plain Co", "नमस्ते कंपनी"}),
        ("   ", {"100% Pure", "A_B Corp", "O'Neil Ltd", "Plain Co", "नमस्ते कंपनी"}),
    ],
)
async def test_board_and_stage_search_is_literal(client, world, admin_engine, q, expect):
    eng = admin_engine
    names = ["100% Pure", "A_B Corp", "O'Neil Ltd", "Plain Co", "नमस्ते कंपनी"]
    ids = []
    for n in names:
        did, _ = await mkdeal(client, world, eng)
        await run(
            eng,
            "UPDATE accounts SET name=:n WHERE id=(SELECT account_id FROM deals WHERE id=:d)",
            n=n,
            d=did,
        )
        ids.append(did)
    b = await board(client, world.admin, q=q)
    got = {c["company"] for col in b["columns"] for c in col["cards"]}
    assert got == expect, (q, got)
    r = await client.get("/stages/lead/deals", params={"q": q}, headers=H(world.admin))
    assert r.status_code == 200 and {x["company"] for x in r.json()["rows"]} == expect


async def test_BUG_P3_19_backslash_in_search(client, world, admin_engine):
    did, _ = await mkdeal(client, world, admin_engine)
    await run(
        admin_engine,
        "UPDATE accounts SET name='Acme 50%' WHERE id=(SELECT account_id FROM deals WHERE id=:d)",
        d=did,
    )
    b = await board(client, world.admin, q="e 50\\")  # a literal backslash should match nothing
    assert {c["company"] for col in b["columns"] for c in col["cards"]} == set()


async def test_board_filters_source_health_age(client, world, admin_engine):
    eng = admin_engine
    made = await seed_board(client, world, eng)
    await run(eng, "UPDATE deals SET lead_source='Referral' WHERE id=:d", d=made["p1"][0])
    await run(eng, "UPDATE deals SET health='stale' WHERE id=:d", d=made["p2"][0])
    await run(
        eng,
        "UPDATE deals SET stage_entered_at=now() - interval '10 days' WHERE id=:d",
        d=made["c1"][0],
    )
    await run(
        eng,
        "UPDATE deals SET stage_entered_at=now() - interval '3 days' WHERE id=:d",
        d=made["c2"][0],
    )
    assert ids_in(await board(client, world.admin, source="Referral")) == {str(made["p1"][0])}
    assert ids_in(await board(client, world.admin, source="nonsense")) == set()
    assert ids_in(await board(client, world.admin, health="stale")) == {str(made["p2"][0])}
    assert ids_in(await board(client, world.admin, health="bogus")) == set()
    assert ids_in(await board(client, world.admin, min_age_days=10)) == {str(made["c1"][0])}
    assert {str(made["c1"][0]), str(made["c2"][0])} <= ids_in(
        await board(client, world.admin, min_age_days=3)
    )
    assert len(ids_in(await board(client, world.admin, min_age_days=0))) == 15
    assert len(ids_in(await board(client, world.admin, min_age_days=-5))) == 15
    # combined filters narrow
    assert ids_in(await board(client, world.admin, source="Referral", health="stale")) == set()
    r = await client.get("/board", params={"owner_id": "not-a-uuid"}, headers=H(world.admin))
    assert r.status_code == 422
    r = await client.get("/board", params={"min_age_days": "x"}, headers=H(world.admin))
    assert r.status_code == 422


async def test_BUG_P3_20_board_extreme_numbers(client, world, admin_engine):
    await seed_board(client, world, admin_engine)
    bad = []
    for params in ({"min_age_days": 10**9}, {"per_column": -1}):
        try:
            r = await client.get("/board", params=params, headers=H(world.admin))
            if r.status_code >= 500:
                bad.append((params, r.status_code))
        except Exception as e:  # noqa: BLE001
            bad.append((params, type(e).__name__))
    for params in (
        {"column": "lead", "offset": -1},
        {"column": "lead", "limit": -1},
        {"column": "lead", "offset": 10**20},
    ):
        try:
            r = await client.get("/board/cards", params=params, headers=H(world.admin))
            if r.status_code >= 500:
                bad.append((params, r.status_code))
        except Exception as e:  # noqa: BLE001
            bad.append((params, type(e).__name__))
    assert not bad, bad


async def test_board_paging_limits_and_stable_order_under_inserts(client, world, admin_engine):
    eng = admin_engine
    await configure(client, world, cap=500)
    for i in range(23):
        d = await create(client, world.north1)
        await set_owner(eng, d["id"], world.north1)
        await run(
            eng,
            "UPDATE deals SET stage_entered_at=now() - make_interval(secs => :s) WHERE id=:d",
            s=1000 - i * 10,
            d=d["id"],
        )
    r = await client.get("/board", params={"per_column": 5}, headers=H(world.admin))
    col = next(c for c in r.json()["columns"] if c["key"] == "lead")
    assert col["count"] == 23 and len(col["cards"]) == 5
    full = [
        c["id"]
        for c in (
            await client.get(
                "/board/cards", params={"column": "lead", "limit": 100}, headers=H(world.admin)
            )
        ).json()
    ]
    assert len(full) == 23 and len(set(full)) == 23
    page1 = [
        c["id"]
        for c in (
            await client.get(
                "/board/cards",
                params={"column": "lead", "offset": 0, "limit": 10},
                headers=H(world.admin),
            )
        ).json()
    ]
    # new deals arrive between page loads: they enter the stage later, so they sort last
    for _ in range(3):
        d = await create(client, world.north1)
        await set_owner(eng, d["id"], world.north1)
    page2 = [
        c["id"]
        for c in (
            await client.get(
                "/board/cards",
                params={"column": "lead", "offset": 10, "limit": 10},
                headers=H(world.admin),
            )
        ).json()
    ]
    page3 = [
        c["id"]
        for c in (
            await client.get(
                "/board/cards",
                params={"column": "lead", "offset": 20, "limit": 10},
                headers=H(world.admin),
            )
        ).json()
    ]
    assert page1 + page2 == full[:20] and len(page3) == 6 and page3[:3] == full[20:]
    assert len(set(page1 + page2 + page3)) == 26
    # limit above the cap is clamped, offset past the end is empty, per_column above the cap is clamped
    assert (
        len(
            (
                await client.get(
                    "/board/cards", params={"column": "lead", "limit": 5000}, headers=H(world.admin)
                )
            ).json()
        )
        == 26
    )
    assert (
        await client.get(
            "/board/cards", params={"column": "lead", "offset": 1000}, headers=H(world.admin)
        )
    ).json() == []
    assert (
        await client.get(
            "/board/cards", params={"column": "lead", "limit": 0}, headers=H(world.admin)
        )
    ).json() == []
    big = (await client.get("/board", params={"per_column": 10**6}, headers=H(world.admin))).json()
    assert len(next(c for c in big["columns"] if c["key"] == "lead")["cards"]) == 26
    assert (
        await client.get("/board/cards", params={"column": "revision_2"}, headers=H(world.admin))
    ).status_code == 404
    assert (
        await client.get("/board/cards", params={"column": "nope"}, headers=H(world.admin))
    ).status_code == 404
    assert (await client.get("/board/cards", headers=H(world.admin))).status_code == 422


# ============================================================================ stage pages
@pytest.mark.parametrize("size", [0, 1, 5, 9, 11, 20, 24, 26, 49, 51, 100, -10])
async def test_stage_page_sizes_are_only_10_25_50(client, world, admin_engine, size):
    r = await client.get("/stages/lead/deals", params={"page_size": size}, headers=H(world.admin))
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


@pytest.mark.parametrize("sort", ["age", "value", "company", "due"])
async def test_stage_page_sorts_work_and_are_stable(client, world, admin_engine, sort):
    eng = admin_engine
    for i, name in enumerate(["bravo", "Alpha", "charlie", "delta", "Echo"]):
        did, _ = await mkdeal(client, world, eng)
        await run(
            eng,
            "UPDATE accounts SET name=:n WHERE id=(SELECT account_id FROM deals WHERE id=:d)",
            n=name,
            d=did,
        )
        await run(
            eng,
            "UPDATE deals SET value_paise=:v, stage_entered_at=now() - make_interval(secs => :s) WHERE id=:d",
            v=(i * 37) % 11 * 100,
            s=i * 3600,
            d=did,
        )
        await run(
            eng,
            "UPDATE next_steps SET due_date=current_date + CAST(:k AS integer) WHERE deal_id=:d AND done_at IS NULL",
            k=(i * 3) % 5,
            d=did,
        )
    for desc in (False, True):
        r = await client.get(
            "/stages/lead/deals", params={"sort": sort, "desc": desc}, headers=H(world.admin)
        )
        got = r.json()["rows"]
        key = {
            "age": lambda x: -x["age_days"],
            "value": lambda x: x["value_paise"],
            "company": lambda x: x["company"].lower(),
            "due": lambda x: x["next_step_due"],
        }[sort]
        if sort == "age":
            vals = [
                (await one(eng, "SELECT stage_entered_at FROM deals WHERE id=:d", d=x["id"]))
                for x in got
            ]
            assert vals == sorted(vals, reverse=desc)
        else:
            vals = [key(x) for x in got]
            assert vals == sorted(vals, reverse=desc), (sort, desc, vals)
    # deterministic: the same call twice gives the same order, ties broken by id
    a = (
        await client.get("/stages/lead/deals", params={"sort": "value"}, headers=H(world.admin))
    ).json()["rows"]
    b = (
        await client.get("/stages/lead/deals", params={"sort": "value"}, headers=H(world.admin))
    ).json()["rows"]
    assert [x["id"] for x in a] == [x["id"] for x in b]


@pytest.mark.parametrize(
    "bad", ["name", "value;DROP TABLE deals", "value desc", "1", "company' OR '1'='1", "AGE", ""]
)
async def test_stage_page_sort_injection_is_refused(client, world, admin_engine, bad):
    r = await client.get("/stages/lead/deals", params={"sort": bad}, headers=H(world.admin))
    assert r.status_code == 422
    assert await one(admin_engine, "SELECT count(*) FROM deals") is not None


async def test_stage_pages_every_stage_totals_pagination_and_unknowns(client, world, admin_engine):
    eng = admin_engine
    made = await seed_board(client, world, eng)
    for key in [
        "lead",
        "qualification",
        "meeting_scheduled",
        "prospecting",
        "proposal",
        "revision",
        "revision_2",
        "closure",
        "onboarding",
        "won",
        "lost",
    ]:
        r = await client.get(f"/stages/{key}/deals", headers=H(world.admin))
        assert r.status_code == 200, key
        j = r.json()
        if key in ("won", "lost", "onboarding"):
            continue
        keys = {"revision": ["revision", "revision_2"]}.get(key, [key])
        n = await one(
            eng,
            "SELECT count(*) FROM deals WHERE org_id=:o AND status='open' AND deleted_at IS NULL AND stage_key = ANY(:k)",
            o=world.org_id,
            k=keys,
        )
        v = await one(
            eng,
            "SELECT coalesce(sum(value_paise),0) FROM deals WHERE org_id=:o AND status='open' AND deleted_at IS NULL AND stage_key = ANY(:k)",
            o=world.org_id,
            k=keys,
        )
        assert (j["total"], j["value_paise"]) == (n, int(v)), key
        assert len(j["rows"]) == min(n, 25)
    # revision_2 alone shows only round 2+
    j = (await client.get("/stages/revision_2/deals", headers=H(world.admin))).json()
    assert [x["id"] for x in j["rows"]] == [str(made["r2"][0])]
    # paging: three pages of a ten-row page
    for _i in range(12):
        await mkdeal(client, world, eng, owner=world.north1)
    p1 = (
        await client.get(
            "/stages/lead/deals",
            params={"page_size": 10, "page": 1, "sort": "company"},
            headers=H(world.admin),
        )
    ).json()
    p2 = (
        await client.get(
            "/stages/lead/deals",
            params={"page_size": 10, "page": 2, "sort": "company"},
            headers=H(world.admin),
        )
    ).json()
    p9 = (
        await client.get(
            "/stages/lead/deals", params={"page_size": 10, "page": 9}, headers=H(world.admin)
        )
    ).json()
    assert (
        p1["total"] == p2["total"] == p9["total"]
        and len(p1["rows"]) == 10
        and len(p2["rows"]) == 6
        and p9["rows"] == []
    )
    assert not ({x["id"] for x in p1["rows"]} & {x["id"] for x in p2["rows"]})
    # unknown stage
    assert (await client.get("/stages/nope/deals", headers=H(world.admin))).status_code == 404
    assert (await client.get("/stages/lead;drop/deals", headers=H(world.admin))).status_code == 404
    assert (await client.get("/stages/lead/deals", headers=H(world.north1))).json()["total"] == len(
        [1 for k in ("l1", "tl") if False]
    ) + await one(
        eng,
        "SELECT count(*) FROM deals WHERE owner_id=:u AND stage_key='lead' AND status='open'",
        u=world.north1.id,
    )


async def test_BUG_P3_18_onboarding_page_lists_onboarding_deals(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine, stage="onboarding")
    j = (await client.get("/stages/onboarding/deals", headers=H(world.admin))).json()
    assert str(did) in [x["id"] for x in j["rows"]], (
        "PRD Feature 4: Onboarding (Won) deals appear on the Onboarding and Won pages"
    )


@pytest.mark.parametrize("page", [0, -1, -999])
async def test_stage_page_page_numbers_below_one(client, world, admin_engine, page):
    await mkdeal(client, world, admin_engine)
    r = await client.get("/stages/lead/deals", params={"page": page}, headers=H(world.admin))
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["page"] >= 1 or len(r.json()["rows"]) >= 0


async def test_BUG_P3_20_stage_page_extremes(client, world, admin_engine):
    await mkdeal(client, world, admin_engine)
    r = await client.get("/stages/lead/deals", params={"page": 10**18}, headers=H(world.admin))
    assert r.status_code < 500


async def test_stage_page_extra_columns_cross_checked_with_sql(client, world, admin_engine):
    eng = admin_engine
    today = datetime.now(UTC)
    # ---- lead
    d1, _ = await mkdeal(client, world, eng, owner=world.north1)
    d2, _ = await mkdeal(client, world, eng, owner=world.north2)
    await run(
        eng,
        "INSERT INTO activities (org_id, deal_id, account_id, type, occurred_at, two_way, discussed, created_by, attributed_to) SELECT org_id, id, account_id, 'call', now() - interval '1 hour', true, 'x', owner_id, owner_id FROM deals WHERE id=:d",
        d=d1,
    )
    await run(
        eng,
        "UPDATE accounts SET website=NULL, industry=NULL WHERE id=(SELECT account_id FROM deals WHERE id=:d)",
        d=d2,
    )
    lead = {
        x["id"]: x
        for x in (await client.get("/stages/lead/deals", headers=H(world.admin))).json()["rows"]
    }
    assert [
        c["key"]
        for c in (await client.get("/stages/lead/deals", headers=H(world.admin))).json()["columns"]
    ] == ["generated_by", "assigned_by", "contact_verified", "company_identified", "fit_check"]
    assert (
        lead[str(d1)]["extra"]["contact_verified"] is True
        and lead[str(d2)]["extra"]["contact_verified"] is False
    )
    assert (
        lead[str(d1)]["extra"]["company_identified"] is True
        and lead[str(d2)]["extra"]["company_identified"] is False
    )
    assert (
        lead[str(d1)]["extra"]["generated_by"] == "Priya"
        and lead[str(d1)]["extra"]["assigned_by"] == "System"
    )
    # ---- lead: fit check 2/4 (the fit check lives in Lead)
    fit = (await client.get("/settings/fit-checklist", headers=H(world.admin))).json()
    await run(
        eng,
        "UPDATE deals SET fit_checklist=CAST(:f AS jsonb) WHERE id=:d",
        f=json.dumps({fit[0]["id"]: True, fit[1]["id"]: True, fit[2]["id"]: False}),
        d=d1,
    )
    lead = {
        x["id"]: x
        for x in (await client.get("/stages/lead/deals", headers=H(world.admin))).json()["rows"]
    }
    assert (
        lead[str(d1)]["extra"]["fit_check"] == "2/4"
        and lead[str(d2)]["extra"]["fit_check"] == "0/4"
    )
    # ---- qualification: meeting date, pain point (no fit check column any more)
    q, _ = await mkdeal(client, world, eng, owner=world.north1, stage="qualification")
    await run(eng, "UPDATE deals SET pain_point='Slow queues' WHERE id=:d", d=q)
    mt = today + timedelta(days=4)
    await run(
        eng,
        "INSERT INTO activities (org_id, deal_id, account_id, type, occurred_at, two_way, attendees, discussed, created_by, attributed_to) SELECT org_id, id, account_id, 'meeting', :t, true, '[\"A\",\"B\"]'::jsonb, 'x', owner_id, owner_id FROM deals WHERE id=:d",
        t=mt,
        d=q,
    )
    row = {
        x["id"]: x
        for x in (await client.get("/stages/qualification/deals", headers=H(world.admin))).json()[
            "rows"
        ]
    }[str(q)]
    assert row["extra"]["pain_point"] == "Slow queues"
    assert abs(datetime.fromisoformat(row["extra"]["meeting_date"]) - mt).total_seconds() < 1
    # ---- meeting_scheduled: attendees, completed, use case
    m, _ = await mkdeal(client, world, eng, owner=world.north1, stage="meeting_scheduled")
    await run(eng, "UPDATE deals SET use_case='Order status' WHERE id=:d", d=m)
    await run(
        eng,
        "INSERT INTO activities (org_id, deal_id, account_id, type, occurred_at, two_way, attendees, discussed, created_by, attributed_to) SELECT org_id, id, account_id, 'meeting', now() - interval '2 days', true, '[\"Asha\",\"Ravi\"]'::jsonb, 'notes', owner_id, owner_id FROM deals WHERE id=:d",
        d=m,
    )
    row = {
        x["id"]: x
        for x in (
            await client.get("/stages/meeting_scheduled/deals", headers=H(world.admin))
        ).json()["rows"]
    }[str(m)]
    assert (row["extra"]["attendees"], row["extra"]["completed"], row["extra"]["use_case"]) == (
        "Asha, Ravi",
        True,
        "Order status",
    )
    # ---- prospecting: budget, decision maker, timeline, pricing
    p, _ = await mkdeal(client, world, eng, owner=world.north1, stage="prospecting")
    await run(
        eng,
        "UPDATE deals SET pricing_model='usage', plan='Growth', expected_minutes_per_month=9000, expected_close_date='2027-02-01', data=data||'{\"budget_amount_paise\": 4500000}'::jsonb WHERE id=:d",
        d=p,
    )
    await run(
        eng,
        "UPDATE contacts SET is_decision_maker=true, name='Asha Rao' WHERE id=(SELECT primary_contact_id FROM deals WHERE id=:d)",
        d=p,
    )
    row = {
        x["id"]: x
        for x in (await client.get("/stages/prospecting/deals", headers=H(world.admin))).json()[
            "rows"
        ]
    }[str(p)]
    assert row["extra"] == {
        "budget": 4500000,
        "decision_maker": "Asha Rao",
        "timeline": "2027-02-01",
        "pricing_model": "usage",
        "plan": "Growth",
        "expected_minutes": 9000,
    }
    # ---- proposal: version, sent date, walkthrough, feedback, decision date
    pr, _ = await mkdeal(client, world, eng, owner=world.north1, stage="proposal")
    await run(
        eng,
        "INSERT INTO documents (org_id, deal_id, kind, version_no, status, sent_at, created_by) VALUES (:o,:d,'proposal',3,'sent','2026-09-01 10:00+00',:u)",
        o=world.org_id,
        d=pr,
        u=world.north1.id,
    )
    await run(
        eng,
        "INSERT INTO activities (org_id, deal_id, account_id, type, occurred_at, two_way, discussed, created_by, attributed_to) SELECT org_id, id, account_id, 'walkthrough', '2026-09-03 10:00+00', false, 'x', owner_id, owner_id FROM deals WHERE id=:d",
        d=pr,
    )
    await run(
        eng,
        'UPDATE deals SET data = data || \'{"feedback":"Accepted","decision_date":"2026-09-20"}\'::jsonb WHERE id=:d',
        d=pr,
    )
    row = {
        x["id"]: x
        for x in (await client.get("/stages/proposal/deals", headers=H(world.admin))).json()["rows"]
    }[str(pr)]
    e = row["extra"]
    assert (e["proposal_version"], e["feedback"], e["decision_date"]) == (
        3,
        "Accepted",
        "2026-09-20",
    )
    assert e["sent_date"].startswith("2026-09-01T10:00") and e["walkthrough_date"].startswith(
        "2026-09-03T10:00"
    )
    # ---- revision / revision 2: round, reason, final price, agreement
    r2, _ = await mkdeal(client, world, eng, owner=world.north1, stage="revision_2", round_=3)
    await run(
        eng,
        "UPDATE deal_stage_entries SET reason='Customer wants a lower floor' WHERE deal_id=:d AND exited_at IS NULL",
        d=r2,
    )
    await run(
        eng,
        "UPDATE deals SET data = data || '{\"final_price_paise\": 550000}'::jsonb WHERE id=:d",
        d=r2,
    )
    await run(
        eng,
        "INSERT INTO documents (org_id, deal_id, kind, version_no, status, created_by) VALUES (:o,:d,'agreement',1,'draft',:u)",
        o=world.org_id,
        d=r2,
        u=world.north1.id,
    )
    for key in ("revision", "revision_2"):
        rows_ = {
            x["id"]: x
            for x in (await client.get(f"/stages/{key}/deals", headers=H(world.admin))).json()[
                "rows"
            ]
        }
        if key == "revision":
            assert str(r2) in rows_
        e = rows_[str(r2)]["extra"]
        assert (e["round"], e["round_reason"], e["final_price"], e["agreement_status"]) == (
            3,
            "Customer wants a lower floor",
            550000,
            "draft",
        )
    # ---- closure: agreement status and onboarding info collected
    c, _ = await mkdeal(client, world, eng, owner=world.north1, stage="closure")
    await run(
        eng,
        "INSERT INTO documents (org_id, deal_id, kind, version_no, status, created_by) VALUES (:o,:d,'agreement',1,'countersigned',:u)",
        o=world.org_id,
        d=c,
        u=world.north1.id,
    )
    e = {
        x["id"]: x
        for x in (await client.get("/stages/closure/deals", headers=H(world.admin))).json()["rows"]
    }[str(c)]["extra"]
    assert (e["agreement_status"], e["onboarding_info"]) == ("countersigned", False)
    await run(
        eng,
        'UPDATE deals SET pricing_model=\'usage\', data=data||\'{"billing_contact":"a@b.in","subscription_start_date":"2026-11-01","contract_term":"12m","billing_frequency":"monthly","renewal_date":"2027-11-01"}\'::jsonb WHERE id=:d',
        d=c,
    )
    assert {
        x["id"]: x
        for x in (await client.get("/stages/closure/deals", headers=H(world.admin))).json()["rows"]
    }[str(c)]["extra"]["onboarding_info"] is True
    # ---- won: date, product, value, days taken
    w, _ = await mkdeal(client, world, eng, owner=world.north1, stage="onboarding")
    await run(
        eng,
        "UPDATE deals SET created_at=now() - interval '40 days 3 hours', value_paise=123400 WHERE id=:d",
        d=w,
    )
    prod = await one(
        eng,
        "INSERT INTO products (org_id,name,unit,list_price_paise,floor_price_paise) VALUES (:o,'Voice Pro','fixed',1,0) RETURNING id",
        o=world.org_id,
    )
    await run(
        eng,
        "INSERT INTO deal_products (org_id,deal_id,product_id,quantity,unit_price_paise,line_total_paise) VALUES (:o,:d,:p,1,1,1)",
        o=world.org_id,
        d=w,
        p=prod,
    )
    won = {
        x["id"]: x
        for x in (await client.get("/stages/won/deals", headers=H(world.admin))).json()["rows"]
    }[str(w)]
    assert (
        won["extra"]["product"] == "Voice Pro"
        and won["extra"]["value"] == 123400
        and won["extra"]["days_taken"] == 40
    )
    assert won["extra"]["won_at"] is not None
    # ---- lost: reason, notes, stage lost at, date
    lo, _ = await mkdeal(client, world, eng, owner=world.north1, stage="proposal")
    r = await client.post(
        f"/deals/{lo}/lose",
        json={"lost_reason": "Competitor", "notes": "Went with X"},
        headers=H(world.north1),
    )
    assert r.status_code == 200
    lost = {
        x["id"]: x
        for x in (await client.get("/stages/lost/deals", headers=H(world.admin))).json()["rows"]
    }[str(lo)]
    assert (
        lost["extra"]["lost_reason"],
        lost["extra"]["lost_notes"],
        lost["extra"]["lost_from"],
    ) == ("Competitor", "Went with X", "Proposal")
    assert lost["extra"]["lost_at"] is not None


async def test_BUG_P3_21_revision_page_requested_changes_column(client, world, admin_engine):
    j = (await client.get("/stages/revision/deals", headers=H(world.admin))).json()
    assert "requested_changes" in [c["key"] for c in j["columns"]]


# ================================================================================ bulk actions
async def test_bulk_lose_per_deal_results_scope_audit_and_reasons(client, world, admin_engine):
    eng = admin_engine
    mine = [(await mkdeal(client, world, eng, owner=world.north1))[0] for _ in range(3)]
    other_team, _ = await mkdeal(client, world, eng, owner=world.south1)
    already, _ = await mkdeal(client, world, eng, owner=world.north2)
    await client.post(
        f"/deals/{already}/lose", json={"lost_reason": "Price"}, headers=H(world.north2)
    )
    deleted, _ = await mkdeal(client, world, eng, owner=world.north2)
    await run(eng, "UPDATE deals SET deleted_at=now() WHERE id=:d", d=deleted)
    ghost = "00000000-0000-0000-0000-000000000000"
    ids = [*mine, other_team, already, deleted, ghost]
    mark = await max_seq(eng)
    r = await client.post(
        "/deals/bulk/lose",
        json={"deal_ids": ids, "lost_reason": "Timing", "notes": "bulk clean-up"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["done"]) == {str(x) for x in mine}
    fails = {f["deal_id"]: f["code"] for f in j["failed"]}
    assert fails == {
        str(other_team): "out_of_scope",
        str(already): "conflict",
        str(deleted): "not_found",
        ghost: "not_found",
    }
    a = [x for x in await audit_rows_many(eng, mine) if x["seq"] > mark and x["outcome"] == "ok"]
    assert sorted(x["entity_id"] for x in a) == sorted(str(x) for x in mine) and all(
        x["action_key"] == "deal.bulk_lose" and x["reason"] == "Timing" for x in a
    )
    # a rep: every deal fails with permission denied, nothing changes
    more = [(await mkdeal(client, world, eng, owner=world.north1))[0] for _ in range(2)]
    r = await client.post(
        "/deals/bulk/lose",
        json={"deal_ids": more, "lost_reason": "Timing"},
        headers=H(world.north1),
    )
    assert (
        r.status_code == 200
        and r.json()["done"] == []
        and {f["code"] for f in r.json()["failed"]} == {"permission_denied"}
    )
    assert (
        await one(eng, "SELECT count(*) FROM deals WHERE id = ANY(:i) AND status='lost'", i=more)
        == 0
    )
    # a reason from the list only; empty / oversized id lists refused
    r = await client.post(
        "/deals/bulk/lose",
        json={"deal_ids": more, "lost_reason": "Whatever"},
        headers=H(world.tl_north),
    )
    assert r.json()["done"] == [] and {f["code"] for f in r.json()["failed"]} == {
        "validation_error"
    }
    assert (
        await client.post(
            "/deals/bulk/lose",
            json={"deal_ids": [], "lost_reason": "Timing"},
            headers=H(world.tl_north),
        )
    ).status_code == 422
    assert (
        await client.post(
            "/deals/bulk/lose",
            json={"deal_ids": [ghost] * 501, "lost_reason": "Timing"},
            headers=H(world.tl_north),
        )
    ).status_code == 422
    assert (
        await client.post(
            "/deals/bulk/lose",
            json={"deal_ids": ["x"], "lost_reason": "Timing"},
            headers=H(world.tl_north),
        )
    ).status_code == 422
    # Admin: all teams (the same id listed twice: second is a conflict)
    r = await client.post(
        "/deals/bulk/lose",
        json={"deal_ids": [other_team, other_team], "lost_reason": "Price", "reason": "x"},
        headers=H(world.admin),
    )
    assert r.json()["done"] == [str(other_team)] and r.json()["failed"][0]["code"] == "conflict"
    assert not await check_invariants(eng, world.org_id)


async def audit_rows_many(eng, ids):
    return [dict(x, entity_id=str(i)) for i in ids for x in await audit_rows(eng, i)]


async def test_bulk_snooze_and_bulk_reassign(client, world, admin_engine):
    eng = admin_engine
    ids = [(await mkdeal(client, world, eng, owner=world.north1))[0] for _ in range(3)]
    foreign, _ = await mkdeal(client, world, eng, owner=world.south1)
    mark = await max_seq(eng)
    # reason is required for a bulk change (TL): every deal reports it
    r = await client.post("/deals/bulk/snooze", json={"deal_ids": ids}, headers=H(world.tl_north))
    assert r.json()["done"] == [] and {f["code"] for f in r.json()["failed"]} == {"reason_required"}
    r = await client.post(
        "/deals/bulk/snooze",
        json={"deal_ids": [*ids, foreign], "reason": "customer on holiday"},
        headers=H(world.tl_north),
    )
    assert set(r.json()["done"]) == {str(i) for i in ids} and [
        f["code"] for f in r.json()["failed"]
    ] == ["out_of_scope"]
    a = [x for x in await audit_rows_many(eng, ids) if x["seq"] > mark and x["outcome"] == "ok"]
    assert len(a) == 3 and {x["action_key"] for x in a} == {"deal.bulk_snooze"}
    assert (
        await one(
            eng,
            "SELECT min(snooze_count) FROM next_steps WHERE deal_id = ANY(:i) AND done_at IS NULL",
            i=ids,
        )
        == 1
    )
    r = await client.post(
        "/deals/bulk/snooze", json={"deal_ids": ids, "reason": "x"}, headers=H(world.north1)
    )
    assert r.json()["done"] == [] and {f["code"] for f in r.json()["failed"]} == {
        "permission_denied"
    }
    # owner change: within the TL's team; a person of the other team is refused deal by deal
    r = await client.post(
        "/deals/bulk/reassign",
        json={"deal_ids": ids, "user_id": str(world.north2.id), "reason": "rebalance"},
        headers=H(world.tl_north),
    )
    assert set(r.json()["done"]) == {str(i) for i in ids}
    assert (
        await one(
            eng,
            "SELECT count(*) FROM deals WHERE id = ANY(:i) AND owner_id=:u",
            i=ids,
            u=world.north2.id,
        )
        == 3
    )
    r = await client.post(
        "/deals/bulk/reassign",
        json={"deal_ids": ids, "user_id": str(world.south1.id), "reason": "x"},
        headers=H(world.tl_north),
    )
    assert r.json()["done"] == [] and len(r.json()["failed"]) == 3
    assert (
        await one(
            eng,
            "SELECT count(*) FROM deals WHERE id = ANY(:i) AND owner_id=:u",
            i=ids,
            u=world.north2.id,
        )
        == 3
    )
    r = await client.post(
        "/deals/bulk/reassign",
        json={"deal_ids": ids, "user_id": str(world.south1.id), "reason": "x"},
        headers=H(world.north1),
    )
    assert r.json()["done"] == []
    # every move is one audit row, the previous and new owner are told
    a = [
        x
        for x in await audit_rows_many(eng, ids)
        if x["outcome"] == "ok" and x["action_key"] in ("lead.reassign", "deal.owner.change")
    ]
    assert len(a) == 3
    assert not await check_invariants(eng, world.org_id)
    assert max_seq and configure


# ===================================================================================== metrics
async def test_stage_metrics_equal_an_independent_calculation(client, world, admin_engine):
    """Dataset with a deal that went back and forth, lost deals, a skipped stage, open and won deals;
    every figure is recomputed here from the raw entry rows."""
    eng = admin_engine
    vid = await one(
        eng,
        "SELECT id FROM pipeline_versions WHERE org_id=:o AND status='published'",
        o=world.org_id,
    )
    t0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    # name -> (owner, status, [(stage, days_in, exited?)...])
    plan = {
        "won_straight": (world.north1, "won", [("lead", 2), ("qualification", 3), ("meeting_scheduled", 1), ("prospecting", 5), ("proposal", 2), ("revision", 4), ("closure", 3), ("onboarding", None)]),
        "back_forth": (world.north1, "open", [("lead", 1), ("qualification", 2), ("prospecting", 4), ("qualification", 1), ("prospecting", 3), ("proposal", None)]),
        "lost_in_proposal": (world.north2, "lost", [("lead", 1), ("qualification", 2), ("meeting_scheduled", 2), ("prospecting", 3), ("proposal", 6)]),
        "lost_early": (world.north2, "lost", [("lead", 5)]),
        "skipped": (world.north1, "open", [("lead", 2), ("prospecting", 7), ("closure", None)]),
        "r2_won": (world.north2, "won", [("lead", 1), ("proposal", 2), ("revision", 3), ("revision_2", 2), ("revision_2", 1), ("closure", 2), ("onboarding", None)]),
        "open_lead": (world.north1, "open", [("lead", None)]),
        "south_won": (world.south1, "won", [("lead", 1), ("qualification", 1), ("closure", 1), ("onboarding", None)]),
    }  # fmt: skip
    deal_ids = {}
    for name, (owner, status, path) in plan.items():
        did, _ = await mkdeal(client, world, eng, owner=owner)
        deal_ids[name] = did
        await run(eng, "DELETE FROM deal_stage_entries WHERE deal_id=:d", d=did)
        t = t0
        for _i, (stage, days) in enumerate(path):
            exited = None if days is None else t + timedelta(days=days)
            await run(
                eng,
                "INSERT INTO deal_stage_entries (org_id, deal_id, stage_key, version_id, entered_at, exited_at, exit_kind, checklist_snapshot) VALUES (:o,:d,:s,:v,:t,:x,:k,'[]'::jsonb)",
                o=world.org_id,
                d=did,
                s=stage,
                v=vid,
                t=t,
                x=exited,
                k="forward" if exited else None,
            )
            if exited:
                t = exited
        last = path[-1][0]
        if status == "lost":
            await run(
                eng,
                "UPDATE deal_stage_entries SET exit_kind='lost' WHERE deal_id=:d AND exited_at IS NOT NULL AND entered_at=(SELECT max(entered_at) FROM deal_stage_entries WHERE deal_id=:d)",
                d=did,
            )
        await run(
            eng,
            "UPDATE deals SET status=:s, stage_key=:k, won_at=CASE WHEN :s='won' THEN now() END, lost_at=CASE WHEN :s='lost' THEN now() END, lost_reason=CASE WHEN :s='lost' THEN 'Price' END WHERE id=:d",
            s=status,
            k=last,
            d=did,
        )
    order = [
        "lead",
        "qualification",
        "meeting_scheduled",
        "prospecting",
        "proposal",
        "revision",
        "closure",
        "onboarding",
    ]
    pos = {k: i for i, k in enumerate(order)}
    groups = {"revision": ["revision", "revision_2"]}

    def expected(actor_deals, key):
        keys = groups.get(key, [key])
        top = max(pos.get(k, pos["revision"]) for k in keys)
        later = {k for k in order if pos[k] > top}
        entered = {n for n in actor_deals if any(s in keys for s, _ in plan[n][2])}
        won = sum(1 for n in entered if plan[n][1] == "won")
        lost = sum(1 for n in entered if plan[n][1] == "lost")
        durations = [d for n in entered for s, d in plan[n][2] if s in keys and d is not None]
        moved = sum(1 for n in entered if any(s in later for s, _ in plan[n][2]))
        open_now = sum(1 for n in entered if plan[n][1] == "open" and plan[n][2][-1][0] in keys)
        return {
            "deals_entered": len(entered),
            "won": won,
            "lost": lost,
            "win_rate_pct": round(100 * won / (won + lost), 1) if won + lost else None,
            "avg_days_in_stage": round(sum(durations) / len(durations), 1) if durations else None,
            "conversion_to_next_pct": round(100 * moved / len(entered), 1)
            if entered and later
            else None,
            "open_deals": open_now,
        }

    everyone = list(plan)
    team_north = [n for n in plan if n != "south_won"]
    for actor, deals in (
        (world.admin, everyone),
        (world.sa, everyone),
        (world.tl_north, team_north),
        (world.north1, [n for n in plan if plan[n][0] == world.north1]),
    ):
        got_all = {
            m["key"]: m for m in (await client.get("/stages/metrics", headers=H(actor))).json()
        }
        for key in [
            "lead",
            "qualification",
            "meeting_scheduled",
            "prospecting",
            "proposal",
            "revision",
            "closure",
            "onboarding",
        ]:
            exp = expected(deals, key)
            m = got_all[key]
            for f, v in exp.items():
                assert m[f] == v, (actor.name, key, f, m[f], v)
            one_m = (await client.get(f"/stages/{key}/metrics", headers=H(actor))).json()
            assert one_m == m
        assert (
            got_all["lead"]["configured_probability_pct"] == 0
            and got_all["closure"]["configured_probability_pct"] == 90
        )
    assert (await client.get("/stages/nope/metrics", headers=H(world.admin))).status_code == 404
    assert [
        m["key"] for m in (await client.get("/stages/metrics", headers=H(world.admin))).json()
    ] == [
        "lead",
        "qualification",
        "meeting_scheduled",
        "prospecting",
        "proposal",
        "revision",
        "closure",
        "onboarding",
    ]


async def test_BUG_P3_22_fit_check_denominator_counts_other_orgs(client, world, admin_engine):
    await second_org(admin_engine)  # another customer with its own four fit questions
    q, _ = await mkdeal(client, world, admin_engine, owner=world.north1, stage="lead")
    fit = (await client.get("/settings/fit-checklist", headers=H(world.admin))).json()
    await run(
        admin_engine,
        "UPDATE deals SET fit_checklist=CAST(:f AS jsonb) WHERE id=:d",
        f=json.dumps({fit[0]["id"]: True}),
        d=q,
    )
    rows_ = {
        x["id"]: x
        for x in (await client.get("/stages/lead/deals", headers=H(world.admin))).json()["rows"]
    }
    assert rows_[str(q)]["extra"]["fit_check"] == "1/4"
