# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Helpers for the Phase 3 deep tests. Copied/extended from tests/modules (never edited there).

The helpers that put a deal "in" a stage use the admin connection directly: the point of the
deep tests is to judge the engine's answer, not to re-prove every proof validator on every case.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from tests.helpers import headers
from tests.modules.journey import (  # noqa: F401  (re-exported for the tests)
    add_doc,
    ago,
    detail,
    due,
    log,
    make_file,
    make_sent_proposal,
    move,
    new_deal,
    patch,
    run_to,
    setup_catalog,
    tick,
)
from tests.modules.test_leads_api import (  # noqa: F401
    actor_by_id,
    audit_keys,
    configure,
    create,
    lead,
)

ORDER = [
    "lead",
    "qualification",
    "meeting_scheduled",
    "prospecting",
    "proposal",
    "revision",
    "revision_2",
    "closure",
    "onboarding",
]
POS = {k: i for i, k in enumerate(ORDER)}
# adjacent forward transitions of version 1 (PRD 9): every deal passes through Revision
FORWARD = {
    ("lead", "qualification"),
    ("qualification", "meeting_scheduled"),
    ("meeting_scheduled", "prospecting"),
    ("prospecting", "proposal"),
    ("proposal", "revision"),
    ("revision", "closure"),
    ("revision_2", "closure"),
    ("closure", "onboarding"),
}
CONDITIONAL = {("revision", "revision_2"), ("revision_2", "revision_2")}


def H(actor) -> dict[str, str]:
    return headers(actor)


async def rows(engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with engine.begin() as c:  # begin(), so INSERT ... RETURNING is committed too
        res = await c.execute(text(sql), params)
        return [dict(r._mapping) for r in res] if res.returns_rows else []


async def one(engine, sql: str, **params: Any) -> Any:
    r = await rows(engine, sql, **params)
    return next(iter(r[0].values())) if r else None


async def run(engine, sql: str, **params: Any) -> None:
    async with engine.begin() as c:
        await c.execute(text(sql), params)


async def set_owner(engine, deal_id, user) -> None:
    await run(
        engine,
        "UPDATE deals SET owner_id=:u, team_id=:t WHERE id=:d",
        u=user.id,
        t=user.team_id,
        d=deal_id,
    )
    await run(
        engine,
        "UPDATE next_steps SET responsible_id=:u WHERE deal_id=:d AND done_at IS NULL",
        u=user.id,
        d=deal_id,
    )


async def published_version_id(engine, org_id, key="new_business"):
    return await one(
        engine,
        "SELECT v.id FROM pipeline_versions v JOIN pipelines p ON p.id=v.pipeline_id "
        "WHERE p.org_id=:o AND p.key=:k AND v.status='published'",
        o=org_id,
        k=key,
    )


async def force_stage(
    engine,
    org_id,
    deal_id,
    stage_key: str,
    *,
    round_: int = 1,
    all_ticked: bool = False,
    ticked: tuple[str, ...] = (),
    ticker=None,
) -> uuid.UUID:
    """Put the deal in `stage_key` exactly as the engine would (new open entry with the stage's
    checklist snapshot, old entry closed), optionally with criteria already ticked."""
    vid = await published_version_id(engine, org_id)
    crit = await rows(
        engine,
        "SELECT key,label,help_text,proof_type,proof_config FROM stage_criteria "
        "WHERE version_id=:v AND stage_key=:s ORDER BY position",
        v=vid,
        s=stage_key,
    )
    snap = [
        {
            "key": c["key"],
            "label": c["label"],
            "help_text": c["help_text"],
            "proof_type": c["proof_type"],
            "proof_config": c["proof_config"],
        }
        for c in crit
    ]
    won = stage_key == "onboarding"
    entry_id = uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "UPDATE deal_stage_entries SET exited_at=now(), exit_kind='forward' "
                "WHERE deal_id=:d AND exited_at IS NULL"
            ),
            {"d": deal_id},
        )
        await c.execute(
            text(
                "INSERT INTO deal_stage_entries (id, org_id, deal_id, stage_key, version_id, "
                "revision_round, entered_at, checklist_snapshot) VALUES (:i,:o,:d,:s,:v,:r,now(),"
                "CAST(:snap AS jsonb))"
            ),
            {
                "i": entry_id,
                "o": org_id,
                "d": deal_id,
                "s": stage_key,
                "v": vid,
                "r": round_,
                "snap": json.dumps(snap),
            },
        )
        await c.execute(
            text(
                "UPDATE deals SET stage_key=:s, pipeline_version_id=:v, revision_round=:r, "
                "stage_entered_at=now(), status=:st, won_at=:w WHERE id=:d"
            ),
            {
                "s": stage_key,
                "v": vid,
                "r": round_,
                "st": "won" if won else "open",
                "w": datetime.now(UTC) if won else None,
                "d": deal_id,
            },
        )
        keys = [s["key"] for s in snap] if all_ticked else list(ticked)
        for k in keys:
            await c.execute(
                text(
                    "INSERT INTO deal_criteria (org_id, deal_id, entry_id, criterion_key, "
                    "ticked_by, proof_ref) VALUES (:o,:d,:e,:k,:u,'{\"forced\": true}')"
                ),
                {
                    "o": org_id,
                    "d": deal_id,
                    "e": entry_id,
                    "k": k,
                    "u": ticker.id if ticker else None,
                },
            )
    return entry_id


