# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Roll out the owner decision "the fit check belongs to the Lead stage" to existing orgs.

New orgs get it from the seed (version 1). Existing orgs have the criterion `fits_profile` in
Qualification inside frozen published versions, which must never be edited. This script, acting
as a Super admin of each org through the pipeline service layer:

  1. reads the current published New business version,
  2. makes a draft in which `fits_profile` sits in Lead right after `contact_verified`
     (and, only if the label is still the original seed text, relabels `need_documented`),
  3. validates and publishes it as a NEW version.

Deals already in the pipeline stay pinned to the version they entered a stage under; leads that
enter a stage from now on use the new version. Idempotent: an org whose published version
already has `fits_profile` in Lead is left alone. An org with an open draft is skipped (never
overwrites someone's work); an org whose published version has no `fits_profile` in
Qualification is skipped with a note.

    python scripts/publish_fit_in_lead.py --org <org uuid>     # one org
    python scripts/publish_fit_in_lead.py --all                # every org
    python scripts/publish_fit_in_lead.py --all --dry-run      # report only

Uses the configured DATABASE_URL (the same as the API). Nothing is written with --dry-run.
"""

import argparse
import asyncio
import sys
import uuid
from typing import Any

from sqlalchemy import select

from app.core.db import dispose_engine, get_sessionmaker
from app.modules.pipeline.config import service
from app.modules.pipeline.config.schemas import DraftIn, PublishIn
from app.modules.users.models import Org, User
from app.platform import registry
from app.platform.context import Actor, RequestInfo

PIPELINE = "new_business"
FIT = "fits_profile"
NEED = "need_documented"
OLD_NEED_LABEL = "Need / pain point documented"
NEW_NEED_LABEL = "Client's problem written down (pain point) and how our product solves it"
REASON = "Fit check moves from Qualification to Lead (owner decision)"


def moved_definition(version: dict[str, Any]) -> dict[str, Any] | None:
    """The full definition (stages, transitions, criteria) with the fit check in Lead, or None
    when there is nothing to do. `version` is the published VersionOut dict."""
    crit = [dict(c) for c in version["criteria"]]
    fit = next((c for c in crit if c["key"] == FIT), None)
    if fit is None or fit["stage_key"] != "qualification":
        return None  # already moved (or removed / customised by the org): leave it alone
    if not any(s["key"] == "lead" for s in version["stages"]):
        return None
    lead = [c for c in crit if c["stage_key"] == "lead"]
    others = [c for c in crit if c is not fit and c["stage_key"] != "lead"]
    after = next((i for i, c in enumerate(lead) if c["key"] == "contact_verified"), len(lead) - 1)
    lead.insert(after + 1, fit)
    fit["stage_key"] = "lead"
    # keep the original order for every other stage; Lead keeps its place at the front
    order = {s["key"]: i for i, s in enumerate(version["stages"])}
    merged = sorted([*lead, *others], key=lambda c: order.get(c["stage_key"], 999))
    for c in merged:
        if c["key"] == NEED and c["label"] == OLD_NEED_LABEL:
            c["label"] = NEW_NEED_LABEL
    keep = ("stage_key", "key", "label", "help_text", "proof_type", "proof_config")
    stage_keys = (
        "key", "name", "probability_pct", "is_entry", "is_final", "won_on_entry",
        "is_conditional", "display_group", "entry_checks",
    )  # fmt: skip
    return {
        "stages": [{k: s[k] for k in stage_keys} for s in version["stages"]],
        "transitions": list(version["transitions"]),
        "criteria": [{k: c[k] for k in keep} for c in merged],
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


async def do_org(org_id: uuid.UUID, name: str, dry_run: bool) -> str:
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
    definition = moved_definition(current)
    if definition is None:
        return f"NO-OP: version {live['version_no']} already has the fit check in Lead (or none)"
    if dry_run:
        return f"WOULD publish a new version from version {live['version_no']}"
    draft = await service.save_draft(
        actor,
        req,
        PIPELINE,
        DraftIn(
            notes="Fit check moved to the Lead stage",
            from_version_no=None,
            reason=REASON,
            **definition,
        ),
    )
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
                print(f"{name} ({oid}): {await do_org(oid, name, dry_run)}")
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
