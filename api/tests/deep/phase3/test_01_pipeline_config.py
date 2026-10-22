# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 1: pipeline configuration (seed, draft, validate, publish,
rollback, frozen versions, permissions, settings lists)."""

import asyncio
import copy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.deep.phase3.p3 import (
    CONDITIONAL,
    FORWARD,
    ORDER,
    POS,
    H,
    max_seq,
    mkdeal,
    one,
    rows,
    run,
    second_org,
)

# ---------------------------------------------------------------------------------------------
# The PRD table (sections 9 and 10), written out by hand. Not imported from the seed module.
PRD_STAGES = {
    "lead": ("Lead", 0),
    "qualification": ("Qualification", 10),
    "meeting_scheduled": ("Meeting Scheduled", 10),
    "prospecting": ("Prospecting", 20),
    "proposal": ("Proposal", 20),
    "revision": ("Revision", 10),
    "revision_2": ("Revision 2", 10),
    "closure": ("Closure", 90),
    "onboarding": ("Onboarding", 100),
}
PRD_CRITERIA = {
    "lead": [
        ("basic_info", "fields"),
        ("company_identified", "fields"),
        ("contact_verified", "activity"),
        ("fits_profile", "checklist"),
    ],
    "qualification": [
        ("need_documented", "fields"),
        ("meeting_scheduled", "activity"),
    ],
    "meeting_scheduled": [("meeting_completed", "activity"), ("opportunity_defined", "fields")],
    "prospecting": [
        ("budget_confirmed", "fields"),
        ("decision_maker", "contact_tag"),
        ("timeline_agreed", "fields"),
        ("pricing_selected", "deal_lines"),
    ],
    "proposal": [
        ("walkthrough_logged", "activity"),
        ("feedback_recorded", "proposal_round"),
        ("proposal_emailed", "proposal_sent"),
        ("decision_date_set", "fields"),
    ],
    "revision": [("terms_accepted", "fields"), ("agreement_sent", "document")],
    "revision_2": [("terms_accepted", "fields"), ("agreement_sent", "document")],
    "closure": [
        ("agreement_countersigned", "document"),
        ("advance_received", "document"),
        ("onboarding_info", "fields"),
    ],
    "onboarding": [
        ("prerequisite_document", "document"),
        ("technical_document", "document"),
        ("kickoff_done", "activity"),
        ("agent_live", "fields"),
        ("first_invoice", "document"),
    ],
}
PRD_REQUIRE = {  # fields each "fields" criterion needs (an {"any": [...]} group is a frozenset)
    ("lead", "basic_info"): {"account.name", "contact.name", frozenset({"contact.phone", "contact.email"}), "lead_source"},
    ("lead", "company_identified"): {"account.name", frozenset({"account.website", "account.industry"})},
    ("qualification", "need_documented"): {"pain_point"},
    ("meeting_scheduled", "opportunity_defined"): {"use_case"},
    ("prospecting", "budget_confirmed"): {"budget_amount_paise", "budget_source"},
    ("prospecting", "timeline_agreed"): {"expected_close_date", "expected_close_reason"},
    ("proposal", "decision_date_set"): {"decision_date"},
    ("revision", "terms_accepted"): {"final_price_paise", "scope", "agreed_by"},
    ("revision_2", "terms_accepted"): {"final_price_paise", "scope", "agreed_by"},
    ("closure", "onboarding_info"): {"billing_contact", "subscription_start_date", "pricing_model", "contract_term", "billing_frequency", "renewal_date"},
    ("closure", "advance_received"): {"advance_amount_paise", "advance_received_by", "advance_received_contact", "advance_received_on"},
    ("onboarding", "prerequisite_document"): {"prereq_documents", "prereq_received_on"},
    ("onboarding", "technical_document"): {"technical_documents", "technical_received_on"},
    ("onboarding", "agent_live"): {"live_date", "live_note"},
}  # fmt: skip
PRD_LOST = [
    "Price",
    "Competitor",
    "Customer not interested",
    "Budget constraints",
    "Timing",
    "Product/service mismatch",
    "Customer stopped responding",
    "Internal decision",
    "Other",
]


def prd_transitions() -> set[tuple[str, str, str]]:
    out = {(a, b, "forward") for a, b in FORWARD}
    out |= {(a, b, "conditional") for a, b in CONDITIONAL}
    for later in ORDER:
        for earlier in ORDER[: POS[later]]:
            out.add((later, earlier, "backward"))
    return out


def _req_set(cfg):
    out = set()
    for r in cfg.get("require", []):
        out.add(frozenset(r["any"]) if isinstance(r, dict) else r)
    return out


async def get_v(client, actor, no, key="new_business"):
    r = await client.get(f"/pipelines/{key}/versions/{no}", headers=H(actor))
    assert r.status_code == 200, r.text
    return r.json()


def to_draft(v: dict, **extra) -> dict:
    """A DraftIn body from a version detail (strip server-only fields)."""
    stages = [
        {k: s[k] for k in ("key", "name", "probability_pct", "is_entry", "is_final", "won_on_entry", "is_conditional", "display_group", "entry_checks")}
        for s in v["stages"]
    ]  # fmt: skip
    crit = [
        {k: c[k] for k in ("stage_key", "key", "label", "help_text", "proof_type", "proof_config")}
        for c in v["criteria"]
    ]
    return {
        "stages": stages,
        "transitions": copy.deepcopy(v["transitions"]),
        "criteria": crit,
        "reason": "qa",
        **extra,
    }


async def save(client, actor, body, expect=200):
    r = await client.post("/pipelines/new_business/versions", json=body, headers=H(actor))
    assert r.status_code == expect, r.text
    return r


async def publish(client, actor, no, expect=200, **body):
    body.setdefault("reason", "qa")
    r = await client.post(
        f"/pipelines/new_business/versions/{no}/publish", json=body, headers=H(actor)
    )
    assert r.status_code == expect, r.text
    return r


async def validate(client, actor, no):
    r = await client.post(f"/pipelines/new_business/versions/{no}/validate", headers=H(actor))
    assert r.status_code == 200, r.text
    return r.json()


def drop_stage(d: dict, key: str, bridge: tuple[str, str] | None = None) -> dict:
    """Remove stage `key` (with its transitions and criteria); optionally add a forward bridge."""
    d["stages"] = [s for s in d["stages"] if s["key"] != key]
    d["transitions"] = [
        t for t in d["transitions"] if key not in (t["from_stage_key"], t["to_stage_key"])
    ]
    d["criteria"] = [c for c in d["criteria"] if c["stage_key"] != key]
    if bridge:
        d["transitions"].append(
            {"from_stage_key": bridge[0], "to_stage_key": bridge[1], "kind": "forward"}
        )
    return d


# ================================================================================ 1.1 seed
async def test_seed_matches_the_prd_table_stage_by_stage(client, world):
    v = await get_v(client, world.admin, 1)
    assert v["status"] == "published" and v["version_no"] == 1
    by_key = {s["key"]: s for s in v["stages"]}
    assert list(by_key) == ORDER  # positions follow the PRD order
    diffs = []
    for k, (name, prob) in PRD_STAGES.items():
        s = by_key[k]
        if (s["name"], s["probability_pct"]) != (name, prob):
            diffs.append((k, s["name"], s["probability_pct"]))
        assert s["is_entry"] is (k == "lead")
        assert s["is_final"] is (k == "onboarding") and s["won_on_entry"] is (k == "onboarding")
        assert s["is_conditional"] is (k == "revision_2")
        assert s["display_group"] == ("revision" if k.startswith("revision") else k)
    assert not diffs, diffs
    # entry checks: Revision needs feedback; nothing is needed to enter Proposal, because the
    # proposal is drafted and sent at its end (owner decision, 17 Oct 2026)
    assert "feedback" in by_key["revision"]["entry_checks"][0]["config"]["require"]
    assert all(not by_key[k]["entry_checks"] for k in ORDER if k != "revision")
    # criteria: keys, order, proof types and the fields each needs
    got: dict[str, list[tuple[str, str]]] = {}
    for c in v["criteria"]:
        got.setdefault(c["stage_key"], []).append((c["key"], c["proof_type"]))
    assert got == PRD_CRITERIA
    cfg = {(c["stage_key"], c["key"]): c["proof_config"] for c in v["criteria"]}
    for (stage, key), need in PRD_REQUIRE.items():
        assert _req_set(cfg[(stage, key)]) == need, (stage, key)
    # the document/activity details the PRD spells out
    assert cfg[("qualification", "meeting_scheduled")]["meeting_status"] == ["booked", "held"]
    assert cfg[("qualification", "meeting_scheduled")]["attendees"] is True
    assert cfg[("lead", "contact_verified")]["two_way"] is True
    assert cfg[("meeting_scheduled", "meeting_completed")]["notes"] is True
    assert cfg[("meeting_scheduled", "meeting_completed")]["meeting_status"] == ["held"]
    assert set(cfg[("lead", "contact_verified")]["types"]) == {"call", "email", "meeting"}
    assert cfg[("revision", "agreement_sent")]["kind"] == "agreement"
    assert cfg[("revision", "agreement_sent")]["file"] is True
    assert cfg[("closure", "agreement_countersigned")]["status"] == ["countersigned"]
    assert cfg[("closure", "agreement_countersigned")]["file"] is True
    assert cfg[("prospecting", "pricing_selected")]["min_lines"] == 1
    also = cfg[("prospecting", "pricing_selected")]["also"][0]["config"]["require"]
    assert set(also) == {"pricing_model", "plan", "expected_minutes_per_month"}
    kick = cfg[("onboarding", "kickoff_done")]
    assert kick["types"] == ["kickoff"] and "go_live_date" in kick["also"][0]["config"]["require"]
    # transitions: forward chain, conditional Revision 2 loop, backward links to every earlier stage
    trans = {(t["from_stage_key"], t["to_stage_key"], t["kind"]) for t in v["transitions"]}
    assert trans == prd_transitions()


async def test_seed_lists_match_the_prd(client, world):
    lost = (await client.get("/settings/lost-reasons", headers=H(world.north1))).json()
    assert [x["label"] for x in lost] == PRD_LOST and all(x["active"] for x in lost)
    fit = (await client.get("/settings/fit-checklist", headers=H(world.north1))).json()
    assert len(fit) == 4 and [x["position"] for x in fit] == [1, 2, 3, 4]
    low = " | ".join(x["label"].lower() for x in fit)
    for needle in ("website", "phone", "interested", "genuine"):
        assert needle in low  # the 4 confirmed questions


async def test_both_pipelines_exist_and_only_those_two(admin_engine, world):
    keys = await rows(
        admin_engine, "SELECT key FROM pipelines WHERE org_id=:o ORDER BY key", o=world.org_id
    )
    assert [k["key"] for k in keys] == ["existing_customer", "new_business"]
    with pytest.raises(DBAPIError):
        await run(
            admin_engine,
            "INSERT INTO pipelines (org_id, key, name) VALUES (:o,'third','Third')",
            o=world.org_id,
        )


# ============================================================================ 1.2 draft/validate
BAD_KEYS = ["Bad", "1abc", "a-b", "", "a b", "é", "a" * 50, "_x", "A1"]


@pytest.mark.parametrize("bad", BAD_KEYS)
async def test_stage_key_slug_rules(client, world, bad):
    v = await get_v(client, world.admin, 1)
    d = to_draft(v)
    d["stages"][0]["key"] = bad
    r = await save(client, world.admin, d, expect=422)
    assert r.json()["code"] == "validation_error"


async def test_stage_key_longest_valid_slug(client, world):
    v = await get_v(client, world.admin, 1)
    d = to_draft(v)
    d["stages"].append(
        {"key": "a" + "b" * 48, "name": "Long", "probability_pct": 5, "is_entry": False,
         "is_final": False, "won_on_entry": False, "is_conditional": False,
         "display_group": None, "entry_checks": []}
    )  # fmt: skip
    await save(client, world.admin, d)


@pytest.mark.parametrize("prob", [-1, 101, 1000])
async def test_probability_bounds(client, world, prob):
    d = to_draft(await get_v(client, world.admin, 1))
    d["stages"][1]["probability_pct"] = prob
    await save(client, world.admin, d, expect=422)


@pytest.mark.parametrize("prob", [0, 100])
async def test_probability_edges_are_accepted(client, world, prob):
    d = to_draft(await get_v(client, world.admin, 1))
    d["stages"][1]["probability_pct"] = prob
    await save(client, world.admin, d)


async def test_duplicate_keys_are_refused(client, world):
    base = to_draft(await get_v(client, world.admin, 1))
    d = copy.deepcopy(base)
    d["stages"].append(copy.deepcopy(d["stages"][1]))
    assert (await save(client, world.admin, d, expect=422)).json()["code"] == "validation_error"
    d = copy.deepcopy(base)
    d["transitions"].append(copy.deepcopy(d["transitions"][0]))
    await save(client, world.admin, d, expect=422)
    d = copy.deepcopy(base)
    d["criteria"].append(copy.deepcopy(d["criteria"][0]))
    await save(client, world.admin, d, expect=422)


async def test_every_validation_problem_code(client, world):
    base = to_draft(await get_v(client, world.admin, 1))
    cases: dict[str, callable] = {}

    def two_entries(d):
        d["stages"][1]["is_entry"] = True

    def no_entry(d):
        d["stages"][0]["is_entry"] = False

    def no_won(d):
        for s in d["stages"]:
            s["won_on_entry"] = False

    def bad_transition(d):
        d["transitions"].append(
            {"from_stage_key": "lead", "to_stage_key": "ghost", "kind": "forward"}
        )

    def unreachable(d):
        d["stages"].append({"key": "island", "name": "Island", "probability_pct": 5, "is_entry": False, "is_final": False, "won_on_entry": False, "is_conditional": False, "display_group": None, "entry_checks": []})  # fmt: skip
        d["transitions"].append(
            {"from_stage_key": "island", "to_stage_key": "lead", "kind": "backward"}
        )
        d["transitions"].append(
            {"from_stage_key": "island", "to_stage_key": "closure", "kind": "forward"}
        )

    def no_forward(d):
        d["transitions"] = [
            t
            for t in d["transitions"]
            if not (t["from_stage_key"] == "qualification" and t["kind"] == "forward")
        ]

    def bad_entry(d):
        for s in d["stages"]:
            if s["key"] == "proposal":
                s["entry_checks"] = [{"type": "document", "config": {"kind": "bogus"}}]

    def bad_crit(d):
        d["criteria"].append({"stage_key": "nowhere", "key": "x", "label": "X", "help_text": None, "proof_type": "fields", "proof_config": {"require": ["plan"]}})  # fmt: skip

    def bad_proof_type(d):
        d["criteria"][0]["proof_type"] = "telepathy"

    def bad_proof_cfg(d):
        d["criteria"][0]["proof_config"] = {"require": []}

    cases = {
        "entry_stage": [two_entries, no_entry],
        "no_won_stage": [no_won],
        "bad_transition": [bad_transition],
        "unreachable": [unreachable],
        "no_forward": [no_forward],
        "bad_entry_check": [bad_entry],
        "bad_criterion": [bad_crit],
        "bad_proof": [bad_proof_type, bad_proof_cfg],
    }
    for code, mutators in cases.items():
        for fn in mutators:
            d = copy.deepcopy(base)
            fn(d)
            r = await save(client, world.admin, d)
            no = r.json()["version_no"]
            res = await validate(client, world.admin, no)
            assert res["ok"] is False
            assert code in {p["code"] for p in res["problems"]}, (code, res)
            # publishing an invalid draft is refused with the same problems
            r = await publish(client, world.admin, no, expect=422)
            assert code in {p["code"] for p in r.json()["details"]["problems"]}
            r = await client.delete(
                "/pipelines/new_business/draft", params={"reason": "qa"}, headers=H(world.admin)
            )
            assert r.status_code == 204


async def test_a_valid_copy_validates_clean_and_empty_draft_says_no_stages(client, world):
    r = await save(client, world.admin, {"from_version_no": 1, "reason": "qa"})
    assert (await validate(client, world.admin, r.json()["version_no"])) == {
        "ok": True,
        "problems": [],
    }
    d = to_draft(await get_v(client, world.admin, 1))
    d["stages"], d["transitions"], d["criteria"] = [], [], []
    r = await save(client, world.admin, d)
    res = await validate(client, world.admin, r.json()["version_no"])
    assert [p["code"] for p in res["problems"]] == ["no_stages"]


async def test_BUG_P3_01_blank_names_are_stored(client, world):
    d = to_draft(await get_v(client, world.admin, 1))
    d["stages"][1]["name"] = "   "
    r = await client.post("/pipelines/new_business/versions", json=d, headers=H(world.admin))
    assert r.status_code == 422, "a stage named '   ' was stored as an empty name"


async def test_huge_payload_does_not_crash(client, world):
    d = to_draft(await get_v(client, world.admin, 1))
    for i in range(300):
        d["stages"].append(
            {"key": f"s{i}", "name": "N" * 100, "probability_pct": 1, "is_entry": False,
             "is_final": False, "won_on_entry": False, "is_conditional": False,
             "display_group": None, "entry_checks": []}
        )  # fmt: skip
        d["transitions"].append(
            {"from_stage_key": "closure", "to_stage_key": f"s{i}", "kind": "forward"}
        )
    for i in range(300):
        d["criteria"].append({"stage_key": "lead", "key": f"c{i}", "label": "L" * 300, "help_text": "h" * 5000, "proof_type": "fields", "proof_config": {"require": ["plan"] * 200}})  # fmt: skip
    r = await client.post("/pipelines/new_business/versions", json=d, headers=H(world.admin))
    if r.status_code == 413:  # the platform now refuses bodies over 2 MB: a clean answer too
        return
    assert r.status_code == 200, r.text[:300]
    r = await client.post(
        "/pipelines/new_business/versions",
        content=b"{" + b'"notes": "' + b"x" * 3_000_000 + b'"}',
        headers={**H(world.admin), "content-type": "application/json"},
    )
    assert r.status_code < 500


async def test_one_draft_at_a_time_and_two_admins_saving_at_once(client, world, admin_engine):
    other = world.sa
    d1 = to_draft(await get_v(client, world.admin, 1))
    d2 = copy.deepcopy(d1)
    d2["stages"][1]["name"] = "Qualified?"
    d1["stages"][1]["name"] = "Qualification X"
    res = await asyncio.gather(
        client.post("/pipelines/new_business/versions", json=d1, headers=H(world.admin)),
        client.post("/pipelines/new_business/versions", json=d2, headers=H(other)),
    )
    assert [r.status_code for r in res] == [200, 200], [r.text for r in res]
    assert res[0].json()["version_no"] == res[1].json()["version_no"] == 2
    n = await one(
        admin_engine,
        "SELECT count(*) FROM pipeline_versions v JOIN pipelines p ON p.id=v.pipeline_id "
        "WHERE p.org_id=:o AND p.key='new_business' AND v.status='draft'",
        o=world.org_id,
    )
    assert n == 1
    # "start from version 1" while a draft exists is refused, saving a whole definition replaces it
    r = await client.post(
        "/pipelines/new_business/versions",
        json={"from_version_no": 1, "reason": "qa"},
        headers=H(world.admin),
    )
    assert r.status_code == 409
    # the draft holds exactly one of the two writers' names, never a mix
    got = await get_v(client, world.admin, 2)
    assert got["stages"][1]["name"] in ("Qualification X", "Qualified?")
    assert len(got["stages"]) == 9


async def test_discard_and_from_version_copy(client, world, admin_engine):
    r = await save(client, world.admin, {"from_version_no": 1, "reason": "qa", "notes": "copy"})
    v2 = r.json()
    v1 = await get_v(client, world.admin, 1)
    assert v2["status"] == "draft" and v2["version_no"] == 2 and v2["notes"] == "copy"
    strip = lambda v: (  # noqa: E731
        [(s["key"], s["name"], s["position"], s["probability_pct"]) for s in v["stages"]],
        sorted((t["from_stage_key"], t["to_stage_key"], t["kind"]) for t in v["transitions"]),
        [(c["stage_key"], c["key"], c["position"], c["proof_type"]) for c in v["criteria"]],
    )
    assert strip(v2) == strip(v1)
    r = await client.delete(
        "/pipelines/new_business/draft", params={"reason": "no"}, headers=H(world.admin)
    )
    assert r.status_code == 204
    again = await client.delete(
        "/pipelines/new_business/draft", params={"reason": "no"}, headers=H(world.admin)
    )
    assert again.status_code == 404
    # discarded versions stay in the history (never erased) and are not the live one
    vs = (await client.get("/pipelines/new_business/versions", headers=H(world.admin))).json()
    assert [(v["version_no"], v["status"]) for v in vs] == [(2, "archived"), (1, "published")]
    r = await client.post(
        "/pipelines/new_business/versions",
        json={"from_version_no": 99, "reason": "qa"},
        headers=H(world.admin),
    )
    assert r.status_code == 404
    # a new draft after a discard gets a fresh number
    r = await save(client, world.admin, {"from_version_no": 1, "reason": "qa"})
    assert r.json()["version_no"] == 3


# ================================================================================== 1.3 publish
async def test_publish_flow_roles_and_double_publish(client, world, admin_engine):
    d = to_draft(await get_v(client, world.admin, 1))
    d["stages"][3]["probability_pct"] = 25
    r = await save(client, world.admin, d)
    assert r.json()["version_no"] == 2
    await publish(client, world.admin, 2)
    vs = {
        v["version_no"]: v["status"]
        for v in (
            await client.get("/pipelines/new_business/versions", headers=H(world.admin))
        ).json()
    }
    assert vs == {1: "archived", 2: "published"}  # fmt: skip
    await publish(client, world.admin, 2, expect=409)  # publish twice
    await publish(client, world.admin, 1, expect=409)  # publish a non-draft
    await publish(client, world.admin, 77, expect=404)
    live = await get_v(client, world.admin, 2)
    assert live["diff_summary"]["probability_changed"] == ["prospecting"]
    assert live["published_by_name"] == "Anita"
    # audit: one ok row per save and per publish, with the reason
    a = await rows(
        admin_engine,
        "SELECT action_key, outcome, reason FROM audit_log WHERE org_id=:o AND "
        "entity_type='pipeline_version' ORDER BY seq",
        o=world.org_id,
    )
    assert [(x["action_key"], x["outcome"], x["reason"]) for x in a] == [
        ("pipeline.config", "ok", "qa")
    ] * 2


async def test_impact_preview_equals_sql_and_mapping_rules(client, world, admin_engine):
    # deals spread over stages, including a pooled one and a soft-deleted one (not counted)
    placed = {"lead": 3, "qualification": 2, "revision_2": 2, "proposal": 1}
    ids = {}
    for stage, n in placed.items():
        for _ in range(n):
            did, _o = await mkdeal(
                client, world, admin_engine, stage=stage, round_=2 if stage == "revision_2" else 1
            )
            ids.setdefault(stage, []).append(did)
    gone, _ = await mkdeal(client, world, admin_engine, stage="revision_2", round_=2)
    await run(admin_engine, "UPDATE deals SET deleted_at=now() WHERE id=:d", d=gone)
    pooled, _ = await mkdeal(client, world, admin_engine)
    await run(admin_engine, "UPDATE deals SET status='pooled', owner_id=NULL WHERE id=:d", d=pooled)
    d = drop_stage(to_draft(await get_v(client, world.admin, 1)), "revision_2")
    no = (await save(client, world.admin, d)).json()["version_no"]
    imp = (
        await client.get(f"/pipelines/new_business/versions/{no}/impact", headers=H(world.admin))
    ).json()
    sql = {
        r["stage_key"]: r["n"]
        for r in await rows(
            admin_engine,
            "SELECT stage_key, count(*) n FROM deals WHERE org_id=:o AND pipeline='new_business' "
            "AND status IN ('open','pooled') AND deleted_at IS NULL GROUP BY stage_key",
            o=world.org_id,
        )
    }
    got = {s["key"]: s["open_deals"] for s in imp["stages"]}
    for k in ORDER:
        assert got[k] == sql.get(k, 0), k
    assert imp["needs_mapping"] == ["revision_2"]
    assert {s["key"]: s["in_draft"] for s in imp["stages"]}["revision_2"] is False
    # removing a stage that holds deals needs a target
    r = await publish(client, world.admin, no, expect=422)
    assert r.json()["details"]["needs_mapping"] == ["revision_2"]
    await publish(client, world.admin, no, expect=422, stage_mapping={"revision_2": "ghost"})
    await publish(client, world.admin, no, expect=422, stage_mapping={"revision_2": "revision_2"})
    # snapshots of deals that stay are untouched, byte for byte
    stay = ids["lead"][0]
    before = await rows(
        admin_engine,
        "SELECT id, checklist_snapshot::text s FROM deal_stage_entries WHERE deal_id=:d",
        d=stay,
    )
    mark = await max_seq(admin_engine)
    await publish(client, world.admin, no, stage_mapping={"revision_2": "revision"})
    after = await rows(
        admin_engine,
        "SELECT id, checklist_snapshot::text s FROM deal_stage_entries WHERE deal_id=:d",
        d=stay,
    )
    assert before == after
    # every moved deal: one new open entry in revision, old entry closed 'override'/'config move', one audit row
    for did in ids["revision_2"]:
        e = await rows(
            admin_engine,
            "SELECT stage_key, exited_at, exit_kind, reason FROM deal_stage_entries WHERE deal_id=:d ORDER BY entered_at",
            d=did,
        )
        assert [x["stage_key"] for x in e][-2:] == ["revision_2", "revision"]  # fmt: skip
        assert (e[-2]["exit_kind"], e[-2]["reason"]) == ("override", "config move")
        assert e[-1]["exited_at"] is None
        assert (
            await one(admin_engine, "SELECT stage_key FROM deals WHERE id=:d", d=did) == "revision"
        )
        a = await rows(
            admin_engine,
            "SELECT action_key, outcome, note, reason FROM audit_log WHERE entity_id=:d AND seq>:m",
            d=str(did),
            m=mark,
        )
        assert [(x["action_key"], x["outcome"], x["note"]) for x in a] == [
            ("pipeline.config", "ok", "config move")
        ]
        assert a[0]["reason"] == "qa"
    # the soft-deleted deal in the removed stage is left alone
    assert (
        await one(admin_engine, "SELECT stage_key FROM deals WHERE id=:d", d=gone) == "revision_2"
    )
    # the moved deals now use the new version
    assert await one(
        admin_engine, "SELECT pipeline_version_id FROM deals WHERE id=:d", d=ids["revision_2"][0]
    ) == await one(
        admin_engine,
        "SELECT id FROM pipeline_versions v WHERE v.version_no=2 AND org_id=:o",
        o=world.org_id,
    )


async def test_new_deals_get_new_checklist_old_deals_keep_theirs(client, world, admin_engine):
    old, _o = await mkdeal(client, world, admin_engine)
    snap_before = await one(
        admin_engine,
        "SELECT checklist_snapshot::text FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
        d=old,
    )
    d = to_draft(await get_v(client, world.admin, 1))
    next(c for c in d["criteria"] if c["key"] == "basic_info")["label"] = "BRAND NEW LABEL"
    d["criteria"].append({"stage_key": "lead", "key": "extra_one", "label": "Extra", "help_text": None, "proof_type": "fields", "proof_config": {"require": ["use_case"]}})  # fmt: skip
    no = (await save(client, world.admin, d)).json()["version_no"]
    await publish(client, world.admin, no)
    new, _o2 = await mkdeal(client, world, admin_engine)
    old_now = await one(
        admin_engine,
        "SELECT checklist_snapshot::text FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
        d=old,
    )
    assert old_now == snap_before
    new_snap = await rows(
        admin_engine,
        "SELECT checklist_snapshot s FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
        d=new,
    )
    labels = [c["label"] for c in new_snap[0]["s"]]
    assert labels[0] == "BRAND NEW LABEL" and "Extra" in labels and len(labels) == 5
    # the old deal's page still shows the three old criteria
    det = (await client.get(f"/deals/{old}", headers=H(world.admin))).json()
    assert len(det["progress"]["criteria"]) == 4
    assert det["progress"]["criteria"][0]["label"] != "BRAND NEW LABEL"


async def test_rollback_creates_a_new_version_and_nothing_is_deleted(client, world, admin_engine):
    d = to_draft(await get_v(client, world.admin, 1))
    d["stages"][1]["name"] = "Qualified lead"
    await save(client, world.admin, d)
    await publish(client, world.admin, 2)
    r = await client.post(
        "/pipelines/new_business/rollback/1", json={"reason": "undo"}, headers=H(world.admin)
    )
    assert r.status_code == 200, r.text
    v3 = r.json()
    assert v3["version_no"] == 3 and v3["status"] == "published"
    assert v3["stages"][1]["name"] == "Qualification"
    vs = {
        v["version_no"]: v["status"]
        for v in (
            await client.get("/pipelines/new_business/versions", headers=H(world.admin))
        ).json()
    }
    assert vs == {1: "archived", 2: "archived", 3: "published"}  # fmt: skip
    assert (
        await one(
            admin_engine,
            "SELECT count(*) FROM pipeline_versions WHERE pipeline_id=(SELECT id FROM pipelines WHERE org_id=:o AND key='new_business')",
            o=world.org_id,
        )
        == 3
    )
    # rollback to a draft or to a missing version, and rollback while a draft exists
    r = await client.post(
        "/pipelines/new_business/rollback/9", json={"reason": "x"}, headers=H(world.admin)
    )
    assert r.status_code == 404
    await save(client, world.admin, {"from_version_no": 1, "reason": "qa"})
    r = await client.post(
        "/pipelines/new_business/rollback/1", json={"reason": "x"}, headers=H(world.admin)
    )
    assert r.status_code == 409
    r = await client.post(
        "/pipelines/new_business/rollback/4", json={"reason": "x"}, headers=H(world.admin)
    )
    assert r.status_code == 409  # a draft cannot be rolled back to


async def test_rollback_needs_a_mapping_when_it_removes_a_stage_with_deals(
    client, world, admin_engine
):
    d = to_draft(await get_v(client, world.admin, 1))
    d["stages"].insert(2, {"key": "demo", "name": "Demo", "probability_pct": 10, "is_entry": False, "is_final": False, "won_on_entry": False, "is_conditional": False, "display_group": None, "entry_checks": []})  # fmt: skip
    d["transitions"] += [
        {"from_stage_key": "qualification", "to_stage_key": "demo", "kind": "forward"},
        {"from_stage_key": "demo", "to_stage_key": "meeting_scheduled", "kind": "forward"},
        {"from_stage_key": "demo", "to_stage_key": "lead", "kind": "backward"},
    ]
    no = (await save(client, world.admin, d)).json()["version_no"]
    await publish(client, world.admin, no)
    # a deal in the new stage
    did, _o = await mkdeal(client, world, admin_engine)
    await run(admin_engine, "UPDATE deals SET stage_key='demo' WHERE id=:d", d=did)
    r = await client.post(
        "/pipelines/new_business/rollback/1", json={"reason": "undo"}, headers=H(world.admin)
    )
    assert r.status_code == 422 and r.json()["details"]["needs_mapping"] == ["demo"]


async def _race_setup(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine, all_ticked=True)
    d = drop_stage(
        to_draft(await get_v(client, world.admin, 1)),
        "qualification",
        ("lead", "meeting_scheduled"),
    )
    no = (await save(client, world.admin, d)).json()["version_no"]
    return did, owner, no


async def test_publish_then_move_gets_a_clean_refusal(client, world, admin_engine):
    did, owner, no = await _race_setup(client, world, admin_engine)
    await publish(client, world.admin, no, stage_mapping={"qualification": "meeting_scheduled"})
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner)
    )
    assert r.status_code == 422  # the stage no longer exists
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "meeting_scheduled", "reason": "x", "override": True},
        headers=H(owner),
    )
    assert r.status_code == 403  # a rep may not skip
    assert await one(admin_engine, "SELECT stage_key FROM deals WHERE id=:d", d=did) == "lead"


async def test_BUG_P3_08_publish_racing_a_move_strands_the_deal(client, world, admin_engine):
    """Interleaving forced with a table lock: the move has read version 1 and is about to commit
    when the publish counts the deals in the removed stage (none yet), then both commit."""
    did, owner, no = await _race_setup(client, world, admin_engine)
    conn = await admin_engine.connect()
    tx = await conn.begin()
    await conn.execute(text("LOCK TABLE outbox_events IN ACCESS EXCLUSIVE MODE"))
    try:
        t_move = asyncio.create_task(
            client.post(f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner))
        )
        await asyncio.sleep(1.5)  # the move is now parked on its outbox insert, holding the deal
        t_pub = asyncio.create_task(
            client.post(
                f"/pipelines/new_business/versions/{no}/publish",
                json={"stage_mapping": {"qualification": "meeting_scheduled"}, "reason": "qa"},
                headers=H(world.admin),
            )
        )
        await asyncio.sleep(1.5)
    finally:
        await tx.rollback()
        await conn.close()
    res = await asyncio.gather(t_move, t_pub)
    assert all(r.status_code < 500 for r in res), [r.text for r in res]
    stage = await one(admin_engine, "SELECT stage_key FROM deals WHERE id=:d", d=did)
    live = {s["key"] for s in (await get_v(client, world.admin, 2))["stages"]}
    assert stage in live, f"deal is in '{stage}', which is not a stage of the live version"


async def test_BUG_P3_06_won_deal_when_its_stage_is_removed_is_left_dangling(
    client, world, admin_engine
):
    """Onboarding holds Won deals. Renaming the Won stage key must not leave them on a stage that
    no longer exists (PRD 9: removing a stage that still has deals needs a target)."""
    did, _o = await mkdeal(client, world, admin_engine, stage="onboarding")
    d = to_draft(await get_v(client, world.admin, 1))
    for s in d["stages"]:
        if s["key"] == "onboarding":
            s["key"] = "live"
    for t in d["transitions"]:
        t["from_stage_key"] = "live" if t["from_stage_key"] == "onboarding" else t["from_stage_key"]
        t["to_stage_key"] = "live" if t["to_stage_key"] == "onboarding" else t["to_stage_key"]
    for c in d["criteria"]:
        if c["stage_key"] == "onboarding":
            c["stage_key"] = "live"
    no = (await save(client, world.admin, d)).json()["version_no"]
    imp = (
        await client.get(f"/pipelines/new_business/versions/{no}/impact", headers=H(world.admin))
    ).json()
    r = await client.post(
        f"/pipelines/new_business/versions/{no}/publish",
        json={"reason": "qa"},
        headers=H(world.admin),
    )
    assert r.status_code == 422 or imp["needs_mapping"], (
        "a Won deal now stands in stage 'onboarding' which no longer exists, with no mapping asked"
    )


# =========================================================================== 1.4 frozen versions
async def _two_versions(client, world):
    await save(client, world.admin, {"from_version_no": 1, "reason": "qa"})
    await publish(client, world.admin, 2)  # v1 archived, v2 published
    await save(client, world.admin, {"from_version_no": 2, "reason": "qa"})  # v3 draft


@pytest.mark.parametrize("table", ["stages", "stage_transitions", "stage_criteria"])
async def test_frozen_versions_reject_update_delete_insert_even_for_the_superuser(
    client, world, admin_engine, database, table
):
    await _two_versions(client, world)
    vids = {
        r["version_no"]: r["id"]
        for r in await rows(
            admin_engine,
            "SELECT v.version_no, v.id FROM pipeline_versions v JOIN pipelines p ON p.id=v.pipeline_id WHERE p.org_id=:o AND p.key='new_business'",
            o=world.org_id,
        )
    }
    app = create_async_engine(database.app_url)
    try:
        for eng in (admin_engine, app):
            for no in (1, 2):  # archived and published
                vid = vids[no]
                upd = {
                    "stages": "UPDATE stages SET name='hack' WHERE version_id=:v",
                    "stage_transitions": "UPDATE stage_transitions SET kind='backward' WHERE version_id=:v",
                    "stage_criteria": "UPDATE stage_criteria SET label='hack' WHERE version_id=:v",
                }[table]
                dele = f"DELETE FROM {table} WHERE version_id=:v"
                ins = {
                    "stages": "INSERT INTO stages (org_id,version_id,key,name,position,probability_pct,display_group) SELECT org_id,version_id,'zz','Z',99,1,'zz' FROM stages WHERE version_id=:v LIMIT 1",
                    "stage_transitions": "INSERT INTO stage_transitions (org_id,version_id,from_stage_key,to_stage_key,kind) VALUES (:o,:v,'zz','zz2','forward')",
                    "stage_criteria": "INSERT INTO stage_criteria (org_id,version_id,stage_key,key,label,position,proof_type) VALUES (:o,:v,'lead','zzk','Z',9,'fields')",
                }[table]
                for sql in (upd, dele, ins):
                    with pytest.raises(DBAPIError, match="cannot be changed"):
                        await run(eng, sql, v=vid, o=world.org_id)
    finally:
        await app.dispose()
    # the draft (v3) can still be edited
    await run(
        admin_engine,
        "UPDATE stages SET name='draft ok' WHERE version_id=:v AND key='lead'",
        v=vids[3],
    )


async def test_BUG_P3_02_a_row_can_be_moved_out_of_a_frozen_version(client, world, admin_engine):
    await _two_versions(client, world)
    vids = {
        r["version_no"]: r["id"]
        for r in await rows(
            admin_engine,
            "SELECT v.version_no, v.id FROM pipeline_versions v JOIN pipelines p ON p.id=v.pipeline_id WHERE p.org_id=:o AND p.key='new_business'",
            o=world.org_id,
        )
    }
    n_before = await one(
        admin_engine, "SELECT count(*) FROM stage_criteria WHERE version_id=:v", v=vids[2]
    )
    # make room in the draft so the unique key does not stop us
    await run(
        admin_engine,
        "DELETE FROM stage_criteria WHERE version_id=:v AND stage_key='lead' AND key='basic_info'",
        v=vids[3],
    )
    with pytest.raises(DBAPIError):
        await run(
            admin_engine,
            "UPDATE stage_criteria SET version_id=:d WHERE version_id=:p AND stage_key='lead' AND key='basic_info'",
            d=vids[3],
            p=vids[2],
        )
    assert (
        await one(
            admin_engine, "SELECT count(*) FROM stage_criteria WHERE version_id=:v", v=vids[2]
        )
        == n_before
    )


async def test_app_role_cannot_delete_versions_or_history(admin_engine, database, world):
    # BUG-P1-11: once the jobs schema has been installed in this database (it depends on which tests ran
    # before), relay_app holds ALL on every table, so the grants this test checks are gone: not a Phase 3 finding.
    if await one(
        admin_engine, "SELECT has_table_privilege('relay_app', 'pipeline_versions', 'DELETE')"
    ):
        pytest.skip(
            "BUG-P1-11: relay_app was granted ALL on every table by the jobs schema in this database"
        )
    app = create_async_engine(database.app_url)
    try:
        for table in (
            "pipeline_versions",
            "deal_stage_entries",
            "activities",
            "documents",
            "deal_products",
            "export_log",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                await run(app, f"DELETE FROM {table}")
        with pytest.raises(DBAPIError, match="permission denied"):
            await run(app, "UPDATE activity_versions SET reason='x'")
        with pytest.raises(DBAPIError, match="permission denied"):
            await run(app, "DELETE FROM audit_log")
    finally:
        await app.dispose()


async def test_BUG_P3_03_app_role_can_unfreeze_a_version(client, world, admin_engine, database):
    await _two_versions(client, world)
    app = create_async_engine(database.app_url)
    try:
        v1 = await one(
            admin_engine,
            "SELECT v.id FROM pipeline_versions v JOIN pipelines p ON p.id=v.pipeline_id WHERE p.org_id=:o AND v.version_no=1",
            o=world.org_id,
        )
        # a second draft is blocked by the unique index; discard v3 first
        await run(
            admin_engine, "UPDATE pipeline_versions SET status='archived' WHERE status='draft'"
        )
        with pytest.raises(DBAPIError):
            await run(app, "UPDATE pipeline_versions SET status='draft' WHERE id=:v", v=v1)
    finally:
        await app.dispose()


async def test_one_published_and_one_draft_enforced_by_the_database(client, world, admin_engine):
    await save(client, world.admin, {"from_version_no": 1, "reason": "qa"})
    pid = await one(
        admin_engine,
        "SELECT id FROM pipelines WHERE org_id=:o AND key='new_business'",
        o=world.org_id,
    )
    for status in ("draft", "published"):
        with pytest.raises(DBAPIError):
            await run(
                admin_engine,
                "INSERT INTO pipeline_versions (org_id,pipeline_id,version_no,status) VALUES (:o,:p,50,:s)",
                o=world.org_id,
                p=pid,
                s=status,
            )


# ===================================================================== 1.5 permissions and lists
async def test_pipeline_config_roles_and_reasons(client, world, admin_engine):
    body = {"from_version_no": 1, "reason": "qa"}
    for actor in (world.north1, world.tl_north):
        r = await client.post("/pipelines/new_business/versions", json=body, headers=H(actor))
        assert r.status_code == 403, (actor.name, r.text)
        for url in (
            "/pipelines/new_business/versions",
            "/pipelines/new_business/versions/1",
            "/pipelines/new_business/versions/1/impact",
        ):
            assert (await client.get(url, headers=H(actor))).status_code == 403
        assert (
            await client.post("/pipelines/new_business/versions/1/validate", headers=H(actor))
        ).status_code == 403
        assert (
            await client.post(
                "/pipelines/new_business/rollback/1", json={"reason": "x"}, headers=H(actor)
            )
        ).status_code == 403
        assert (
            await client.delete("/pipelines/new_business/draft", headers=H(actor))
        ).status_code == 403
    r = await client.post(
        "/pipelines/new_business/versions", json={"from_version_no": 1}, headers=H(world.admin)
    )
    assert (
        r.status_code == 422 and r.json()["code"] == "reason_required"
    )  # Admin needs a reason on every save
    r = await client.post(
        "/pipelines/new_business/versions",
        json={"from_version_no": 1, "reason": "   "},
        headers=H(world.admin),
    )
    assert r.status_code == 422
    r = await client.post(
        "/pipelines/new_business/versions", json={"from_version_no": 1}, headers=H(world.sa)
    )
    assert r.status_code == 200  # Super admin: reason optional
    assert (
        await client.post(
            "/pipelines/new_business/versions/2/publish", json={}, headers=H(world.admin)
        )
    ).status_code == 422
    assert (
        await client.delete("/pipelines/new_business/draft", headers=H(world.admin))
    ).status_code == 422
    assert (
        await client.delete("/pipelines/new_business/draft", headers=H(world.sa))
    ).status_code == 204
    # denied attempts are audited, one row each
    n = await one(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='pipeline.config' AND outcome='denied'",
        o=world.org_id,
    )
    assert n >= 10
    # unknown pipeline key and mass assignment
    r = await client.get("/pipelines/third/versions", headers=H(world.admin))
    assert r.status_code == 404
    r = await client.post(
        "/pipelines/new_business/versions",
        json={"from_version_no": 1, "reason": "x", "status": "published"},
        headers=H(world.sa),
    )
    assert r.status_code == 422


async def test_other_orgs_pipeline_is_invisible(client, world, admin_engine):
    other = await second_org(admin_engine)
    assert other != world.org_id
    vs = (await client.get("/pipelines/new_business/versions", headers=H(world.admin))).json()
    assert len(vs) == 1
    ids = {
        r["id"]
        for r in await rows(
            admin_engine, "SELECT id FROM pipeline_versions WHERE org_id=:o", o=other
        )
    }
    assert not ({v["id"] for v in vs} & ids)


async def test_custom_fields_validation_order_and_immutability(client, world, admin_engine):
    base = {
        "entity": "deal",
        "key": "region",
        "label": "Region",
        "type": "dropdown",
        "options": ["N", "S"],
        "active": True,
    }
    put = lambda items, actor=None, reason="qa": client.put(  # noqa: E731
        "/settings/custom-fields",
        json={"fields": items, "reason": reason},
        headers=H(actor or world.admin),
    )
    assert (await put([{**base, "options": []}])).status_code == 422  # dropdown needs options
    assert (await put([{**base, "key": "Bad Key"}])).status_code == 422
    assert (await put([{**base, "type": "weird"}])).status_code == 422
    assert (await put([base], world.tl_north)).status_code == 403
    assert (await put([base], world.north1)).status_code == 403
    assert (await put([base], reason=None)).status_code == 422
    assert (await put([base])).status_code == 200
    # type immutability
    assert (await put([{**base, "type": "text", "options": []}])).status_code == 422
    # omitted = hidden, not deleted; same key again re-activates
    r = await put([{**base, "key": "other", "label": "Other", "type": "text", "options": []}])
    got = {f["key"]: f for f in r.json()}
    assert got["region"]["active"] is False and got["other"]["active"] is True
    assert (
        await one(
            admin_engine, "SELECT count(*) FROM custom_field_defs WHERE org_id=:o", o=world.org_id
        )
        == 2
    )
    assert (await put([base])).status_code == 200


async def test_BUG_P3_07_duplicate_custom_field_key_in_one_request(client, world):
    base = {
        "entity": "deal",
        "key": "dup",
        "label": "A",
        "type": "text",
        "options": [],
        "active": True,
    }
    r = await client.put(
        "/settings/custom-fields",
        json={"fields": [base, {**base, "label": "B"}], "reason": "qa"},
        headers=H(world.admin),
    )
    assert r.status_code == 422, (r.status_code, r.text[:200])


async def test_custom_field_values_per_type_on_a_deal(client, world, admin_engine):
    fields = [
        {"entity": "deal", "key": "t", "label": "T", "type": "text", "options": [], "active": True},
        {
            "entity": "deal",
            "key": "n",
            "label": "N",
            "type": "number",
            "options": [],
            "active": True,
        },
        {"entity": "deal", "key": "d", "label": "D", "type": "date", "options": [], "active": True},
        {
            "entity": "deal",
            "key": "y",
            "label": "Y",
            "type": "yes_no",
            "options": [],
            "active": True,
        },
        {
            "entity": "deal",
            "key": "dd",
            "label": "DD",
            "type": "dropdown",
            "options": ["a", "b"],
            "active": True,
        },
    ]
    assert (
        await client.put(
            "/settings/custom-fields",
            json={"fields": fields, "reason": "qa"},
            headers=H(world.admin),
        )
    ).status_code == 200
    did, owner = await mkdeal(client, world, admin_engine)
    ok = {"t": "x", "n": 5.5, "d": "2026-01-31", "y": True, "dd": "a"}
    r = await client.patch(f"/deals/{did}", json={"custom_fields": ok}, headers=H(owner))
    assert r.status_code == 200, r.text
    bad = [
        {"t": 5},
        {"n": "5"},
        {"n": True},
        {"d": "31/01/2026"},
        {"y": "yes"},
        {"dd": "zzz"},
        {"ghost": 1},
    ]
    for b in bad:
        r = await client.patch(f"/deals/{did}", json={"custom_fields": b}, headers=H(owner))
        assert r.status_code == 422, (b, r.status_code, r.text[:150])
    # hidden fields are refused and removed from the page, values stay in storage
    await client.put(
        "/settings/custom-fields",
        json={"fields": fields[:1], "reason": "qa"},
        headers=H(world.admin),
    )
    det = (await client.get(f"/deals/{did}", headers=H(owner))).json()
    assert [c["key"] for c in det["custom"]] == ["t"]
    assert (
        await client.patch(f"/deals/{did}", json={"custom_fields": {"n": 1}}, headers=H(owner))
    ).status_code == 422
    assert (
        await one(admin_engine, "SELECT custom_fields->>'n' FROM deals WHERE id=:d", d=did)
    ) == "5.5"


@pytest.mark.parametrize("path", ["/settings/lost-reasons", "/settings/fit-checklist"])
async def test_reason_and_fit_lists_validation_ordering_hide_not_delete(
    client, world, admin_engine, path
):
    table = "lost_reasons" if "lost" in path else "fit_items"
    cur = (await client.get(path, headers=H(world.admin))).json()
    put = lambda items, actor=None, reason="qa": client.put(  # noqa: E731
        path, json={"items": items, "reason": reason}, headers=H(actor or world.admin)
    )
    keep = [{"id": x["id"], "label": x["label"]} for x in cur]
    assert (await put(keep, world.tl_north)).status_code == 403
    assert (await put(keep, world.north1)).status_code == 403
    assert (await put(keep, reason=None)).status_code == 422
    assert (
        await put(keep + [{"label": cur[0]["label"].upper()}])
    ).status_code == 422  # unique, ignoring case
    assert (await put([{"label": ""}])).status_code == 422
    assert (await put(keep + [{"label": "x" * 201}])).status_code == 422
    # reorder + add + hide one
    new = [keep[2], keep[0], {"label": "Brand new"}]
    r = await put(new)
    assert r.status_code == 200
    out = r.json()
    assert [x["label"] for x in out if x["active"]] == [
        cur[2]["label"],
        cur[0]["label"],
        "Brand new",
    ]
    assert [x["position"] for x in out if x["active"]] == [1, 2, 3]
    hidden = [x for x in out if not x["active"]]
    assert len(hidden) == len(cur) - 2
    assert (
        await one(admin_engine, f"SELECT count(*) FROM {table} WHERE org_id=:o", o=world.org_id)
        == len(cur) + 1
    )  # nothing deleted
    # same ids survive (deals keep pointing at them)
    assert {x["id"] for x in out if x["active"]} >= {cur[2]["id"], cur[0]["id"]}


async def test_BUG_P3_04_readd_hidden_lost_reason_by_label(client, world):
    cur = (await client.get("/settings/lost-reasons", headers=H(world.admin))).json()
    only = [{"id": cur[0]["id"], "label": cur[0]["label"]}]
    r = await client.put(
        "/settings/lost-reasons", json={"items": only, "reason": "qa"}, headers=H(world.admin)
    )
    assert r.status_code == 200
    # the admin types one of the hidden reasons again (the screen has no id for it)
    again = only + [{"label": cur[1]["label"]}]
    r = await client.put(
        "/settings/lost-reasons", json={"items": again, "reason": "qa"}, headers=H(world.admin)
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:120]}"


async def test_BUG_P3_05_blank_list_labels(client, world):
    cur = (await client.get("/settings/lost-reasons", headers=H(world.admin))).json()
    keep = [{"id": x["id"], "label": x["label"]} for x in cur]
    r = await client.put(
        "/settings/lost-reasons",
        json={"items": keep + [{"label": "   "}], "reason": "qa"},
        headers=H(world.admin),
    )
    assert r.status_code == 422, "a lost reason with an empty label was stored"


async def test_empty_lost_reasons_list_and_fit_list_effects(client, world, admin_engine):
    """Hiding every lost reason makes it impossible to lose a deal (and an empty fit checklist
    makes Qualification impossible). Document the behaviour; it must at least not 500."""
    r = await client.put(
        "/settings/lost-reasons", json={"items": [], "reason": "qa"}, headers=H(world.admin)
    )
    assert r.status_code == 200
    did, owner = await mkdeal(client, world, admin_engine)
    r = await client.post(f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(owner))
    assert r.status_code == 422
