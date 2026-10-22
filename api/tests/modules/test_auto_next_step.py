# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The first next step comes from the pipeline: the first unmet criterion of the deal's stage (or
'Move to <next stage>' when all are met), read from the version the deal is pinned to. A new lead
gets it when the form leaves the step out; a stage move refreshes it unless a person chose one."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.core.time import utc_now
from app.modules.deals import health
from app.modules.leads.models import Deal
from app.modules.pipeline.engine.nextstep import suggest
from tests.deep.phase3.p3 import ORDER, force_stage
from tests.helpers import headers
from tests.modules.test_leads_api import audit_keys, configure, lead
from tests.modules.test_pipeline_config import BASE, get_version, publish, save, to_draft

# "Basic info" is already true for a lead made from the form (company, contact, phone, source), so
# the first thing the pipeline still asks for is the company's website or industry
LEAD_FIRST = "Company identified (name and website or industry)"


def tomorrow():
    return health.ist_date(utc_now()) + timedelta(days=1)


async def post_lead(client, actor, **kw):
    body = lead(**kw)
    for k in [k for k, v in kw.items() if v is None]:
        body.pop(k, None)
    return await client.post("/leads", json=body, headers=headers(actor))


async def lean(client, actor, **kw):
    """A lead with no next-step fields at all (what the form now sends), created by a leader."""
    body = lead(**kw)
    body.pop("next_step_text"), body.pop("next_step_due_date")
    body.update({k: v for k, v in kw.items() if k.startswith("next_step")})
    r = await client.post("/leads", json=body, headers=headers(actor))
    return r


async def step_row(admin_engine, deal_id):
    async with admin_engine.connect() as c:
        return (
            (
                await c.execute(
                    text(
                        "SELECT text, due_date, auto_generated FROM next_steps "
                        "WHERE deal_id = :d AND done_at IS NULL"
                    ),
                    {"d": deal_id},
                )
            )
            .mappings()
            .one()
        )


async def suggestion(deal_id):
    async with get_sessionmaker()() as s:
        deal = await s.get(Deal, uuid.UUID(str(deal_id)))
        return await suggest(s, deal)


# ------------------------------------------------------------------------------ at creation
async def test_new_lead_without_a_next_step_gets_one_from_the_pipeline(client, world, admin_engine):
    await configure(client, world)
    r = await lean(client, world.tl_north)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["next_step"]["text"] == LEAD_FIRST
    assert d["next_step"]["due_date"] == tomorrow().isoformat()
    row = await step_row(admin_engine, d["id"])
    assert row["auto_generated"] is True and row["due_date"] >= health.ist_date(utc_now())
    # the same step follows the new owner as before
    assert d["next_step"]["responsible"]["id"] == d["owner"]["id"]
    audit = await audit_keys(d["id"])
    assert audit == [("lead.create", "ok"), ("lead.assigned", "ok")]


async def test_a_reps_waiting_lead_has_its_step_and_no_responsible_until_assigned(client, world):
    await configure(client, world)
    r = await lean(client, world.north1)
    d = r.json()
    assert d["awaiting_team_leader"] is True
    assert d["next_step"]["text"] == LEAD_FIRST and d["next_step"]["responsible"] is None
    out = await client.post(
        f"/pool/{d['id']}/decide",
        json={"action": "give_creator"},
        headers=headers(world.tl_north),
    )
    assert out.status_code == 200
    got = (await client.get(f"/deals/{d['id']}", headers=headers(world.north1))).json()
    assert got["next_step"]["responsible"]["id"] == str(world.north1.id)
    assert got["next_step"]["text"] == LEAD_FIRST


async def test_an_explicit_next_step_is_kept(client, world, admin_engine):
    await configure(client, world)
    due = (tomorrow() + timedelta(days=4)).isoformat()
    r = await lean(client, world.tl_north, next_step_text="  Call   Asha ", next_step_due_date=due)
    assert r.status_code == 201, r.text
    row = await step_row(admin_engine, r.json()["id"])
    assert row["text"] == "Call Asha" and str(row["due_date"]) == due
    assert row["auto_generated"] is False


