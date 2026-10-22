# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Version 1 of the New business pipeline (PRD 5) and the starter settings lists.

Plain data plus `seed_statements`, which returns SQL to run on any connection (the migration
uses it for existing orgs, tests and the dev seed use it for new ones). Idempotent per org.

Proof types (validated by app.modules.pipeline.engine.proofs):
  fields        every named field is filled; {"any": [...]} accepts one of several
  activity      a logged activity of a type, optionally two-way / future / completed / notes
  checklist     every active item of the named checklist is ticked (the fit checklist)
  contact_tag   a contact on the account carries the tag
  deal_lines    at least N product lines on the deal
  document      a document of a kind in one of the statuses, optionally with a file
`also` on any criterion adds more checks that must all pass.
"""

import json
import uuid
from typing import Any

# --------------------------------------------------------------------------------------------
STAGES: list[dict[str, Any]] = [
    {"key": "lead", "name": "Lead", "prob": 0, "entry": True},
    {"key": "qualification", "name": "Qualification", "prob": 10},
    {"key": "meeting_scheduled", "name": "Meeting Scheduled", "prob": 10},
    {"key": "prospecting", "name": "Prospecting", "prob": 20},
    # The proposal is drafted and sent at the END of this stage (once the customer has accepted),
    # so nothing is needed to enter it.
    {"key": "proposal", "name": "Proposal", "prob": 20},
    {
        "key": "revision",
        "name": "Revision",
        "prob": 10,
        "group": "revision",
        "entry_checks": [{"type": "fields", "config": {"require": ["feedback"]}}],
    },
    {
        "key": "revision_2",
        "name": "Revision 2",
        "prob": 10,
        "group": "revision",
        "conditional": True,
    },
    {"key": "closure", "name": "Closure", "prob": 90},
    {"key": "onboarding", "name": "Onboarding", "prob": 100, "final": True, "won": True},
]

_REVISION_CRITERIA = [
    {
        "key": "terms_accepted",
        "label": "Customer accepted final terms (final price, scope, agreed by)",
        "type": "fields",
        "config": {"require": ["final_price_paise", "scope", "agreed_by"]},
    },
    {
        "key": "agreement_sent",
        "label": "Agreement sent for signature (status Sent, with the file)",
        "type": "document",
        "config": {"kind": "agreement", "status": ["sent", "countersigned"], "file": True},
    },
]

CRITERIA: dict[str, list[dict[str, Any]]] = {
    "lead": [
        {
            "key": "basic_info",
            "label": "Basic info complete (company, contact, phone or email, source)",
            "type": "fields",
            "config": {
                "require": [
                    "account.name",
                    "contact.name",
                    {"any": ["contact.phone", "contact.email"]},
                    "lead_source",
                ]
            },
        },
        {
            "key": "company_identified",
            "label": "Company identified (name and website or industry)",
            "type": "fields",
            "config": {
                "require": ["account.name", {"any": ["account.website", "account.industry"]}]
            },
        },
        {
            "key": "contact_verified",
            "label": "Contact verified (a two-way call, email or meeting is logged)",
            "type": "activity",
            "config": {"types": ["call", "email", "meeting"], "two_way": True, "completed": True},
        },
        {
            "key": "fits_profile",
            "label": "Fits the target customer profile (fit checklist)",
            "type": "checklist",
            "config": {"list": "fit"},
        },
    ],
    "qualification": [
        {
            "key": "need_documented",
            "label": "Client's problem written down (pain point) and how our product solves it",
            "type": "fields",
            "config": {"require": ["pain_point"]},
        },
        {
            "key": "meeting_scheduled",
            "label": "Meeting booked or held (date, time and attendees logged)",
            "type": "activity",
            # the customer gave a date and it is locked (booked), or the meeting already took place
            "config": {
                "types": ["meeting"],
                "meeting_status": ["booked", "held"],
                "attendees": True,
            },
        },
    ],
    "meeting_scheduled": [
        {
            "key": "meeting_completed",
            "label": "Meeting held and logged (who attended and what was discussed)",
            "type": "activity",
            # the lead only passes this stage once the meeting is HELD; a postponed, cancelled or
            # no-show meeting never counts, and a booked one has not happened yet
            "config": {
                "types": ["meeting"],
                "meeting_status": ["held"],
                "attendees": True,
                "notes": True,
            },
        },
        {
            "key": "opportunity_defined",
            "label": "Business opportunity defined (use case)",
            "type": "fields",
            "config": {"require": ["use_case"]},
        },
    ],
    "prospecting": [
        {
            "key": "budget_confirmed",
            "label": "Budget confirmed (amount and source)",
            "type": "fields",
            "config": {"require": ["budget_amount_paise", "budget_source"]},
        },
        {
            "key": "decision_maker",
            "label": "Decision maker identified (name, phone and email on file)",
            "type": "contact_tag",
            # reachable: the proposal is emailed to this person
            "config": {"tag": "decision_maker", "require": ["name", "phone", "email"]},
        },
        {
            "key": "timeline_agreed",
            "label": "Timeline agreed (expected close date and reason)",
            "type": "fields",
            "config": {"require": ["expected_close_date", "expected_close_reason"]},
        },
        {
            "key": "pricing_selected",
            "label": "Pricing model, plan and expected minutes per month selected from the catalog",
            "type": "deal_lines",
            "config": {"min_lines": 1},
            "also": [
                {
                    "type": "fields",
                    "config": {"require": ["pricing_model", "plan", "expected_minutes_per_month"]},
                }
            ],
        },
    ],
    "proposal": [
        {
            "key": "walkthrough_logged",
            "label": "Proposal walkthrough meeting held and logged (this round)",
            "type": "activity",
            # Each round has its own meeting: after a change request a NEW meeting is held to
            # revise the proposal, so only a meeting held after the last round closed counts.
            "config": {
                "types": ["meeting"],
                "meeting_status": ["held"],
                "attendees": True,
                "notes": True,
                "after_last_round": True,
            },
        },
        {
            "key": "feedback_recorded",
            "label": "Customer accepted the proposal (changes or objections start a new round)",
            "type": "proposal_round",
            "config": {"feedback": "accepted"},
        },
        {
            "key": "proposal_emailed",
            "label": "Proposal sent by email (draft, recipient name and email)",
            "type": "proposal_sent",
            "config": {"statuses": ["sent", "accepted"]},
        },
        {
            "key": "decision_date_set",
            "label": "Decision date set (the day the customer will decide)",
            "type": "fields",
            "config": {"require": ["decision_date"]},
        },
    ],
    "revision": _REVISION_CRITERIA,
    "revision_2": _REVISION_CRITERIA,
    "closure": [
        {
            "key": "agreement_countersigned",
            "label": "Agreement countersigned (status and signed file)",
            "type": "document",
            "config": {"kind": "agreement", "status": ["countersigned"], "file": True},
        },
        {
            "key": "advance_received",
            "label": "Advance received (amount, who received it and their details, with the "
            "receipt)",
            "type": "document",
            "config": {"kind": "advance", "status": ["received"], "file": True},
            "also": [
                {
                    "type": "fields",
                    "config": {
                        "require": [
                            "advance_amount_paise",
                            "advance_received_by",
                            "advance_received_contact",
                            "advance_received_on",
                        ]
                    },
                }
            ],
        },
        {
            "key": "onboarding_info",
            "label": "Onboarding information collected (billing contact, start date, pricing "
            "model, term, billing frequency, renewal date)",
            "type": "fields",
            "config": {
                "require": [
                    "billing_contact",
                    "subscription_start_date",
                    "pricing_model",
                    "contract_term",
                    "billing_frequency",
                    "renewal_date",
                ]
            },
        },
    ],
    "onboarding": [
        {
            "key": "prerequisite_document",
            "label": "Pre-requisite document received (the file, what it covers and when)",
            "type": "document",
            "config": {"kind": "prerequisite", "status": ["received"], "file": True},
            "also": [
                {
                    "type": "fields",
                    "config": {"require": ["prereq_documents", "prereq_received_on"]},
                }
            ],
        },
        {
            "key": "technical_document",
            "label": "Technical document received (the file, what it covers and when)",
            "type": "document",
            "config": {"kind": "technical", "status": ["received"], "file": True},
            "also": [
                {
                    "type": "fields",
                    "config": {"require": ["technical_documents", "technical_received_on"]},
                }
            ],
        },
        {
            "key": "kickoff_done",
            "label": "Kickoff done (logged, with the go-live date)",
            "type": "activity",
            "config": {"types": ["kickoff"], "completed": True},
            "also": [{"type": "fields", "config": {"require": ["go_live_date"]}}],
        },
        {
            "key": "agent_live",
            "label": "Live with the customer (live date and note)",
            "type": "fields",
            "config": {"require": ["live_date", "live_note"]},
        },
        {
            "key": "first_invoice",
            "label": "First invoice raised",
            "type": "document",
            "config": {"kind": "invoice", "status": ["draft", "sent", "countersigned"]},
        },
    ],
}

# forward transitions (revision -> closure is the normal path; revision_2 is conditional)
_FORWARD = [
    ("lead", "qualification"),
    ("qualification", "meeting_scheduled"),
    ("meeting_scheduled", "prospecting"),
    ("prospecting", "proposal"),
    ("proposal", "revision"),
    ("revision", "closure"),
    ("revision_2", "closure"),
    ("closure", "onboarding"),
]
_CONDITIONAL = [("revision", "revision_2"), ("revision_2", "revision_2")]

LOST_REASONS = [
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

# The fit questions a rep answers about a lead before it can leave Lead. The Super admin
# or an Admin can change this list any time under Settings lists (PRD 5 and open item 17).
FIT_ITEMS = [
    "The company website is real and opens properly",
    "The client phone number is correct and reachable",
    "The client is interested in what we offer",
    "The company is genuine (not a fraud or scam)",
]


def transitions() -> list[tuple[str, str, str]]:
    order = [s["key"] for s in STAGES]
    out = [(a, b, "forward") for a, b in _FORWARD] + [
        (a, b, "conditional") for a, b in _CONDITIONAL
    ]
    for i, to_key in enumerate(order):
        out += [(frm, to_key, "backward") for frm in order[i + 1 :]]
    return out


def seed_statements(org_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    """SQL to give `org_id` its two pipelines, New business version 1 (published), the lost
    reasons and the fit checklist. Skips anything that already exists."""
    nb, ec, ver = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    o = {"o": org_id}
    out: list[tuple[str, dict[str, Any]]] = [
        (
            "INSERT INTO pipelines (id, org_id, key, name) VALUES (:id, :o, 'new_business', "
            "'New business') ON CONFLICT (org_id, key) DO NOTHING",
            {**o, "id": nb},
        ),
        (
            "INSERT INTO pipelines (id, org_id, key, name) VALUES (:id, :o, 'existing_customer', "
            "'Existing customer') ON CONFLICT (org_id, key) DO NOTHING",
            {**o, "id": ec},
        ),
    ]
    # Version 1 only when the org has no version yet. Children are written while it is a draft
    # (published versions are frozen by a trigger), then it is published.
    guard = (
        "(SELECT id FROM pipelines WHERE org_id = :o AND key = 'new_business') "
        "AND NOT EXISTS (SELECT 1 FROM pipeline_versions v "
        "JOIN pipelines p ON p.id = v.pipeline_id "
        "WHERE p.org_id = :o AND p.key = 'new_business')"
    )
    out.append(
        (
            "INSERT INTO pipeline_versions (id, org_id, pipeline_id, version_no, status, notes) "
            f"SELECT :v, :o, id, 1, 'draft', 'Initial New business pipeline' FROM pipelines "
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
    for pos, label in enumerate(LOST_REASONS, start=1):
        out.append(
            (
                "INSERT INTO lost_reasons (org_id, label, position) VALUES (:o, :l, :p) "
                "ON CONFLICT (org_id, label) DO NOTHING",
                {**o, "l": label, "p": pos},
            )
        )
    for pos, label in enumerate(FIT_ITEMS, start=1):
        out.append(
            (
                "INSERT INTO fit_items (org_id, label, position) SELECT :o, :l, :p WHERE NOT "
                "EXISTS (SELECT 1 FROM fit_items WHERE org_id = :o AND label = :l)",
                {**o, "l": label, "p": pos},
            )
        )
    return out
