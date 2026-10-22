# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Version 1 of the Existing customer pipeline (Phase 5 PRD): a subscription change raised on
an existing account (upsell, downsell, plan change) runs through Request -> Proposal -> Revision
-> Closure -> Updated.

Same shape as seeds/new_business.py: plain data plus `seed_statements`, idempotent per org. The
`existing_customer` pipeline row itself is already inserted by new_business.seed_statements (it
owns both pipeline rows so an org gets exactly one of each); this module only adds the version,
its stages/transitions/criteria, and publishes it.

Proof types: see app.modules.pipeline.engine.proofs. `also` on any criterion adds more checks
that must all pass.
"""

import json
import uuid
from typing import Any

# --------------------------------------------------------------------------------------------
STAGES: list[dict[str, Any]] = [
    {"key": "request", "name": "Request", "prob": 10, "entry": True},
    {
        "key": "proposal",
        "name": "Proposal",
        "prob": 30,
        "entry_checks": [
            {"type": "document", "config": {"kind": "proposal", "status": ["draft", "sent"]}}
        ],
    },
    {
        "key": "revision",
        "name": "Revision",
        "prob": 50,
        "entry_checks": [{"type": "fields", "config": {"require": ["feedback"]}}],
    },
    {"key": "closure", "name": "Closure", "prob": 90},
    {"key": "updated", "name": "Updated", "prob": 100, "final": True, "won": True},
]

CRITERIA: dict[str, list[dict[str, Any]]] = {
    "request": [
        {
            "key": "request_type_recorded",
            "label": "Request type recorded (upsell, downsell or plan change)",
            "type": "fields",
            "config": {"require": ["request_type"]},
        },
        {
            "key": "requirement_documented",
            "label": "Requirement documented (what the customer wants changed, and why)",
            "type": "fields",
            "config": {"require": ["requirement"]},
        },
        {
            "key": "contact_confirmed",
            "label": "Customer contact confirmed (a two-way call, email or meeting is logged)",
            "type": "activity",
            "config": {"types": ["call", "email", "meeting"], "two_way": True, "completed": True},
        },
    ],
    "proposal": [
        {
            "key": "proposal_sent",
            "label": "Proposal sent (status Sent, with the file)",
            "type": "document",
            "config": {"kind": "proposal", "status": ["sent"], "file": True},
        },
        {
            "key": "feedback_recorded",
            "label": "Customer feedback recorded (Accepted / Changes requested / Objections)",
            "type": "fields",
            "config": {"require": ["feedback"]},
        },
        {
            "key": "decision_date_set",
            "label": "Decision date set",
            "type": "fields",
            "config": {"require": ["decision_date"]},
        },
    ],
    "revision": [
        {
            "key": "terms_accepted",
            "label": "Final terms accepted (final price, scope, agreed by)",
            "type": "fields",
            "config": {"require": ["final_price_paise", "scope", "agreed_by"]},
        },
        {
            "key": "amendment_sent",
            "label": "Agreement or amendment sent for signature (status Sent, with the file)",
            "type": "document",
            "config": {"kind": "agreement", "status": ["sent", "countersigned"], "file": True},
        },
    ],
    "closure": [
        {
            "key": "amendment_countersigned",
            "label": "Agreement or amendment countersigned (status and signed file)",
            "type": "document",
            "config": {"kind": "agreement", "status": ["countersigned"], "file": True},
        },
        {
            "key": "new_plan_recorded",
            "label": "New plan and effective date recorded",
            "type": "closure_change",
            "config": {},
        },
    ],
    "updated": [
        {
            "key": "subscription_updated",
            "label": "Subscription updated to the new plan",
            "type": "fields",
            "config": {"require": ["subscription_updated_at"]},
        },
        {
            "key": "first_revised_invoice",
            "label": "First revised invoice raised",
            "type": "document",
            "config": {"kind": "invoice", "status": ["draft", "sent", "countersigned"]},
        },
    ],
}

_FORWARD = [
    ("request", "proposal"),
    ("proposal", "revision"),
    ("revision", "closure"),
    ("closure", "updated"),
]


def transitions() -> list[tuple[str, str, str]]:
    order = [s["key"] for s in STAGES]
    out = [(a, b, "forward") for a, b in _FORWARD]
    for i, to_key in enumerate(order):
        out += [(frm, to_key, "backward") for frm in order[i + 1 :]]
    return out


def seed_statements(org_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    """SQL to give `org_id` Existing customer version 1 (published). The pipeline row itself is
    created by new_business.seed_statements; this only adds it if that hasn't run yet, then
    writes the version. Skips anything that already exists."""
    ec, ver = uuid.uuid4(), uuid.uuid4()
    o = {"o": org_id}
    out: list[tuple[str, dict[str, Any]]] = [
        (
            "INSERT INTO pipelines (id, org_id, key, name) VALUES (:id, :o, 'existing_customer', "
            "'Existing customer') ON CONFLICT (org_id, key) DO NOTHING",
            {**o, "id": ec},
        ),
    ]
    guard = (
        "(SELECT id FROM pipelines WHERE org_id = :o AND key = 'existing_customer') "
        "AND NOT EXISTS (SELECT 1 FROM pipeline_versions v "
        "JOIN pipelines p ON p.id = v.pipeline_id "
        "WHERE p.org_id = :o AND p.key = 'existing_customer')"
    )
    out.append(
        (
            "INSERT INTO pipeline_versions (id, org_id, pipeline_id, version_no, status, notes) "
            f"SELECT :v, :o, id, 1, 'draft', 'Initial Existing customer pipeline' FROM pipelines "
            f"WHERE id = {guard}",
            {**o, "v": ver},
        )
    )
    for pos, s in enumerate(STAGES, start=1):
        out.append(
            (
                "INSERT INTO stages (org_id, version_id, key, name, position, probability_pct, "
                "is_entry, is_final, won_on_entry, is_conditional, display_group, entry_checks) "
                "SELECT :o, :v, :k, :n, :p, :pr, :e, :f, :w, :c, :g, CAST(:ec AS jsonb) "
                "WHERE EXISTS (SELECT 1 FROM pipeline_versions WHERE id = :v)",
                {
                    **o,
                    "v": ver,
                    "k": s["key"],
                    "n": s["name"],
                    "p": pos,
                    "pr": s["prob"],
                    "e": bool(s.get("entry")),
                    "f": bool(s.get("final")),
                    "w": bool(s.get("won")),
                    "c": bool(s.get("conditional")),
                    "g": s.get("group", s["key"]),
                    "ec": json.dumps(s.get("entry_checks", [])),
                },
            )
        )
    for frm, to, kind in transitions():
        out.append(
            (
                "INSERT INTO stage_transitions (org_id, version_id, from_stage_key, to_stage_key, "
                "kind) SELECT :o, :v, :f, :t, :k WHERE EXISTS "
                "(SELECT 1 FROM pipeline_versions WHERE id = :v)",
                {**o, "v": ver, "f": frm, "t": to, "k": kind},
            )
        )
    for stage_key, items in CRITERIA.items():
        for pos, c in enumerate(items, start=1):
            config = {**c["config"]}
            if c.get("also"):
                config["also"] = c["also"]
            out.append(
                (
                    "INSERT INTO stage_criteria (org_id, version_id, stage_key, key, label, "
                    "position, proof_type, proof_config) SELECT :o, :v, :s, :k, :l, :p, :t, "
                    "CAST(:cfg AS jsonb) WHERE EXISTS "
                    "(SELECT 1 FROM pipeline_versions WHERE id = :v)",
                    {
                        **o,
                        "v": ver,
                        "s": stage_key,
                        "k": c["key"],
                        "l": c["label"],
                        "p": pos,
                        "t": c["type"],
                        "cfg": json.dumps(config),
                    },
                )
            )
    out.append(
        (
            "UPDATE pipeline_versions SET status = 'published', published_at = now() WHERE id = :v",
            {"v": ver},
        )
    )
    return out