async def audit_rows(engine, deal_id) -> list[dict[str, Any]]:
    return await rows(
        engine,
        "SELECT seq, action_key, outcome, reason, old_value, new_value, actor_id, note "
        "FROM audit_log WHERE entity_id=:d ORDER BY seq",
        d=str(deal_id),
    )


async def max_seq(engine) -> int:
    # the identity sequence's last value, not max(seq): a Phase 1 tamper test leaves rows with seq >= 1,000,000
    return int(
        await one(
            engine,
            "SELECT coalesce(pg_sequence_last_value(pg_get_serial_sequence('audit_log','seq')::regclass),0)",
        )
        or 0
    )


async def max_event(engine) -> int:
    return int(await one(engine, "SELECT coalesce(max(id),0) FROM outbox_events") or 0)


async def events_after(engine, min_id: int, deal_id=None) -> list[dict[str, Any]]:
    r = await rows(
        engine,
        "SELECT id, type, payload FROM outbox_events WHERE id>:m ORDER BY id",
        m=min_id,
    )
    if deal_id is not None:
        r = [e for e in r if e["payload"].get("deal_id") == str(deal_id)]
    return r


async def mkdeal(
    client,
    world,
    engine,
    *,
    owner=None,
    stage: str = "lead",
    round_: int = 1,
    all_ticked: bool = False,
    ticked: tuple[str, ...] = (),
    ticker=None,
):
    """A fresh lead owned by `owner` (default north1), standing in `stage`."""
    deal, _ = await new_deal(client, world)
    owner = owner or world.north1
    await set_owner(engine, deal["id"], owner)
    if stage != "lead" or all_ticked or ticked:
        await force_stage(
            engine,
            world.org_id,
            deal["id"],
            stage,
            round_=round_,
            all_ticked=all_ticked,
            ticked=ticked,
            ticker=ticker or owner,
        )
    return deal["id"], owner


async def add_proposal_doc(engine, org_id, deal_id, by) -> None:
    await run(
        engine,
        "INSERT INTO documents (org_id, deal_id, kind, version_no, status, created_by) "
        "VALUES (:o,:d,'proposal',1,'draft',:u)",
        o=org_id,
        d=deal_id,
        u=by.id,
    )


async def set_data(engine, deal_id, **kv: Any) -> None:
    await run(
        engine,
        "UPDATE deals SET data = data || CAST(:j AS jsonb) WHERE id=:d",
        j=json.dumps(kv),
        d=deal_id,
    )


