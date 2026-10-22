# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 2 (performance): the duplicate check with 1,000 / 5,000 / 20,000
accounts. pg_trgm is absent on the embedded test database, so the Python trigram scan runs.

Timings are printed and written to perf_results.json next to this file (informational, the
asserts only guard against a collapse, see the report for the 300 ms finding)."""

import json
import random
import statistics
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.trigram import name_grams
from tests.deep.phase2.support import configure, headers, lead

RESULTS = Path(__file__).with_name("perf_results.json")
WORDS_A = [
    "Sunrise",
    "Bharat",
    "Shree",
    "Apex",
    "Delta",
    "Omega",
    "Kaveri",
    "Ganga",
    "Metro",
    "Prime",
    "Royal",
    "Nova",
    "Zenith",
    "Orchid",
    "Maple",
    "Indus",
    "Vega",
    "Lotus",
    "Titan",
    "Vijay",
]
WORDS_B = [
    "Foods",
    "Textiles",
    "Logistics",
    "Steel",
    "Traders",
    "Motors",
    "Pharma",
    "Systems",
    "Infra",
    "Retail",
    "Energy",
    "Plastics",
    "Agro",
    "Exports",
    "Labs",
    "Media",
]
SUFFIX = ["Pvt Ltd", "Private Limited", "LLP", "& Sons", "Industries", "Enterprises", ""]


def names(n: int) -> list[str]:
    rng = random.Random(7)
    out, seen = [], set()
    while len(out) < n:
        nm = f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)} {rng.choice(SUFFIX)} {rng.randint(1, 99999)}".strip()
        if nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


async def load_accounts(engine, org_id, n):
    rows = []
    for i, nm in enumerate(names(n)):
        rows.append(
            {
                "a": uuid.uuid4(),
                "o": org_id,
                "n": nm,
                "d": f"co{i}.example" if i % 3 else None,
                "p": f"+9199{i:08d}"[:13],
                "g": name_grams(nm),  # what the app stores for every account it creates
            }
        )
    async with engine.begin() as c:
        for k in range(0, len(rows), 2000):
            chunk = rows[k : k + 2000]
            await c.execute(
                text(
                    "INSERT INTO accounts (id, org_id, name, domain, website, name_grams) "
                    "VALUES (:a,:o,:n,:d,:d, CAST(:g AS text[]))"
                ),
                chunk,
            )
            await c.execute(
                text(
                    "INSERT INTO contacts (org_id, account_id, name, phone_e164) VALUES (:o,:a,'Contact',:p)"
                ),
                chunk,
            )
    async with engine.connect() as c:  # autovacuum does this in production; the test has no time
        await c.execution_options(isolation_level="AUTOCOMMIT")
        await c.execute(text("ANALYZE accounts"))
    from app.modules.accounts import duplicates

    duplicates._common_cache = None  # re-read the statistics for this table
    return rows


async def timed(fn, repeat=5):
    ts = []
    last = None
    for _ in range(repeat):
        t = time.perf_counter()
        last = await fn()
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts), max(ts), last


@pytest.mark.parametrize("count", [1000, 5000, 20000])
async def test_duplicate_check_and_create_timing(qc, admin_engine, world, count):
    await configure(qc, world)
    rows = await load_accounts(admin_engine, world.org_id, count)
    h = headers(world.north1)
    probe = rows[count // 2]

    async def check_nomatch():
        return await qc.post(
            "/duplicates/check", json={"company": "Quixotic Zebra Holdings"}, headers=h
        )

    async def check_name():
        return await qc.post("/duplicates/check", json={"company": probe["n"] + " Ltd"}, headers=h)

    async def check_phone():
        return await qc.post(
            "/duplicates/check", json={"company": "Quixotic", "phone": probe["p"]}, headers=h
        )

    out = {"accounts": count}
    for label, fn in (
        ("check_no_match", check_nomatch),
        ("check_name_match", check_name),
        ("check_phone_match", check_phone),
    ):
        med, worst, resp = await timed(fn)
        assert resp.status_code == 200
        out[label] = {"median_ms": round(med), "max_ms": round(worst)}

    async def create():
        return await qc.post(
            "/leads", json=lead(f"Quixotic Zebra {uuid.uuid4().hex[:6]}"), headers=h
        )

    med, worst, resp = await timed(create, repeat=3)
    assert resp.status_code in (201, 409)
    out["create_lead"] = {"median_ms": round(med), "max_ms": round(worst)}

    async def search():
        return await qc.get(
            "/accounts", params={"q": "Sunrise Foods"}, headers=headers(world.admin)
        )

    med, worst, resp = await timed(search, repeat=3)
    assert resp.status_code == 200
    out["accounts_search_admin"] = {"median_ms": round(med), "max_ms": round(worst)}
    if count == 20000:  # does the scan stall other users? time a cheap request while 6 checks run
        import asyncio

        async def me():
            t = time.perf_counter()
            r = await qc.get("/me", headers=headers(world.north2))
            assert r.status_code == 200
            return (time.perf_counter() - t) * 1000

        async def burst():
            return await asyncio.gather(*(check_name() for _ in range(6)))

        t0 = time.perf_counter()
        task = asyncio.create_task(burst())
        await asyncio.sleep(0.05)
        lat = await me()
        await task
        out["me_latency_during_6_concurrent_checks_ms"] = round(lat)
        out["6_concurrent_checks_total_ms"] = round((time.perf_counter() - t0) * 1000)
    print("PERF", json.dumps(out))
    data = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    data[str(count)] = out
    RESULTS.write_text(json.dumps(data, indent=1, sort_keys=True))
    # RISK-P2-01 fixed: the GIN prefilter keeps the check well under 300 ms at 20,000 accounts
    assert out["check_no_match"]["median_ms"] < 300


async def test_pg_trgm_path_gives_identical_results_when_the_extension_can_be_enabled(
    qc, admin_engine, world, monkeypatch
):
    from app.modules.accounts import duplicates

    try:
        async with admin_engine.begin() as c:
            await c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"pg_trgm cannot be enabled on the embedded server: {type(exc).__name__}")
    await configure(qc, world)
    await load_accounts(admin_engine, world.org_id, 300)
    monkeypatch.setattr(duplicates, "_trgm_available", False)
    py = []
    for probe in ("Sunrise Foods Pvt Ltd", "Apex Steel", "Zenith Motors LLP 12", "Quixotic"):
        r = await qc.post(
            "/duplicates/check", json={"company": probe}, headers=headers(world.admin)
        )
        py.append(sorted((m["company"], m["similarity"]) for m in r.json()["warn"]))
    monkeypatch.setattr(duplicates, "_trgm_available", True)
    pg = []
    for probe in ("Sunrise Foods Pvt Ltd", "Apex Steel", "Zenith Motors LLP 12", "Quixotic"):
        r = await qc.post(
            "/duplicates/check", json={"company": probe}, headers=headers(world.admin)
        )
        pg.append(sorted((m["company"], m["similarity"]) for m in r.json()["warn"]))
    assert [[c for c, _ in x] for x in py] == [[c for c, _ in x] for x in pg]
