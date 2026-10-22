# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pipeline configuration: draft, validate, impact, publish with stage mapping, rollback."""

from copy import deepcopy

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.platform.audit.models import AuditLog
from tests.helpers import headers
from tests.modules.journey import detail, new_deal, run_to
from tests.modules.test_leads_api import configure, create

BASE = "/pipelines/new_business"
STAGE_KEYS = (
    "key", "name", "probability_pct", "is_entry", "is_final", "won_on_entry", "is_conditional",
    "display_group", "entry_checks",
)  # fmt: skip


def to_draft(v: dict) -> dict:
    return {
        "stages": [{k: s[k] for k in STAGE_KEYS} for s in v["stages"]],
        "transitions": deepcopy(v["transitions"]),
        "criteria": [
            {
                k: c[k]
                for k in ("stage_key", "key", "label", "help_text", "proof_type", "proof_config")
            }
            for c in v["criteria"]
        ],
    }


async def get_version(client, world, no):
    r = await client.get(f"{BASE}/versions/{no}", headers=headers(world.admin))
    assert r.status_code == 200, r.text
    return r.json()


async def save(client, actor, body, expect=200):
    r = await client.post(f"{BASE}/versions", json=body, headers=headers(actor))
    assert r.status_code == expect, r.text
    return r.json()


async def publish(client, world, no, expect=200, **body):
    r = await client.post(
        f"{BASE}/versions/{no}/publish",
        json={"reason": "go", **body},
        headers=headers(world.admin),
    )
    assert r.status_code == expect, r.text
    return r.json()


async def test_only_admins_configure_and_every_save_has_a_reason(client, world):
    r = await client.get(f"{BASE}/versions", headers=headers(world.admin))
    assert r.status_code == 200 and [v["status"] for v in r.json()] == ["published"]
    for actor in (world.north1, world.tl_north):
        assert (await client.get(f"{BASE}/versions", headers=headers(actor))).status_code == 403
        r = await client.post(
            f"{BASE}/versions", json={"from_version_no": 1, "reason": "x"}, headers=headers(actor)
        )
        assert r.status_code == 403
    r = await client.post(
        f"{BASE}/versions", json={"from_version_no": 1}, headers=headers(world.admin)
    )
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    v = await save(client, world.admin, {"from_version_no": 1, "reason": "start"})
    assert v["status"] == "draft" and v["version_no"] == 2 and len(v["stages"]) == 9
    # only one draft at a time
    r = await client.post(
        f"{BASE}/versions",
        json={"from_version_no": 1, "reason": "again"},
        headers=headers(world.admin),
    )
    assert r.status_code == 409
    # super admin's reason is optional
    r = await client.post(f"{BASE}/versions", json={"notes": "n"}, headers=headers(world.sa))
    assert r.status_code == 200


async def test_validation_catches_a_broken_draft(client, world):
    v = await save(client, world.admin, {"from_version_no": 1, "reason": "start"})
    body = to_draft(v)
    body["stages"][1]["is_entry"] = True  # a second entry stage
    body["criteria"][0]["proof_type"] = "telepathy"
    body["transitions"] = [
        t
        for t in body["transitions"]
        if t["from_stage_key"] != "proposal" or t["kind"] != "forward"
    ]
    await save(client, world.admin, {**body, "reason": "oops"})
    r = await client.post(f"{BASE}/versions/2/validate", headers=headers(world.admin))
    codes = {p["code"] for p in r.json()["problems"]}
    assert r.json()["ok"] is False
    assert {"entry_stage", "bad_proof", "no_forward"} <= codes
    r = await client.post(
        f"{BASE}/versions/2/publish", json={"reason": "go"}, headers=headers(world.admin)
    )
    assert r.status_code == 422 and r.json()["details"]["problems"]
    # the live version is untouched
    assert [
        v["status"]
        for v in (await client.get(f"{BASE}/versions", headers=headers(world.admin))).json()
    ][-1] == "published"