def in_days(n: int) -> str:
    return (datetime.now(UTC) + timedelta(days=n)).isoformat()


async def second_org(engine) -> uuid.UUID:
    """A second organisation with its own pipeline, for isolation tests."""
    from app.modules.pipeline.seeds import seed_org_pipeline

    oid = uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id, name) VALUES (:i, 'Other org')"), {"i": oid})
        await seed_org_pipeline(c, oid)
    return oid


# ---------------------------------------------------------------------------------------------
# Real proof, created through the API (copied from tests/modules/journey.py `run_to`)
PROOF_ORDER = [
    "lead",
    "qualification",
    "meeting_scheduled",
    "prospecting",
    "proposal",
    "revision",
    "closure",
    "onboarding",
]
STAGE_KEYS = {
    "lead": ["basic_info", "company_identified", "contact_verified", "fits_profile"],
    "qualification": ["need_documented", "meeting_scheduled"],
    "meeting_scheduled": ["meeting_completed", "opportunity_defined"],
    "prospecting": ["budget_confirmed", "decision_maker", "timeline_agreed", "pricing_selected"],
    "proposal": [
        "walkthrough_logged",
        "feedback_recorded",
        "proposal_emailed",
        "decision_date_set",
    ],
    "revision": ["terms_accepted", "agreement_sent"],
    "revision_2": ["terms_accepted", "agreement_sent"],
    "closure": ["agreement_countersigned", "advance_received", "onboarding_info"],
    "onboarding": [
        "prerequisite_document",
        "technical_document",
        "kickoff_done",
        "agent_live",
        "first_invoice",
    ],
}