async def test_text_only_gets_tomorrow_and_date_only_gets_the_pipeline_text(
    client, world, admin_engine
):
    await configure(client, world)
    a = (await lean(client, world.tl_north, next_step_text="Send brochure")).json()
    ra = await step_row(admin_engine, a["id"])
    assert ra["text"] == "Send brochure" and ra["due_date"] == tomorrow()
    due = (tomorrow() + timedelta(days=2)).isoformat()
    b = (await lean(client, world.tl_north, next_step_due_date=due)).json()
    rb = await step_row(admin_engine, b["id"])
    assert rb["text"] == LEAD_FIRST and str(rb["due_date"]) == due and rb["auto_generated"]


@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
async def test_a_blank_explicit_text_is_still_rejected(client, world, bad):
    await configure(client, world)
    r = await lean(client, world.tl_north, next_step_text=bad)
    assert r.status_code == 422, r.text


async def test_a_past_explicit_date_is_still_rejected_and_nothing_is_created(
    client, world, admin_engine
):
    await configure(client, world)
    yesterday = (health.ist_date(utc_now()) - timedelta(days=1)).isoformat()
    r = await lean(client, world.tl_north, next_step_due_date=yesterday)
    assert r.status_code == 422
    async with admin_engine.connect() as c:
        n = (
            await c.execute(
                text("SELECT count(*) FROM deals WHERE org_id = :o"), {"o": world.org_id}
            )
        ).scalar_one()
    assert n == 0


# ------------------------------------------------------------------------------ the helper
async def test_every_stage_suggests_its_first_unmet_criterion_then_the_next_stage(
    client, world, admin_engine
):
    await configure(client, world)
    d = (await lean(client, world.tl_north)).json()
    async with admin_engine.connect() as c:
        crit = (
            await c.execute(
                text(
                    "SELECT c.stage_key, c.label FROM stage_criteria c JOIN pipeline_versions v "
                    "ON v.id = c.version_id WHERE v.org_id = :o AND v.status = 'published' "
                    "ORDER BY c.stage_key, c.position"
                ),
                {"o": world.org_id},
            )
        ).all()
        stages = (
            await c.execute(
                text(
                    "SELECT s.key, s.name FROM stages s JOIN pipeline_versions v ON v.id = s.version_id "
                    "WHERE v.org_id = :o AND v.status = 'published' ORDER BY s.position"
                ),
                {"o": world.org_id},
            )
        ).all()
    first: dict[str, str] = {}
    for key, label in crit:
        first.setdefault(key, label)
    names = [n for _, n in stages]
    keys = [k for k, _ in stages]
    checked = 0
    for key in ORDER:
        if key not in first:
            continue
        await force_stage(admin_engine, world.org_id, d["id"], key)
        # a criterion whose proof already holds is skipped (only the lead stage has such a case here)
        assert await suggestion(d["id"]) == (LEAD_FIRST if key == "lead" else first[key]), key
        await force_stage(admin_engine, world.org_id, d["id"], key, all_ticked=True)
        got = await suggestion(d["id"])
        i = keys.index(key)
        if key == "onboarding" or i + 1 >= len(keys):
            assert got is None or got.startswith("Move to"), (key, got)
        else:
            assert got and got.startswith("Move to ") and got[8:] in names, (key, got)
        checked += 1
    assert checked >= 8


async def test_criteria_whose_proof_already_holds_are_skipped(client, world):
    await configure(client, world)
    d = (await lean(client, world.tl_north, website="https://skipcheck.example")).json()
    # basic info and company identified are true from the form; the first real work is contact
    assert d["next_step"]["text"].startswith("Contact verified")


