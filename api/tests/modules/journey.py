# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Test helpers that walk a deal through the New business pipeline over the real HTTP API."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.modules.leads.service import today_ist
from tests.helpers import headers
from tests.modules.test_leads_api import actor_by_id, configure, create


def due(days: int = 1) -> str:
    return (today_ist() + timedelta(days=days)).isoformat()


def ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


async def new_deal(client, world, **kw) -> tuple[dict[str, Any], Any]:
    """A configured org, one assigned lead (with industry), and the Actor who owns it."""
    await configure(client, world)
    d = await create(
        client,
        world.north1,
        industry="Retail",
        website=f"https://{uuid.uuid4().hex[:8]}-journey.in",
        **kw,
    )
    return d, actor_by_id(world, d["owner"]["id"])


async def log(client, actor, deal_id, type_="call", **kw):
    body = {
        "type": type_,
        "two_way": True,
        "discussed": "Talked it through",
        "next_step_text": "Follow up",
        "next_step_due_date": due(2),
        **kw,
    }
    r = await client.post(f"/deals/{deal_id}/activities", json=body, headers=headers(actor))
    assert r.status_code == 201, r.text
    return r.json()


async def tick(client, actor, deal_id, key, expect=204, **body):
    r = await client.post(
        f"/deals/{deal_id}/criteria/{key}/tick", json=body, headers=headers(actor)
    )
    assert r.status_code == expect, r.text
    return r


async def move(client, actor, deal_id, to, expect=200, **body):
    r = await client.post(
        f"/deals/{deal_id}/move", json={"to_stage": to, **body}, headers=headers(actor)
    )
    assert r.status_code == expect, r.text
    return r


async def patch(client, actor, deal_id, expect=200, **body):
    r = await client.patch(f"/deals/{deal_id}", json=body, headers=headers(actor))
    assert r.status_code == expect, r.text
    return r


async def detail(client, actor, deal_id):
    r = await client.get(f"/deals/{deal_id}", headers=headers(actor))
    assert r.status_code == 200, r.text
    return r.json()


async def make_file(admin_engine, world, deal_id, uploader) -> str:
    """A finished upload attached to the deal (the real signed-upload flow needs S3)."""
    fid = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO files (id, org_id, owner_entity_type, owner_entity_id, key, filename,"
                " content_type, size, status, uploaded_by) VALUES (:i, :o, 'deal', :d, :k, 'f.pdf',"
                " 'application/pdf', 10, 'uploaded', :u)"
            ),
            {"i": fid, "o": world.org_id, "d": str(deal_id), "k": f"k/{fid}", "u": uploader.id},
        )
    return str(fid)


async def make_sent_proposal(admin_engine, world, deal_id, owner) -> str:
    """A proposal that was drafted and emailed (the real send needs a template, a PDF and email,
    none of which these tests are about)."""
    pid = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO proposals (id, org_id, deal_id, version_no, content, status, sent_at,"
                " sent_to, created_by) VALUES (:i, :o, :d, 1, CAST(:c AS jsonb), 'sent', now(),"
                " 'asha@acme.in', :u)"
            ),
            {"i": pid, "o": world.org_id, "d": deal_id, "c": '{"recipient_name": "Asha Rao"}',
             "u": owner.id},
        )  # fmt: skip
    return str(pid)


async def add_doc(client, actor, deal_id, kind, status="draft", file_id=None, expect=201, **kw):
    body = {"kind": kind, "status": status, "file_id": file_id, **kw}
    r = await client.post(f"/deals/{deal_id}/documents", json=body, headers=headers(actor))
    assert r.status_code == expect, r.text
    return r.json()


