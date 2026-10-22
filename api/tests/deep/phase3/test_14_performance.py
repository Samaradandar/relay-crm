# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 8 (performance): realistic volumes, timed. Targets from the Phase 3
prompt: board 1.5 s, deal page 1 s, move 300 ms. Numbers are printed (run with -s) and the
generous bounds below only catch order-of-magnitude regressions."""

import statistics
import time

from sqlalchemy import event

from app.core.db import get_engine, get_sessionmaker
from app.modules.deals.today import refresh_health
from tests.deep.phase3.p3 import H, mkdeal, one, run

STAGES = ["lead", "lead", "lead", "qualification", "meeting_scheduled", "prospecting", "proposal", "revision", "closure"]  # fmt: skip


async def bulk(eng, world, n: int, prefix: str = "Perf") -> None:
    reps = [world.north1, world.north2, world.south1, world.south2]
    await run(
        eng,
        "INSERT INTO accounts (org_id, name, domain) SELECT :o, :p || ' ' || g, :p || g || '.example' FROM generate_series(1, :n) g",
        o=world.org_id,
        p=prefix,
        n=n,
    )
    vid = await one(
        eng,
        "SELECT v.id FROM pipeline_versions v JOIN pipelines p ON p.id=v.pipeline_id WHERE p.org_id=:o AND p.key='new_business' AND v.status='published'",
        o=world.org_id,
    )
    for i, rep in enumerate(reps):
        await run(
            eng,
            "INSERT INTO deals (org_id, account_id, status, stage_key, owner_id, team_id, lead_source, pipeline_version_id, value_paise, assigned_at, stage_entered_at, health) "
            "SELECT :o, a.id, 'open', (ARRAY['lead','lead','lead','qualification','meeting_scheduled','prospecting','proposal','revision','closure'])[1 + (row_number() OVER ())::int % 9], "
            ":u, :t, 'Website', :v, (row_number() OVER ())::int % 50 * 100000, now(), now() - ((row_number() OVER ())::int % 20) * interval '1 day', "
            "(ARRAY['on_track','on_track','at_risk','stale'])[1 + (row_number() OVER ())::int % 4] "
            "FROM accounts a WHERE a.org_id=:o AND a.name LIKE :p AND (a.id::text < 'zzzz') AND (abs(hashtext(a.name)) % 4) = :i",
            o=world.org_id, u=rep.id, t=rep.team_id, v=vid, p=f"{prefix} %", i=i,
        )  # fmt: skip
    await run(
        eng,
        "INSERT INTO deal_stage_entries (org_id, deal_id, stage_key, version_id, checklist_snapshot) SELECT d.org_id, d.id, d.stage_key, d.pipeline_version_id, '[]' FROM deals d WHERE d.org_id=:o AND NOT EXISTS (SELECT 1 FROM deal_stage_entries e WHERE e.deal_id=d.id)",
        o=world.org_id,
    )
    await run(
        eng,
        "INSERT INTO next_steps (org_id, deal_id, text, due_date, responsible_id, lane) SELECT d.org_id, d.id, 'Follow up', current_date + (row_number() OVER ())::int % 7 - 2, d.owner_id, 'your_move' FROM deals d WHERE d.org_id=:o AND NOT EXISTS (SELECT 1 FROM next_steps s WHERE s.deal_id=d.id)",
        o=world.org_id,
    )


async def timed(client, actor, path, n=5, **kw):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        r = await client.get(path, headers=H(actor), **kw)
        ts.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, (path, r.status_code, r.text[:100])
    return statistics.median(ts), max(ts)


async def test_reads_with_2000_deals_meet_the_targets(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)  # first: the assignment engine is still free
    await bulk(eng, world, 2000)
    n = await one(eng, "SELECT count(*) FROM deals WHERE org_id=:o", o=world.org_id)
    assert n >= 1900, n
    out = {}
    out["board admin"] = await timed(client, world.admin, "/board")
    out["board rep"] = await timed(client, world.north1, "/board")
    out["deal page"] = await timed(client, world.admin, f"/deals/{did}")
    out["stage page 50 rows"] = await timed(
        client, world.admin, "/stages/lead/deals", params={"page_size": 50}
    )
    out["stage page sorted by value"] = await timed(
        client,
        world.admin,
        "/stages/prospecting/deals",
        params={"page_size": 50, "sort": "value", "desc": True},
    )
    out["stage metrics (all)"] = await timed(client, world.admin, "/stages/metrics")
    out["today"] = await timed(client, world.north1, "/today")
    out["monitoring"] = await timed(client, world.admin, "/monitoring")
    out["teams compare"] = await timed(client, world.admin, "/teams-compare")
    out["deals list 500"] = await timed(client, world.admin, "/deals", params={"limit": 500})
    out["search q=Perf 1"] = await timed(client, world.admin, "/board", params={"q": "Perf 1"})
    for k, (med, mx) in out.items():
        print(f"PERF {k:<28} median {med:7.0f} ms   max {mx:7.0f} ms")
    assert out["board admin"][0] < 1500 and out["board rep"][0] < 1500
    assert out["deal page"][0] < 1000
    for k, (med, _mx) in out.items():
        assert med < 5000, (k, med)


async def test_move_latency_and_deal_page_statement_count(client, world, admin_engine):
    eng = admin_engine
    from tests.deep.phase3.p3 import force_stage

    first = [await mkdeal(client, world, eng) for _ in range(13)]
    await bulk(eng, world, 500)

    counts: list[str] = []
    sync = get_engine().sync_engine

    def listener(c, cur, stmt, p, ctx, many):
        counts.append(stmt)

    event.listen(sync, "before_cursor_execute", listener)
    try:
        did, owner = first[0]
        counts.clear()
        r = await client.get(f"/deals/{did}", headers=H(owner))
        assert r.status_code == 200
        deal_page = len(counts)
        counts.clear()
        r = await client.get("/board", headers=H(owner))
        board = len(counts)
        counts.clear()
        r = await client.get("/stages/lead/deals", params={"page_size": 50}, headers=H(owner))
        stage_page = len(counts)
        print(f"PERF statements: deal page {deal_page}, board {board}, stage page {stage_page}")
        ts = []
        for d, o in first[1:]:
            await force_stage(eng, world.org_id, d, "lead", all_ticked=True, ticker=o)
            counts.clear()
            t = time.perf_counter()
            r = await client.post(
                f"/deals/{d}/move", json={"to_stage": "qualification"}, headers=H(o)
            )
            ts.append((time.perf_counter() - t) * 1000)
            assert r.status_code == 200
        move_stmts = len(counts)
        print(
            f"PERF move: median {statistics.median(ts):.0f} ms, max {max(ts):.0f} ms, {move_stmts} statements"
        )
    finally:
        event.remove(sync, "before_cursor_execute", listener)
    assert deal_page <= 40, f"the deal page runs {deal_page} SQL statements (limit 40)"
    assert statistics.median(ts) < 300


async def test_refresh_health_on_5000_deals_and_publish_moving_2000(client, world, admin_engine):
    eng = admin_engine
    await bulk(eng, world, 5000)
    t = time.perf_counter()
    res = await refresh_health(get_sessionmaker())
    took = time.perf_counter() - t
    print(
        f"PERF refresh_health on {await one(eng, 'SELECT count(*) FROM deals WHERE org_id=:o', o=world.org_id)} deals: {took:.1f} s, changed {res['changed']}"
    )
    assert took < 60
    t2 = time.perf_counter()
    res2 = await refresh_health(get_sessionmaker())
    print(
        f"PERF refresh_health second run: {time.perf_counter() - t2:.1f} s, changed {res2['changed']}"
    )
    # publish a draft that removes a stage holding ~2,000 deals
    from tests.deep.phase3.test_01_pipeline_config import drop_stage, get_v, publish, save, to_draft

    await run(
        eng,
        "UPDATE deals SET stage_key='revision_2' WHERE org_id=:o AND id IN (SELECT id FROM deals WHERE org_id=:o ORDER BY id LIMIT 2000)",
        o=world.org_id,
    )
    await run(
        eng,
        "UPDATE deal_stage_entries SET stage_key='revision_2' WHERE deal_id IN (SELECT id FROM deals WHERE org_id=:o AND stage_key='revision_2')",
        o=world.org_id,
    )
    d = drop_stage(to_draft(await get_v(client, world.admin, 1)), "revision_2")
    no = (await save(client, world.admin, d)).json()["version_no"]
    t3 = time.perf_counter()
    await publish(client, world.admin, no, stage_mapping={"revision_2": "revision"})
    took3 = time.perf_counter() - t3
    moved = await one(
        eng,
        "SELECT count(*) FROM deal_stage_entries e JOIN deals d ON d.id=e.deal_id WHERE d.org_id=:o AND e.reason='config move'",
        o=world.org_id,
    )
    print(f"PERF publish moving {moved} deals: {took3:.1f} s")
    assert moved >= 1900
    assert took3 < 120