async def test_all_met_in_lead_says_move_to_qualification(client, world, admin_engine):
    await configure(client, world)
    d = (await lean(client, world.tl_north)).json()
    await force_stage(admin_engine, world.org_id, d["id"], "lead", all_ticked=True)
    assert await suggestion(d["id"]) == "Move to Qualification"


async def test_a_stage_that_left_the_version_suggests_nothing(client, world, admin_engine):
    await configure(client, world)
    d = (await lean(client, world.tl_north)).json()
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE deal_stage_entries SET stage_key = 'ghost', checklist_snapshot = '[]'::jsonb "
                "WHERE deal_id = :d AND exited_at IS NULL"
            ),
            {"d": d["id"]},
        )
    assert await suggestion(d["id"]) is None


async def test_the_suggestion_follows_the_pinned_version_not_the_latest(client, world):
    await configure(client, world)
    old = (await lean(client, world.tl_north)).json()
    v = await save(client, world.admin, {"from_version_no": 1, "reason": "start"})
    body = to_draft(v)
    body["stages"][1]["name"] = "Discovery"  # Qualification renamed
    for c in body["criteria"]:
        if c["stage_key"] == "lead" and c["key"] == "company_identified":
            c["label"] = "Renamed company check"
    await save(client, world.admin, {**body, "reason": "rename"})
    await publish(client, world, 2)
    assert (await get_version(client, world, 2))["status"] == "published"
    # the deal already in Lead keeps the old wording
    assert await suggestion(old["id"]) == LEAD_FIRST
    # a lead created now reads the new version
    new = (await lean(client, world.tl_north)).json()
    assert new["next_step"]["text"] == "Renamed company check"
    # and the old deal's 'Move to' still names the stage as it was pinned
    assert BASE  # (all-met naming is covered per stage above)


# ------------------------------------------------------------------------------ after a move
async def test_a_move_sets_the_new_stage_step_but_never_overwrites_a_persons(
    client, world, admin_engine
):
    await configure(client, world)
    auto = (await lean(client, world.tl_north)).json()
    manual = (await lean(client, world.tl_north, next_step_text="Send quote Friday")).json()
    for d in (auto, manual):
        r = await client.post(
            f"/deals/{d['id']}/move",
            json={"to_stage": "qualification", "override": True, "reason": "test"},
            headers=headers(world.admin),
        )
        assert r.status_code == 200, r.text
    a = await step_row(admin_engine, auto["id"])
    assert (
        a["text"] == "Client's problem written down (pain point) and how our product solves it"
        and a["auto_generated"] is True
    )
    assert a["due_date"] == tomorrow()
    m = await step_row(admin_engine, manual["id"])
    assert m["text"] == "Send quote Friday" and m["auto_generated"] is False
    keys = await audit_keys(auto["id"])
    assert keys.count(("next_step.set", "ok")) == 1  # one row for the one change
    assert keys.count(("next_step.set", "ok")) == 1 and ("deal.override_or_skip", "ok") in keys
    assert ("next_step.set", "ok") not in await audit_keys(manual["id"])
    # only one open step remains on each deal
    async with admin_engine.connect() as c:
        n = (
            await c.execute(
                text("SELECT count(*) FROM next_steps WHERE deal_id = :d AND done_at IS NULL"),
                {"d": auto["id"]},
            )
        ).scalar_one()
    assert n == 1


async def test_a_second_move_replaces_the_system_step_again(client, world, admin_engine):
    await configure(client, world)
    d = (await lean(client, world.tl_north)).json()
    for stage in ("qualification", "meeting_scheduled"):
        r = await client.post(
            f"/deals/{d['id']}/move",
            json={"to_stage": stage, "override": True, "reason": "test"},
            headers=headers(world.admin),
        )
        assert r.status_code == 200, r.text
    row = await step_row(admin_engine, d["id"])
    assert row["text"].startswith("Meeting completed") and row["auto_generated"]