async def setup_catalog(client, world):
    r = await client.put(
        "/products",
        json={
            "products": [
                {
                    "name": "Voice Agent",
                    "unit": "per_minute",
                    "list_price_paise": 600,
                    "floor_price_paise": 450,
                }
            ],
            "reason": "catalog",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200, r.text
    return r.json()[0]


async def run_to(client, world, admin_engine, target: str):
    """Walk a fresh deal forward until it stands in `target` (criteria ticked with real proof).
    Returns (deal dict, owner)."""
    deal, owner = await new_deal(client, world)
    did = deal["id"]
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
    product = await setup_catalog(client, world)
    fit = (await client.get("/settings/fit-checklist", headers=headers(world.admin))).json()
    steps = {
        "lead": ["basic_info", "company_identified", "contact_verified", "fits_profile"],
        "qualification": ["need_documented", "meeting_scheduled"],
        "meeting_scheduled": ["meeting_completed", "opportunity_defined"],
        "prospecting": [
            "budget_confirmed",
            "decision_maker",
            "timeline_agreed",
            "pricing_selected",
        ],
        "proposal": [
            "walkthrough_logged",
            "feedback_recorded",
            "proposal_emailed",
            "decision_date_set",
        ],
        "revision": ["terms_accepted", "agreement_sent"],
        "closure": ["agreement_countersigned", "advance_received", "onboarding_info"],
        "onboarding": [
            "prerequisite_document",
            "technical_document",
            "kickoff_done",
            "agent_live",
            "first_invoice",
        ],
    }

    async def prove(stage: str) -> None:
        if stage == "lead":
            await log(client, owner, did, "call")
            await patch(client, owner, did, fit_checklist={f["id"]: True for f in fit})
        elif stage == "qualification":
            await patch(client, owner, did, pain_point="Missed calls after 6pm")
            await log(
                client,
                owner,
                did,
                "meeting",
                occurred_at=(datetime.now(UTC) + timedelta(days=3)).isoformat(),
                attendees=["Asha", "Ravi"],
            )
        elif stage == "meeting_scheduled":
            await log(
                client,
                owner,
                did,
                "meeting",
                occurred_at=ago(2),
                two_way=True,
                attendees=["Asha", "Ravi"],
                discussed="Discovery notes",
            )
            await patch(client, owner, did, use_case="Inbound order status calls")
        elif stage == "prospecting":
            await patch(client, owner, did, budget_amount_paise=5000000, budget_source="Annual ops budget",
                        expected_close_date=due(30), expected_close_reason="Quarter-end budget",
                        pricing_model="usage", plan="Growth", expected_minutes_per_month=10000)  # fmt: skip
            d = await detail(client, owner, did)
            cid = d["contacts"][0]["id"]
            r = await client.patch(
                f"/contacts/{cid}",
                json={
                    "is_decision_maker": True,
                    "phone": "+919800000001",
                },  # reachable: name, phone, email
                headers=headers(owner),
            )
            assert r.status_code == 200, r.text
            r = await client.post(
                f"/deals/{did}/lines",
                json={"product_id": product["id"], "quantity": 1000},
                headers=headers(owner),
            )
            assert r.status_code == 201, r.text
            await add_doc(client, owner, did, "proposal", "draft")
        elif stage == "proposal":
            # the loop: a walkthrough meeting was held, the customer accepted, the proposal was sent
            await log(client, owner, did, "meeting", occurred_at=ago(1), attendees=["Asha"],
                      discussed="Proposal walkthrough")  # fmt: skip
            r = await client.post(
                f"/deals/{did}/proposal-rounds",
                json={"feedback": "accepted"},
                headers=headers(owner),
            )
            assert r.status_code == 201, r.text
            await make_sent_proposal(admin_engine, world, did, owner)
            await patch(client, owner, did, decision_date=due(10))
        elif stage == "revision":
            await patch(
                client,
                owner,
                did,
                final_price_paise=550000,
                scope="Voice agent, 10k min",
                agreed_by="Asha Rao",
            )
            fid = await make_file(admin_engine, world, did, owner)
            await add_doc(client, owner, did, "agreement", "sent", fid)
        elif stage == "closure":
            fid = await make_file(admin_engine, world, did, owner)
            await add_doc(client, owner, did, "agreement", "countersigned", fid)
            await patch(client, owner, did, billing_contact="accounts@acme.in", subscription_start_date=due(15),
                        contract_term="12 months", billing_frequency="monthly", renewal_date=due(380),
                        advance_amount_paise=50000, advance_received_by="Neha Kapoor", advance_received_contact="neha@acme.in", advance_received_on=due(0))  # fmt: skip
            fid2 = await make_file(admin_engine, world, did, owner)
            await add_doc(client, owner, did, "advance", "received", fid2)
        elif stage == "onboarding":
            await log(client, owner, did, "kickoff", occurred_at=ago(1))
            await patch(
                client, owner, did, prereq_documents="Business requirements", prereq_received_on=due(0), technical_documents="API access and integration spec", technical_received_on=due(0), go_live_date=due(7), live_date=due(8), live_note="Agent is live"
            )  # fmt: skip
            for kind in ("prerequisite", "technical"):
                await add_doc(client, owner, did, kind, "received", await make_file(admin_engine, world, did, owner))
            await add_doc(client, owner, did, "invoice", "sent", number="INV-1")

    for stage in order[:-1]:
        if stage == target:
            return deal, owner
        await prove(stage)
        for key in steps[stage]:
            await tick(client, owner, did, key)
        await move(client, owner, did, order[order.index(stage) + 1])
    if target == "onboarding":
        return deal, owner
    await prove("onboarding")
    for key in steps["onboarding"]:
        await tick(client, owner, did, key)
    return deal, owner