async def test_publishing_changes_only_deals_that_enter_a_stage_later(client, world):
    deal, owner = await new_deal(client, world)
    before = await detail(client, owner, deal["id"])
    assert len(before["progress"]["criteria"]) == 4
    v = await save(client, world.admin, {"from_version_no": 1, "reason": "start"})
    body = to_draft(v)
    body["stages"][0]["name"] = "New lead"
    body["criteria"].append(
        {
            "stage_key": "lead", "key": "extra_check", "label": "Extra check", "help_text": None,
            "proof_type": "fields", "proof_config": {"require": ["use_case"]},
        }
    )  # fmt: skip
    await save(client, world.admin, {**body, "reason": "edit"})
    ok = (await client.post(f"{BASE}/versions/2/validate", headers=headers(world.admin))).json()
    assert ok["ok"], ok
    out = await publish(client, world, 2)
    assert out["status"] == "published" and out["diff_summary"]["criteria_added"] == [
        "lead.extra_check"
    ]
    versions = (await client.get(f"{BASE}/versions", headers=headers(world.admin))).json()
    assert [v["status"] for v in versions] == ["published", "archived"]
    # the deal already in Lead keeps its checklist
    after = await detail(client, owner, deal["id"])
    assert len(after["progress"]["criteria"]) == 4
    # a deal that enters Lead after publishing gets the new one
    fresh = await create(client, world.north1, industry="Retail")
    fresh_owner = world.north1 if fresh["owner"]["id"] == str(world.north1.id) else world.north2
    d2 = await detail(client, fresh_owner, fresh["id"])
    assert len(d2["progress"]["criteria"]) == 5
    # a published version is frozen in the database too
    async with get_sessionmaker()() as s:
        from sqlalchemy import text

        try:
            await s.execute(
                text(
                    "UPDATE stages SET name='x' WHERE version_id=(SELECT id FROM pipeline_versions WHERE version_no=2 AND org_id=:o)"
                ),
                {"o": world.org_id},
            )
            await s.commit()
            raised = False
        except Exception:  # noqa: BLE001
            raised = True
    assert raised


async def test_removing_a_stage_with_deals_needs_a_target_and_logs_each_move(
    client, world, admin_engine
):
    deal, owner = await run_to(client, world, admin_engine, "meeting_scheduled")
    did = deal["id"]
    v = await save(client, world.admin, {"from_version_no": 1, "reason": "start"})
    body = to_draft(v)
    body["stages"] = [s for s in body["stages"] if s["key"] != "meeting_scheduled"]
    body["criteria"] = [c for c in body["criteria"] if c["stage_key"] != "meeting_scheduled"]
    body["transitions"] = [
        t
        for t in body["transitions"]
        if "meeting_scheduled" not in (t["from_stage_key"], t["to_stage_key"])
    ] + [{"from_stage_key": "qualification", "to_stage_key": "prospecting", "kind": "forward"}]
    await save(client, world.admin, {**body, "reason": "drop a stage"})
    impact = (await client.get(f"{BASE}/versions/2/impact", headers=headers(world.admin))).json()
    assert impact["needs_mapping"] == ["meeting_scheduled"]
    assert next(s for s in impact["stages"] if s["key"] == "meeting_scheduled")["open_deals"] == 1
    r = await client.post(
        f"{BASE}/versions/2/publish", json={"reason": "go"}, headers=headers(world.admin)
    )
    assert r.status_code == 422 and r.json()["details"]["needs_mapping"] == ["meeting_scheduled"]
    await publish(client, world, 2, stage_mapping={"meeting_scheduled": "prospecting"})
    d = await detail(client, owner, did)
    assert d["stage_key"] == "prospecting"
    moved = [v for v in d["visits"] if v["stage_key"] == "meeting_scheduled"][-1]
    assert moved["exit_kind"] == "override" and moved["reason"] == "config move"
    async with get_sessionmaker()() as s:
        rows = list(
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == did, AuditLog.note == "config move"
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1 and rows[0].actor_id == world.admin.id
    # roll back: the stage returns, as a new version number
    r = await client.post(
        f"{BASE}/rollback/1", json={"reason": "undo"}, headers=headers(world.admin)
    )
    assert r.status_code == 200, r.text
    assert r.json()["version_no"] == 3 and len(r.json()["stages"]) == 9
    versions = [
        v["status"]
        for v in (await client.get(f"{BASE}/versions", headers=headers(world.admin))).json()
    ]
    assert versions == ["published", "archived", "archived"]


async def test_settings_lists(client, world):
    await configure(client, world)
    lost = (await client.get("/settings/lost-reasons", headers=headers(world.admin))).json()
    assert len(lost) == 9 and lost[0]["label"] == "Price"
    r = await client.put(
        "/settings/lost-reasons",
        json={
            "items": [
                {"id": lost[0]["id"], "label": "Price too high"},
                {"label": "Went with in-house"},
            ],
            "reason": "tidy",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200
    active = [x["label"] for x in r.json() if x["active"]]
    assert active == ["Price too high", "Went with in-house"]
    assert (
        await client.put(
            "/settings/lost-reasons",
            json={"items": [], "reason": "x"},
            headers=headers(world.tl_north),
        )
    ).status_code == 403
    r = await client.put(
        "/settings/custom-fields",
        json={
            "fields": [{"entity": "deal", "key": "gstin", "label": "GSTIN", "type": "text"}],
            "reason": "add",
        },
        headers=headers(world.admin),
    )
    assert r.status_code == 200 and r.json()[0]["key"] == "gstin"
    fit = (await client.get("/settings/fit-checklist", headers=headers(world.admin))).json()
    assert len(fit) == 4
