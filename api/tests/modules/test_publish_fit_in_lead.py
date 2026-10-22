# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""scripts/publish_fit_in_lead.py: rolls the fit check into Lead as a new published version,
leaves old versions and open deals alone, and is idempotent."""

from copy import deepcopy

from sqlalchemy import text

from scripts.publish_fit_in_lead import NEW_NEED_LABEL, OLD_NEED_LABEL, do_org, moved_definition
from tests.helpers import headers
from tests.modules.journey import new_deal
from tests.modules.test_pipeline_config import BASE, get_version, publish, save, to_draft


def keys(v: dict, stage: str) -> list[str]:
    return [c["key"] for c in v["criteria"] if c["stage_key"] == stage]


async def old_layout(client, world) -> dict:
    """Publish the pre-decision layout (fit check in Qualification) as the live version."""
    cur = await get_version(client, world, 1)
    body = to_draft(cur)
    for c in body["criteria"]:
        if c["key"] == "fits_profile":
            c["stage_key"] = "qualification"
        if c["key"] == "need_documented":
            c["label"] = OLD_NEED_LABEL
    body["criteria"].sort(key=lambda c: c["stage_key"] != "lead")  # lead first, rest keep order
    await save(client, world.admin, {**body, "reason": "old layout"})
    return await publish(client, world, 2)


async def test_seed_has_the_fit_check_in_lead_after_contact_verified(client, world):
    v = await get_version(client, world, 1)
    assert keys(v, "lead") == [
        "basic_info",
        "company_identified",
        "contact_verified",
        "fits_profile",
    ]
    assert keys(v, "qualification") == ["need_documented", "meeting_scheduled"]


async def test_script_moves_the_fit_check_once_and_pins_old_deals(client, world, admin_engine):
    live = await old_layout(client, world)
    assert "fits_profile" in keys(live, "qualification")
    deal, _ = await new_deal(client, world)  # entered Lead under version 2
    out = await do_org(world.org_id, "t", dry_run=True)
    assert out.startswith("WOULD publish")
    assert [
        v["version_no"]
        for v in (await client.get(f"{BASE}/versions", headers=headers(world.admin))).json()
    ] == [2, 1]
    out = await do_org(world.org_id, "t", dry_run=False)
    assert out.startswith("PUBLISHED version 3"), out
    v3 = await get_version(client, world, 3)
    assert v3["status"] == "published"
    assert keys(v3, "lead") == [
        "basic_info",
        "company_identified",
        "contact_verified",
        "fits_profile",
    ]
    assert keys(v3, "qualification") == ["need_documented", "meeting_scheduled"]
    need = next(c for c in v3["criteria"] if c["key"] == "need_documented")
    assert need["label"] == NEW_NEED_LABEL
    assert [s["key"] for s in v3["stages"]] == [s["key"] for s in live["stages"]]
    assert len(v3["criteria"]) == len(live["criteria"])
    # the frozen version 2 is untouched, and the existing deal stays pinned to it
    assert "fits_profile" in keys(await get_version(client, world, 2), "qualification")
    async with admin_engine.connect() as c:
        pinned = (
            await c.execute(
                text(
                    "SELECT v.version_no FROM deals d JOIN pipeline_versions v ON v.id = d.pipeline_version_id WHERE d.id = :d"
                ),
                {"d": deal["id"]},
            )
        ).scalar_one()
    assert pinned == 2
    # a lead created now uses version 3
    fresh, _ = await new_deal(client, world)
    async with admin_engine.connect() as c:
        no = (
            await c.execute(
                text(
                    "SELECT v.version_no FROM deals d JOIN pipeline_versions v ON v.id = d.pipeline_version_id WHERE d.id = :d"
                ),
                {"d": fresh["id"]},
            )
        ).scalar_one()
    assert no == 3
    # idempotent
    again = await do_org(world.org_id, "t", dry_run=False)
    assert again.startswith("NO-OP"), again
    versions = (await client.get(f"{BASE}/versions", headers=headers(world.admin))).json()
    assert [v["version_no"] for v in versions] == [3, 2, 1]


async def test_script_skips_an_org_with_an_open_draft(client, world):
    await old_layout(client, world)
    await save(client, world.admin, {"from_version_no": 2, "reason": "wip"})
    out = await do_org(world.org_id, "t", dry_run=False)
    assert out.startswith("SKIPPED: an open draft")


def test_moved_definition_is_a_noop_when_already_moved_and_keeps_a_custom_label():
    v = {
        "stages": [
            {
                "key": "lead",
                "name": "Lead",
                "probability_pct": 0,
                "is_entry": True,
                "is_final": False,
                "won_on_entry": False,
                "is_conditional": False,
                "display_group": None,
                "entry_checks": [],
            },
            {
                "key": "qualification",
                "name": "Q",
                "probability_pct": 10,
                "is_entry": False,
                "is_final": False,
                "won_on_entry": False,
                "is_conditional": False,
                "display_group": None,
                "entry_checks": [],
            },
        ],
        "transitions": [],
        "criteria": [
            {
                "stage_key": "lead",
                "key": "contact_verified",
                "label": "cv",
                "help_text": None,
                "proof_type": "activity",
                "proof_config": {},
            },
            {
                "stage_key": "qualification",
                "key": "need_documented",
                "label": "Custom label",
                "help_text": None,
                "proof_type": "fields",
                "proof_config": {},
            },
            {
                "stage_key": "qualification",
                "key": "fits_profile",
                "label": "fit",
                "help_text": None,
                "proof_type": "checklist",
                "proof_config": {"list": "fit"},
            },
        ],
    }
    d = moved_definition(deepcopy(v))
    assert d is not None
    assert [(c["stage_key"], c["key"]) for c in d["criteria"]] == [
        ("lead", "contact_verified"),
        ("lead", "fits_profile"),
        ("qualification", "need_documented"),
    ]
    assert d["criteria"][2]["label"] == "Custom label"
    for c in v["criteria"]:
        if c["key"] == "fits_profile":
            c["stage_key"] = "lead"
    assert moved_definition(v) is None
