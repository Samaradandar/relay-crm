# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""DEMO DATA ONLY. Fills a development database with made-up companies so every screen has
something to show and every function can be tried: deals in every stage, Won and Lost deals,
waiting leads, pending approvals, a pending transfer, stale and overdue deals, proof-missing and
review-needed warnings.

Everything is created through the real API (in-process), so permissions, reasons, audit rows,
notifications and approvals behave exactly as in production. Nothing in the application depends
on this script; production never runs it. It refuses to run when ENV=production.

    python scripts/run_local.py --reset --data demo --demo      # seed + serve
    python scripts/seed_demo.py                                  # or against the configured DB
"""

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.security import DevAuthProvider, get_auth_provider
from app.modules.leads.models import Deal
from app.modules.users import service as user_service
from app.modules.users.models import Team, User
from app.platform import registry
from app.platform.context import RequestInfo, system_actor
from scripts.seed_dev import seed as seed_dev

EXTRA_REPS = [
    ("Aarav Kapoor", "aarav@relay.local", "Team North"),
    ("Diya Reddy", "diya@relay.local", "Team South"),
]
COMPANIES = [
    ("Saffron Kitchens", "Food & Beverage", "Mumbai"),
    ("Orchid Textiles", "Textiles", "Surat"),
    ("Bluewave Logistics", "Logistics", "Pune"),
    ("Kaveri Healthcare", "Healthcare", "Bengaluru"),
    ("Himalaya Tours", "Travel", "Dehradun"),
    ("Metro Auto Spares", "Automotive", "Chennai"),
    ("Lotus Realty", "Real estate", "Hyderabad"),
    ("Greenfield Agro", "Agriculture", "Nashik"),
    ("Pinnacle Insurance Desk", "Insurance", "Mumbai"),
    ("Sundaram Electronics", "Electronics", "Coimbatore"),
    ("Anand Pharma Distributors", "Pharma", "Ahmedabad"),
    ("Riverside Hotels", "Hospitality", "Goa"),
    ("Vega Education", "Education", "Delhi"),
    ("Nimbus Telecom Retail", "Telecom", "Noida"),
    ("Karma Fitness", "Fitness", "Gurugram"),
    ("Zenith Foods", "Food & Beverage", "Indore"),
    ("Apex Courier", "Logistics", "Kolkata"),
    ("Marigold Salons", "Beauty", "Jaipur"),
    ("Trident Finance", "Finance", "Mumbai"),
    ("Aurora Diagnostics", "Healthcare", "Lucknow"),
    ("Banyan Home Loans", "Finance", "Chennai"),
    ("Cobalt Cabs", "Transport", "Bengaluru"),
    ("Delta Retail Mart", "Retail", "Pune"),
    ("Evergreen Pharmacy", "Pharma", "Kochi"),
    ("Falcon Security", "Security", "Delhi"),
    ("Gulmohar Homes", "Real estate", "Nagpur"),
    ("Horizon Broadband", "Telecom", "Hyderabad"),
    ("Indus Furnishings", "Furniture", "Jaipur"),
    ("Jade Jewellers", "Retail", "Surat"),
    ("Kalinga Seeds", "Agriculture", "Bhubaneswar"),
    ("Lumen Opticals", "Retail", "Mumbai"),
    ("Mosaic Events", "Events", "Goa"),
    ("Nectar Dairy", "Food & Beverage", "Anand"),
    ("Opal Dental Clinics", "Healthcare", "Pune"),
    ("Prism Print House", "Printing", "Chennai"),
    ("Quartz Tiles", "Manufacturing", "Morbi"),
]
SOURCES = ["Website", "Referral", "LinkedIn", "Event", "Cold outbound", "Inbound call"]
ORDER = [
    "lead", "qualification", "meeting_scheduled", "prospecting", "proposal", "revision",
    "closure", "onboarding",
]  # fmt: skip
# scenario per company index: (stage reached, outcome)  outcome: open | won | lost:<reason>
SCENARIOS: dict[int, tuple[str, str]] = {}
for i in range(0, 6):
    SCENARIOS[i] = ("lead", "open")
for i in range(6, 10):
    SCENARIOS[i] = ("qualification", "open")
for i in range(10, 13):
    SCENARIOS[i] = ("meeting_scheduled", "open")
for i in range(13, 17):
    SCENARIOS[i] = ("prospecting", "open")
for i in range(17, 20):
    SCENARIOS[i] = ("proposal", "open")
for i in range(20, 22):
    SCENARIOS[i] = ("revision", "open")
SCENARIOS[22] = ("revision_2", "open")
SCENARIOS[23] = ("closure", "open")
SCENARIOS[24] = ("closure", "open")
for i in (25, 26, 27):
    SCENARIOS[i] = ("onboarding", "won")
SCENARIOS[28] = ("qualification", "lost:Price")
SCENARIOS[29] = ("prospecting", "lost:Competitor")
SCENARIOS[30] = ("proposal", "lost:Budget constraints")
SCENARIOS[31] = ("meeting_scheduled", "lost:Timing")
for i in range(32, 36):
    SCENARIOS[i] = ("lead", "open")


def due(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


class Demo:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.c = client
        self.tokens: dict[str, dict[str, str]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.org = get_settings().org_id

    def h(self, email: str) -> dict[str, str]:
        if email not in self.tokens:
            provider = get_auth_provider()
            assert isinstance(provider, DevAuthProvider)
            self.tokens[email] = {"Authorization": f"Bearer {provider.issue(email)}"}
        return self.tokens[email]

    async def call(
        self, email: str, method: str, path: str, expect: int | tuple[int, ...] = 200, **kw: Any
    ) -> Any:
        r = await self.c.request(method, path, headers=self.h(email), **kw)
        ok = (expect,) if isinstance(expect, int) else expect
        if r.status_code not in ok:
            raise RuntimeError(f"{email} {method} {path} -> {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    async def file(self, deal_id: str, uploader_email: str) -> str:
        """A finished upload record (demo files have no bytes behind them)."""
        fid = uuid.uuid4()
        async with get_sessionmaker()() as s:
            uid = (
                await s.execute(select(User.id).where(User.email == uploader_email))
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO files (id, org_id, owner_entity_type, owner_entity_id, key, filename,"
                    " content_type, size, status, uploaded_by) VALUES (:i, :o, 'deal', :d, :k,"
                    " 'agreement.pdf', 'application/pdf', 20480, 'uploaded', :u)"
                ),
                {"i": fid, "o": self.org, "d": deal_id, "k": f"demo/{fid}", "u": uid},
            )
            await s.commit()
        return str(fid)

    async def log(
        self, who: str, deal: str, kind: str, hours_ago: float = 1, **kw: Any
    ) -> dict[str, Any]:
        body = {
            "type": kind, "two_way": True, "discussed": f"{kind.title()} with the customer about how they work today.",
            "occurred_at": ago(hours_ago), "next_step_text": "Follow up", "next_step_due_date": due(2), **kw,
        }  # fmt: skip
        return await self.call(who, "POST", f"/deals/{deal}/activities", 201, json=body)

    async def tick(self, who: str, deal: str, keys: list[str]) -> None:
        for k in keys:
            await self.call(who, "POST", f"/deals/{deal}/criteria/{k}/tick", 204, json={})

    async def move(self, who: str, deal: str, to: str, **kw: Any) -> None:
        await self.call(who, "POST", f"/deals/{deal}/move", 200, json={"to_stage": to, **kw})


async def main(dispose: bool = True) -> None:
    settings = get_settings()
    if getattr(settings, "env", "") == "production":
        raise SystemExit("seed_demo.py is demo data only and refuses to run in production.")
    registry.setup()
    await seed_dev()
    actor = system_actor(settings.org_id, "Demo seed")
    req = RequestInfo(origin="system_job")
    async with get_sessionmaker()() as s:
        teams = {t.name: t.id for t in (await s.execute(select(Team))).scalars()}
        existing = (await s.execute(select(Deal.id).limit(1))).first()
    if existing:
        print("The database already has deals; demo data was not added (use --reset).")
        if dispose:
            await dispose_engine()
        return
    for name, email, team in EXTRA_REPS:
        async with get_sessionmaker()() as s:
            have = (await s.execute(select(User.id).where(User.email == email))).first()
        if not have:
            await user_service.create_user(
                actor, req, name=name, email=email, role="user", team_id=teams[team], reason="demo"
            )

    from app.main import create_app

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://demo"
    ) as client:
        d = Demo(client)
        admin, sa = "anita@relay.local", "samar@relay.local"
        tl = {"Team North": "tara@relay.local", "Team South": "tejas@relay.local"}
        reps = ["priya@relay.local", "rahul@relay.local", "aarav@relay.local",
                "sneha@relay.local", "vikram@relay.local", "diya@relay.local"]  # fmt: skip
        product = next(
            p for p in await d.call(admin, "GET", "/products") if p["unit"] == "per_minute"
        )  # priced per minute: quantity = expected minutes per month
        fit = await d.call(admin, "GET", "/settings/fit-checklist")
        # route leads by source so both teams get work
        north = str(teams["Team North"])
        south = str(teams["Team South"])
        await d.call(admin, "PUT", "/settings/assignment", json={
            "default_team_id": north, "default_cap": 25, "reason": "demo setup", "groups": [], "caps": {},
            "rules": [{"name": "Events and referrals go South", "priority": 10, "active": True,
                       "match": {"lead_source": "Event"}, "target_type": "team", "target_id": south},
                      {"name": "Referrals go South", "priority": 20, "active": True,
                       "match": {"lead_source": "Referral"}, "target_type": "team", "target_id": south}],
        })  # fmt: skip

        deals: dict[int, dict[str, Any]] = {}
        owners: dict[int, str] = {}
        for i, (company, industry, city) in enumerate(COMPANIES):
            creator = reps[i % len(reps)]
            slug = company.lower().replace(" ", "").replace("&", "")
            lead = await d.call(creator, "POST", "/leads", 201, json={
                "company": company, "website": f"https://www.{slug}.in", "industry": industry, "city": city,
                "contact_name": ["Asha Rao", "Rohit Mehta", "Neha Iyer", "Imran Khan", "Kavita Joshi"][i % 5],
                "contact_role": ["Founder", "Operations head", "CX manager", "Owner", "IT head"][i % 5],
                "email": f"contact@{slug}.in", "phone": f"98{(10000000 + i * 7919) % 100000000:08d}",
                "lead_source": SOURCES[i % len(SOURCES)],
                "next_step_text": "Intro call", "next_step_due_date": due(1 + i % 4),
            })  # fmt: skip
            deals[i] = lead
            owners[i] = (
                next(
                    e
                    for e in reps
                    if lead["owner"] and e.split("@")[0].title() in lead["owner"]["name"]
                )
                if lead["owner"]
                else ""
            )
        users = await d.call(admin, "GET", "/users")
        by_id = {u["id"]: u["email"] for u in users}
        for i, lead in deals.items():
            owners[i] = by_id.get(lead["owner"]["id"], "") if lead["owner"] else ""

        async def prove(i: int, stage: str) -> None:
            """Give deal i real proof for `stage` and tick it."""
            o, did = owners[i], deals[i]["id"]
            if stage == "lead":
                await d.log(o, did, "call", max(1, 30 - i))
                await d.call(o, "PATCH", f"/deals/{did}", json={"fit_checklist": {f["id"]: True for f in fit}})  # fmt: skip
                await d.tick(
                    o, did, ["basic_info", "company_identified", "contact_verified", "fits_profile"]
                )
            elif stage == "qualification":
                await d.call(o, "PATCH", f"/deals/{did}", json={"pain_point": "Follow-ups are slow and inconsistent across the team"})  # fmt: skip
                await d.log(o, did, "meeting", -72, attendees=["Asha Rao", o.split("@")[0].title()])
                await d.tick(o, did, ["need_documented", "meeting_scheduled"])
            elif stage == "meeting_scheduled":
                await d.log(
                    o,
                    did,
                    "meeting",
                    5,
                    discussed="Walked through current volumes and the top five things they need.",
                )
                await d.call(
                    o,
                    "PATCH",
                    f"/deals/{did}",
                    json={"use_case": "Automating routine customer requests"},
                )
                await d.tick(o, did, ["meeting_completed", "opportunity_defined"])
            elif stage == "prospecting":
                await d.call(o, "PATCH", f"/deals/{did}", json={
                    "budget_amount_paise": 4000000 + i * 100000, "budget_source": "Annual operations budget",
                    "expected_close_date": due(25 + i), "expected_close_reason": "Quarter-end budget cycle",
                    "pricing_model": "Usage based", "plan": "Growth", "expected_minutes_per_month": 8000 + i * 500})  # fmt: skip
                contact = (await d.call(o, "GET", f"/deals/{did}"))["contacts"][0]
                await d.call(
                    o, "PATCH", f"/contacts/{contact['id']}", json={"is_decision_maker": True}
                )
                await d.call(
                    o,
                    "POST",
                    f"/deals/{did}/lines",
                    201,
                    json={"product_id": product["id"], "quantity": 8000 + i * 500},
                )
                await d.call(
                    o,
                    "POST",
                    f"/deals/{did}/documents",
                    201,
                    json={"kind": "proposal", "status": "draft", "number": f"P-{100 + i}"},
                )
                await d.tick(
                    o,
                    did,
                    ["budget_confirmed", "decision_maker", "timeline_agreed", "pricing_selected"],
                )
            elif stage == "proposal":
                await d.log(
                    o,
                    did,
                    "walkthrough",
                    3,
                    discussed="Live walkthrough of the proposal and demo of the product.",
                )
                await d.call(
                    o,
                    "PATCH",
                    f"/deals/{did}",
                    json={"feedback": "Accepted", "decision_date": due(7)},
                )
                await d.tick(
                    o, did, ["walkthrough_logged", "feedback_recorded", "decision_date_set"]
                )
            elif stage == "revision":
                await d.call(
                    o,
                    "PATCH",
                    f"/deals/{did}",
                    json={
                        "final_price_paise": 5200000,
                        "scope": "Standard plan, 10,000 units / month",
                        "agreed_by": "Asha Rao",
                    },
                )
                fid = await d.file(did, o)
                await d.call(
                    o,
                    "POST",
                    f"/deals/{did}/documents",
                    201,
                    json={
                        "kind": "agreement",
                        "status": "sent",
                        "file_id": fid,
                        "number": f"A-{200 + i}",
                    },
                )
                await d.tick(o, did, ["terms_accepted", "agreement_sent"])
            elif stage == "closure":
                fid = await d.file(did, o)
                await d.call(
                    o,
                    "POST",
                    f"/deals/{did}/documents",
                    201,
                    json={"kind": "agreement", "status": "countersigned", "file_id": fid},
                )
                await d.call(o, "PATCH", f"/deals/{did}", json={
                    "billing_contact": f"accounts@{deals[i]['company'].lower().replace(' ', '')}.in",
                    "subscription_start_date": due(15), "contract_term": "12 months",
                    "billing_frequency": "Monthly", "renewal_date": due(380)})  # fmt: skip
                await d.tick(o, did, ["agreement_countersigned", "onboarding_info"])
            elif stage == "onboarding":
                await d.log(
                    o,
                    did,
                    "kickoff",
                    2,
                    discussed="Kickoff call: scope, go-live plan and contacts agreed.",
                )
                await d.call(
                    o,
                    "PATCH",
                    f"/deals/{did}",
                    json={
                        "go_live_date": due(7),
                        "live_date": due(8),
                        "live_note": "Live and in use by their team",
                    },
                )
                await d.call(
                    o,
                    "POST",
                    f"/deals/{did}/documents",
                    201,
                    json={
                        "kind": "invoice",
                        "status": "sent",
                        "number": f"INV-{300 + i}",
                        "amount_paise": 5200000,
                    },
                )
                await d.tick(o, did, ["kickoff_done", "agent_live", "first_invoice"])

        for i, (target, outcome) in SCENARIOS.items():
            if deals[i]["status"] != "open" or not owners[i]:
                continue  # waiting in the Pool or unassigned
            o, did = owners[i], deals[i]["id"]
            if target == "lead":
                if i % 3 == 0:
                    await prove(i, "lead")  # contact verified, ready to move
                elif i % 3 == 1:
                    await d.log(o, did, "call", 4)  # call logged, nothing ticked yet
                continue
            order = ORDER if target != "revision_2" else ORDER[:6]
            for stage in order[: order.index(target if target != "revision_2" else "revision")]:
                await prove(i, stage)
                await d.move(o, did, ORDER[ORDER.index(stage) + 1])
            if target == "revision_2":
                await prove(i, "revision")
                await d.move(o, did, "revision_2", reason="Customer asked for revised SLA terms")
            elif target == "onboarding":  # the loop above already walked it into Onboarding
                await prove(i, "onboarding")
            if outcome.startswith("lost:"):
                await d.call(
                    o,
                    "POST",
                    f"/deals/{did}/lose",
                    json={
                        "lost_reason": outcome.split(":", 1)[1],
                        "notes": "Customer decided to go another way.",
                    },
                )

        # ---- situations worth showing in a demo
        open_ids = [
            (i, deals[i]["id"], owners[i])
            for i in deals
            if deals[i]["status"] == "open" and owners[i]
        ]

        async def at(stage: str) -> list[tuple[int, str, str]]:
            out = []
            for i, did, o in open_ids:
                if SCENARIOS[i][0] == stage and SCENARIOS[i][1] == "open":
                    out.append((i, did, o))
            return out

        async def ensure_line(o: str, did: str) -> dict[str, Any]:
            lines = await d.call(o, "GET", f"/deals/{did}/lines")
            if not lines["lines"]:  # these deals have only reached Prospecting: pick a product now
                await d.call(
                    o,
                    "POST",
                    f"/deals/{did}/lines",
                    201,
                    json={"product_id": product["id"], "quantity": 8000},
                )
                lines = await d.call(o, "GET", f"/deals/{did}/lines")
            return lines

        pros = await at("prospecting")
        if pros:  # a discount waiting for the Team Leader, and a big value change waiting too
            i, did, o = pros[0]
            lines = await ensure_line(o, did)
            await d.call(o, "PATCH", f"/deals/{did}/lines/{lines['lines'][0]['id']}", 202,
                         json={"discount_pct": "6", "reason": "Customer compared with a competitor quote"})  # fmt: skip
        if len(pros) > 1:
            i, did, o = pros[1]
            lines = await ensure_line(o, did)
            lid, qty = lines["lines"][0]["id"], lines["lines"][0]["quantity"]
            await d.call(o, "PATCH", f"/deals/{did}/lines/{lid}", 202,
                         json={"quantity": int(qty * 1.5), "reason": "Customer added two more branches"})  # fmt: skip
        if len(pros) > 2:  # a deep discount that needs an Admin
            i, did, o = pros[2]
            lines = await ensure_line(o, did)
            await d.call(o, "PATCH", f"/deals/{did}/lines/{lines['lines'][0]['id']}", 202,
                         json={"discount_pct": "14", "reason": "Strategic logo, wants a multi-year deal"})  # fmt: skip
        revs = await at("revision")
        if revs:  # a pending transfer: the other team's Team Leader asks to take a deal
            i, did, o = revs[0]
            team = next(u["team_id"] for u in users if u["email"] == o)
            asker = tl["Team South" if team == north else "Team North"]
            await d.call(asker, "POST", "/transfers", 202, json={"direction": "take", "deal_id": did,
                         "reason": "This customer's head office is in our region"})  # fmt: skip
        quals = await at("qualification")
        sched = await at("meeting_scheduled")
        if sched:  # proof missing: the meeting behind a Qualification tick is deleted by the TL
            i, did, o = sched[0]
            acts = await d.call(o, "GET", f"/deals/{did}/activities")
            meeting = next(a for a in acts if a["type"] == "meeting")
            team = next(u["team_id"] for u in users if u["email"] == o)
            await d.call(tl["Team North" if team == north else "Team South"], "DELETE",
                         f"/activities/{meeting['id']}", 204, params={"reason": "Logged against the wrong company"})  # fmt: skip
        if len(quals) > 1:  # review needed: a past tick is rejected
            i, did, o = quals[1]
            team = next(u["team_id"] for u in users if u["email"] == o)
            await d.call(tl["Team North" if team == north else "Team South"], "POST",
                         f"/deals/{did}/criteria/contact_verified/verify", 204,
                         json={"decision": "reject", "reason": "That contact was not the right person"})  # fmt: skip
        # age a few deals: stale, overdue and nudge-due
        leads = await at("lead")
        async with get_sessionmaker()() as s:
            if leads:
                await s.execute(
                    text(
                        "UPDATE deals SET last_activity_at = now() - interval '10 days', health='stale' WHERE id=:d"
                    ),
                    {"d": leads[0][1]},
                )
            if len(leads) > 1:
                await s.execute(
                    text(
                        "UPDATE next_steps SET due_date = current_date - 3 WHERE deal_id=:d AND done_at IS NULL"
                    ),
                    {"d": leads[1][1]},
                )
                await s.execute(
                    text("UPDATE deals SET health='at_risk' WHERE id=:d"), {"d": leads[1][1]}
                )
            await s.commit()
        prop = await at("proposal")
        if prop:
            i, did, o = prop[0]
            step = (await d.call(o, "GET", f"/deals/{did}"))["next_step"]
            await d.call(o, "POST", f"/next-steps/{step['id']}/done", 204, json={"next": {
                "text": "Waiting for the signed quote from their finance team", "due_date": due(3), "lane": "waiting_on_them"}})  # fmt: skip
            async with get_sessionmaker()() as s:
                await s.execute(
                    text(
                        "UPDATE next_steps SET lane_since = now() - interval '3 days' WHERE deal_id=:d AND done_at IS NULL"
                    ),
                    {"d": did},
                )
                await s.execute(
                    text(
                        "UPDATE deals SET flags = flags || '{\"nudge_due\": true}'::jsonb WHERE id=:d"
                    ),
                    {"d": did},
                )
                await s.commit()
        # activity from a Super admin-owned deal is hidden from Team Leaders: make one
        if closures := await at("closure"):
            async with get_sessionmaker()() as s:
                sa_id = (await s.execute(select(User.id).where(User.email == sa))).scalar_one()
                await s.execute(
                    text("UPDATE deals SET owner_id=:u, team_id=NULL WHERE id=:d"),
                    {"u": sa_id, "d": closures[-1][1]},
                )
                await s.commit()
        # run the engines once so notifications and health are populated
        from app.modules.deals.today import refresh_health
        from app.platform.outbox.service import dispatch_batch

        for _ in range(20):
            if await dispatch_batch(get_sessionmaker()) == 0:
                break
        await refresh_health(get_sessionmaker())
        for _ in range(20):
            if await dispatch_batch(get_sessionmaker()) == 0:
                break
        board = await d.call(admin, "GET", "/board")
        print(
            f"demo data ready: {sum(c['count'] for c in board['columns'])} open deals on the board, "
            f"{(await d.call(admin, 'GET', '/stages/won/deals'))['total']} won, "
            f"{(await d.call(admin, 'GET', '/stages/lost/deals'))['total']} lost. "
            "Sign in as any @relay.local user (anita = Admin, samar = Super admin, tara/tejas = Team Leaders)."
        )
    if dispose:
        await dispose_engine()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
