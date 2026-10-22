# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Roll the current New business rules out to existing orgs (new orgs get them from the seed).

The rules this brings (owner decisions, 16-17 Oct 2026):
  - Qualification "Meeting booked or held", Meeting Scheduled "Meeting held and logged": a booked
    meeting is locked until it is held; postponing or cancelling needs a reason.
  - Prospecting "Decision maker identified" needs the decision maker's name, phone and email.
  - Proposal: walkthrough meeting held -> customer answers; changes or objections close the round
    and a new meeting revises the proposal; once accepted the proposal is drafted and emailed
    (recipient name and email), then the decision date is set. Nothing is needed to enter Proposal
    any more (the proposal is made at its end).

Published versions are frozen, so this script, acting as a Super admin of each org through the
pipeline service layer, reads the current published New business version, makes the criteria and
stage entry checks match the seed, and publishes the result as a NEW version. Deals already in a
stage keep the checklist they entered it with; leads that enter a stage from now on use the new
one. Idempotent: an org whose published version already matches is left alone (a criterion the
org removed is not put back). An org with an open draft is skipped (never overwrites someone's
work).

    python scripts/publish_pipeline_rules.py --org <org uuid>     # one org
    python scripts/publish_pipeline_rules.py --all                # every org
    python scripts/publish_pipeline_rules.py --all --dry-run      # report only

Uses the configured DATABASE_URL (the same as the API). Nothing is written with --dry-run.
"""

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from sqlalchemy import select

from app.core.db import dispose_engine, get_sessionmaker
from app.modules.pipeline.config import service
from app.modules.pipeline.config.schemas import DraftIn, PublishIn
from app.modules.pipeline.seeds.new_business import CRITERIA, STAGES
from app.modules.users.models import Org, User
from app.platform import registry
from app.platform.context import Actor, RequestInfo

PIPELINE = "new_business"
# single criteria replaced in place by the seed's definition (stage, key)
SINGLE = (
    ("qualification", "meeting_scheduled"),
    ("meeting_scheduled", "meeting_completed"),
    ("prospecting", "decision_maker"),
)
# stages whose whole criteria list and entry checks must match the seed
WHOLE_STAGES = ("proposal", "closure", "onboarding")
REASON = "Meetings, decision maker details, the proposal loop and onboarding pre-requisites"


def seed_row(stage: str, c: dict[str, Any]) -> dict[str, Any]:
    config = dict(c["config"])
    if c.get("also"):
        config["also"] = c["also"]
    return {
        "stage_key": stage, "key": c["key"], "label": c["label"], "help_text": None,
        "proof_type": c["type"], "proof_config": config,
    }  # fmt: skip


def signature(rows: list[dict[str, Any]]) -> str:
    return json.dumps(
        [[r["key"], r["label"], r["proof_type"], r["proof_config"]] for r in rows], sort_keys=True
    )


def updated_definition(version: dict[str, Any]) -> dict[str, Any] | None:
    """The full definition matching the seed, or None when the version already does."""
    crit = [dict(c) for c in version["criteria"]]
    stages = [dict(s) for s in version["stages"]]
    changed = False
    for stage, key in SINGLE:
        row = next((c for c in crit if c["stage_key"] == stage and c["key"] == key), None)
        if row is None:
            continue  # the org removed it: not ours to put back
        want = seed_row(stage, next(c for c in CRITERIA[stage] if c["key"] == key))
        if (row["label"], row["proof_type"], row["proof_config"]) != (
            want["label"], want["proof_type"], want["proof_config"],
        ):  # fmt: skip
            row.update(label=want["label"], proof_type=want["proof_type"])
            row["proof_config"] = want["proof_config"]
            changed = True
    for stage in WHOLE_STAGES:
        st = next((s for s in stages if s["key"] == stage), None)
        if st is None:
            continue
        want_rows = [seed_row(stage, c) for c in CRITERIA[stage]]
        if signature([c for c in crit if c["stage_key"] == stage]) != signature(want_rows):
            rebuilt: list[dict[str, Any]] = []
            placed = False
            for c in crit:
                if c["stage_key"] != stage:
                    rebuilt.append(c)
                elif not placed:
                    rebuilt.extend(want_rows)
                    placed = True
            crit = rebuilt if placed else [*crit, *want_rows]
            changed = True
        want_checks = next(s for s in STAGES if s["key"] == stage).get("entry_checks", [])
        if st.get("entry_checks", []) != want_checks:
            st["entry_checks"] = want_checks
            changed = True
    if not changed:
        return None
    keep = ("stage_key", "key", "label", "help_text", "proof_type", "proof_config")
    stage_keys = (
        "key", "name", "probability_pct", "is_entry", "is_final", "won_on_entry",
        "is_conditional", "display_group", "entry_checks",
    )  # fmt: skip
    return {
        "stages": [{k: s[k] for k in stage_keys} for s in stages],
        "transitions": list(version["transitions"]),
        "criteria": [{k: c[k] for k in keep} for c in crit],
    }


async def super_admin_of(org_id: uuid.UUID) -> Actor | None:
    async with get_sessionmaker()() as s:
        u = (
            await s.execute(
                select(User)
                .where(
                    User.org_id == org_id,
                    User.role == "super_admin",
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
                .order_by(User.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
    if u is None:
        return None
    return Actor(
        id=u.id, org_id=u.org_id, role=u.role, team_id=u.team_id, name=u.name, email=u.email
    )


async def do_org(org_id: uuid.UUID, dry_run: bool) -> str:
    actor = await super_admin_of(org_id)
    if actor is None:
        return "SKIPPED: no active Super admin"
    req = RequestInfo(origin="system_job")
    async with get_sessionmaker()() as s:
        versions = await service.list_versions(s, actor, PIPELINE)
    if any(v["status"] == "draft" for v in versions):
        return "SKIPPED: an open draft exists (publish or discard it first)"
    live = next((v for v in versions if v["status"] == "published"), None)
    if live is None:
        return "SKIPPED: no published version"
    async with get_sessionmaker()() as s:
        current = await service.get_version(s, actor, PIPELINE, live["version_no"])
    definition = updated_definition(current)
    if definition is None:
        return f"NO-OP: version {live['version_no']} already has these rules (or customised)"
    if dry_run:
        return f"WOULD publish a new version from version {live['version_no']}"
    draft = await service.save_draft(
        actor,
        req,
        PIPELINE,
        DraftIn(notes="Meetings, decision maker and proposal rules", from_version_no=None,
                reason=REASON, **definition),
    )  # fmt: skip
    published = await service.publish(
        actor, req, PIPELINE, draft["version_no"], PublishIn(reason=REASON)
    )
    return f"PUBLISHED version {published['version_no']} (was {live['version_no']})"


async def main(org: uuid.UUID | None, dry_run: bool) -> int:
    registry.setup()
    failures = 0
    try:
        async with get_sessionmaker()() as s:
            q = select(Org.id, Org.name).order_by(Org.created_at)
            if org is not None:
                q = q.where(Org.id == org)
            orgs = [(r.id, r.name) for r in (await s.execute(q)).all()]
        if not orgs:
            print("No matching org.")
            return 1
        for oid, name in orgs:
            try:
                print(f"{name} ({oid}): {await do_org(oid, dry_run)}")
            except Exception as e:  # report and carry on with the next org
                failures += 1
                print(f"{name} ({oid}): FAILED: {type(e).__name__}: {e}")
    finally:
        await dispose_engine()
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--org", type=uuid.UUID, help="one org id")
    g.add_argument("--all", action="store_true", help="every org")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.org, a.dry_run)))
