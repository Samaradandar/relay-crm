# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Load check for the Phase 3 targets: 2,000 open deals, 50 concurrent users.
Targets at the 95th percentile on staging: board < 1.5 s, deal page < 1 s, move < 300 ms.

    python scripts/run_local.py --reset --data load --port 8003 --load 2000   # API + 2,000 deals
    python scripts/loadtest.py run --url http://localhost:8003 --users 50 --seconds 30

Numbers from a laptop's embedded Postgres are an early signal only; the go-live check runs the same
script against staging.
"""

import argparse
import asyncio
import random
import statistics
import sys
import time
from collections import defaultdict
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

THINK_MIN, THINK_MAX = 1.0, 3.0  # seconds a person looks at a screen before the next click
TARGETS_MS = {"board": 1500, "deal": 1000, "move": 300}
SEED_SQL = [
    "CREATE TEMP TABLE _l AS SELECT g, gen_random_uuid() aid, gen_random_uuid() cid, "
    "gen_random_uuid() did FROM generate_series(1, :n) g",
    "CREATE TEMP TABLE _reps AS SELECT id, team_id, row_number() OVER (ORDER BY id) rn "
    "FROM users WHERE org_id = :o AND role = 'user' AND is_active",
    "INSERT INTO accounts (id, org_id, name, domain, website, industry) SELECT aid, :o, "
    "'Load Co ' || g, 'load' || g || '.example', 'https://load' || g || '.example', 'Retail' FROM _l",
    "INSERT INTO contacts (id, org_id, account_id, name, email) SELECT cid, :o, aid, "
    "'Contact ' || g, 'c' || g || '@load.example' FROM _l",
    """INSERT INTO deals (id, org_id, account_id, primary_contact_id, status, stage_key, owner_id,
        team_id, lead_source, pipeline_version_id, value_paise, assigned_at, stage_entered_at,
        health, expected_close_date)
       SELECT l.did, :o, l.aid, l.cid, 'open',
        (ARRAY['lead','lead','lead','qualification','meeting_scheduled','prospecting','proposal',
               'revision','closure'])[1 + l.g % 9],
        r.id, r.team_id, 'Website', v.id, (l.g % 50 + 1) * 100000, now(), now() - (l.g % 20) * interval '1 day',
        (ARRAY['on_track','on_track','at_risk','stale'])[1 + l.g % 4], current_date + (l.g % 90)
       FROM _l l JOIN _reps r ON r.rn = 1 + l.g % (SELECT count(*) FROM _reps)
       JOIN pipeline_versions v ON v.status = 'published'
         AND v.pipeline_id = (SELECT id FROM pipelines WHERE org_id = :o AND key = 'new_business')""",
    """INSERT INTO deal_stage_entries (org_id, deal_id, stage_key, version_id, checklist_snapshot)
       SELECT :o, d.id, d.stage_key, d.pipeline_version_id, '[]' FROM deals d
       WHERE d.id IN (SELECT did FROM _l)""",
    """INSERT INTO next_steps (org_id, deal_id, text, due_date, responsible_id, lane)
       SELECT :o, d.id, 'Follow up', current_date + (l.g % 7) - 2, d.owner_id,
        (ARRAY['your_move','your_move','waiting_on_them'])[1 + l.g % 3]
       FROM _l l JOIN deals d ON d.id = l.did""",
]


async def seed(n: int) -> None:
    engine = create_async_engine(
        get_settings().migration_database_url or get_settings().database_url
    )
    org = get_settings().org_id
    async with engine.begin() as c:
        for sql in SEED_SQL:
            await c.execute(text(sql), {"n": n, "o": org} if ":n" in sql or ":o" in sql else {})
        total = (
            await c.execute(text("SELECT count(*) FROM deals WHERE status='open'"))
        ).scalar_one()
    await engine.dispose()
    print(f"seeded {n} deals; {total} open deals in the database")


async def login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    for attempt in range(3):  # 50 people signing in at once: retry a dropped connection
        try:
            r = await client.post("/auth/dev/login", json={"email": email})
            break
        except httpx.TransportError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5)
    r.raise_for_status()
    body = r.json()
    token = next(v for k, v in body.items() if "token" in k)
    return {"Authorization": f"Bearer {token}"}


class Stats:
    def __init__(self) -> None:
        self.times: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, int] = defaultdict(int)

    def add(self, op: str, ms: float, ok: bool) -> None:
        self.times[op].append(ms)
        if not ok:
            self.errors[op] += 1


async def timed(stats: Stats, op: str, coro: Any) -> httpx.Response | None:
    t0 = time.perf_counter()
    try:
        r = await coro
    except httpx.TransportError:  # a dropped or refused connection counts as a failed request
        stats.add(op, (time.perf_counter() - t0) * 1000, False)
        return None
    # a refused move (409/422: someone else moved it first) is a normal answer, not a failure
    ok = r.status_code < 400 or r.status_code in (409, 422)
    stats.add(op, (time.perf_counter() - t0) * 1000, ok)
    return r


async def worker(
    client: httpx.AsyncClient, email: str, stats: Stats, until: float, rng: random.Random
) -> None:
    h = await login(client, email)
    mine: list[dict[str, Any]] = []
    for _ in range(3):  # the opening list read, retried like the sign-in
        try:
            mine = (await client.get("/deals", params={"limit": 200}, headers=h)).json()
            break
        except httpx.TransportError:
            await asyncio.sleep(0.5)
    me = (await client.get("/me", headers=h)).json()["id"]
    # only the owner moves a deal: supervisors' clicks are reads, so a refusal is not counted
    lead_deals = [
        d["id"] for d in mine
        if d["stage_key"] == "lead" and d["status"] == "open" and (d.get("owner") or {}).get("id") == me
    ]  # fmt: skip
    ids = [d["id"] for d in mine] or [None]
    order = ["qualification", "meeting_scheduled", "prospecting"]
    progress: dict[str, int] = {}
    while time.perf_counter() < until:
        pick = rng.random()
        if pick < 0.35:
            await timed(stats, "board", client.get("/board", headers=h))
        elif pick < 0.7 and ids[0]:
            await timed(stats, "deal", client.get(f"/deals/{rng.choice(ids)}", headers=h))
        elif pick < 0.85:
            await timed(stats, "today", client.get("/today", headers=h))
        elif lead_deals:
            did = rng.choice(lead_deals)
            step = progress.get(did, 0)
            if step < len(order):
                r = await timed(
                    stats, "move",
                    client.post(f"/deals/{did}/move", json={"to_stage": order[step]}, headers=h),
                )  # fmt: skip
                if r is not None and r.status_code == 200:
                    progress[did] = step + 1
                else:
                    lead_deals.remove(did)
            else:
                lead_deals.remove(did)
        else:
            await timed(stats, "stage_page", client.get("/stages/lead/deals", headers=h))
        await asyncio.sleep(rng.uniform(THINK_MIN, THINK_MAX))


async def run(url: str, users: int, seconds: int) -> int:
    personas = [
        f"{n}@relay.local" for n in ("priya", "rahul", "sneha", "vikram", "tara", "tejas", "anita")
    ]
    stats = Stats()
    rng = random.Random(7)
    limits = httpx.Limits(max_connections=users * 2)
    async with httpx.AsyncClient(base_url=url, timeout=60, limits=limits) as client:
        until = time.perf_counter() + seconds
        await asyncio.gather(
            *(worker(client, personas[i % len(personas)], stats, until, rng) for i in range(users))
        )
    bad = 0
    print(f"{'op':<12}{'n':>7}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}{'errors':>8}  target")
    for op, ts in sorted(stats.times.items()):
        ts.sort()
        p95 = ts[int(len(ts) * 0.95) - 1] if len(ts) > 1 else ts[0]
        target = TARGETS_MS.get(op)
        flag = "" if target is None else f"{target} ms {'OK' if p95 <= target else 'MISSED'}"
        bad += int(target is not None and p95 > target) + (1 if stats.errors[op] else 0)
        print(
            f"{op:<12}{len(ts):>7}{statistics.median(ts):>9.0f}{p95:>9.0f}{ts[-1]:>9.0f}{stats.errors[op]:>8}  {flag}"
        )
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--deals", type=int, default=2000)
    r = sub.add_parser("run")
    r.add_argument("--url", default="http://localhost:8003")
    r.add_argument("--users", type=int, default=50)
    r.add_argument("--seconds", type=int, default=30)
    a = ap.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if a.cmd == "seed":
        asyncio.run(seed(a.deals))
    else:
        sys.exit(asyncio.run(run(a.url, a.users, a.seconds)))


if __name__ == "__main__":
    main()