class Rig:
    """One deal with a catalog and fit list, plus helpers to create real proof for any stage."""

    def __init__(self, client, world, eng):
        self.client, self.world, self.eng = client, world, eng
        self.did = None
        self.owner = None
        self.product = None
        self.fit = None
        self.ids: dict[str, str] = {}

    async def start(
        self,
        stage: str = "lead",
        *,
        owner=None,
        prove_before: bool = True,
        prove_self: bool | None = None,
    ):
        c, w = self.client, self.world
        deal, _ = await new_deal(c, w)
        self.did = deal["id"]
        self.owner = owner or w.north1
        await set_owner(self.eng, self.did, self.owner)
        self.product = await setup_catalog(c, w)
        self.fit = (await c.get("/settings/fit-checklist", headers=H(w.admin))).json()
        eff = "revision" if stage == "revision_2" else stage
        if prove_before:
            for st in PROOF_ORDER:
                if st == eff:
                    break
                await self.prove(st)
        if prove_self if prove_self is not None else prove_before:
            await self.prove(eff)
        if stage != "lead":
            await force_stage(
                self.eng,
                w.org_id,
                self.did,
                stage,
                round_=2 if stage == "revision_2" else 1,
                ticker=self.owner,
            )
        return self

    async def req(self, method, url, expect=None, actor=None, **kw):
        r = await self.client.request(method, url, headers=H(actor or self.owner), **kw)
        if expect is not None:
            assert r.status_code == expect, (method, url, r.status_code, r.text[:300])
        return r

    async def deal(self, actor=None):
        return (await self.req("GET", f"/deals/{self.did}", 200, actor)).json()

    async def tick(self, key, expect=204, actor=None, **body):
        return await self.req(
            "POST", f"/deals/{self.did}/criteria/{key}/tick", expect, actor, json=body
        )

    async def crit(self, key):
        d = await self.deal(self.world.admin)
        return next(c for c in d["progress"]["criteria"] if c["key"] == key)

    async def log(self, type_="call", expect=201, **kw):
        body = {"type": type_, "two_way": True, "discussed": "Talked it through", "next_step_text": "Follow up", "next_step_due_date": due(2), **kw}  # fmt: skip
        r = await self.req("POST", f"/deals/{self.did}/activities", expect, json=body)
        return r.json()

    async def patch(self, expect=200, actor=None, **body):
        return await self.req("PATCH", f"/deals/{self.did}", expect, actor, json=body)

    async def prove(self, stage: str):
        c, w, did, owner = self.client, self.world, self.did, self.owner
        if stage == "lead":
            await self.log("call")
            await self.patch(fit_checklist={f["id"]: True for f in self.fit})
        elif stage == "qualification":
            await self.patch(pain_point="Missed calls after 6pm")
            self.ids["future_meeting"] = (
                await self.log("meeting", occurred_at=in_days(3), attendees=["Asha", "Ravi"])
            )["id"]
        elif stage == "meeting_scheduled":
            self.ids["meeting"] = (
                await self.log(
                    "meeting",
                    occurred_at=ago(2),
                    two_way=True,
                    attendees=["Asha", "Ravi"],
                    discussed="Discovery notes",
                )
            )["id"]
            await self.patch(use_case="Inbound order status calls")
        elif stage == "prospecting":
            await self.patch(budget_amount_paise=5000000, budget_source="Annual ops budget", expected_close_date=due(30), expected_close_reason="Quarter-end budget", pricing_model="usage", plan="Growth", expected_minutes_per_month=10000)  # fmt: skip
            d = await self.deal()
            self.ids["contact"] = d["contacts"][0]["id"]
            await self.req(
                "PATCH",
                f"/contacts/{self.ids['contact']}",
                200,
                json={"is_decision_maker": True, "phone": "+919800000001"},
            )
            r = await self.req(
                "POST",
                f"/deals/{did}/lines",
                201,
                json={"product_id": self.product["id"], "quantity": 1000},
            )
            self.ids["line"] = r.json()["lines"][0]["id"]
            self.ids["proposal"] = (await add_doc(c, owner, did, "proposal", "draft"))["id"]
        elif stage == "proposal":
            self.ids["walkthrough"] = (
                await self.log(
                    "meeting",
                    occurred_at=ago(1),
                    attendees=["Asha"],
                    discussed="Proposal walkthrough",
                )
            )["id"]
            await self.req(
                "POST", f"/deals/{did}/proposal-rounds", 201, json={"feedback": "accepted"}
            )
            self.ids["proposal_sent"] = await make_sent_proposal(self.eng, w, did, owner)
            await self.patch(decision_date=due(10))
        elif stage == "revision":
            await self.patch(
                final_price_paise=550000, scope="Voice agent, 10k min", agreed_by="Asha Rao"
            )
            fid = await make_file(self.eng, w, did, owner)
            self.ids["agreement"] = (await add_doc(c, owner, did, "agreement", "sent", fid))["id"]
        elif stage == "closure":
            fid = await make_file(self.eng, w, did, owner)
            self.ids["agreement_cs"] = (
                await add_doc(c, owner, did, "agreement", "countersigned", fid)
            )["id"]
            await self.patch(billing_contact="accounts@acme.in", subscription_start_date=due(15), contract_term="12 months", billing_frequency="monthly", renewal_date=due(380), advance_amount_paise=50000, advance_received_by="Neha Kapoor", advance_received_contact="neha@acme.in", advance_received_on=due(0))  # fmt: skip
            fid2 = await make_file(self.eng, w, did, owner)
            await add_doc(c, owner, did, "advance", "received", fid2)
        elif stage == "onboarding":
            self.ids["kickoff"] = (await self.log("kickoff", occurred_at=ago(1)))["id"]
            await self.patch(prereq_documents="Business requirements", prereq_received_on=due(0), technical_documents="API access and integration spec", technical_received_on=due(0), go_live_date=due(7), live_date=due(8), live_note="Agent is live")  # fmt: skip
            for kind in ("prerequisite", "technical"):
                await add_doc(c, owner, did, kind, "received", await make_file(self.eng, w, did, owner))
            self.ids["invoice"] = (await add_doc(c, owner, did, "invoice", "sent", number="INV-1"))[
                "id"
            ]
