# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Bring deals that are ALREADY in a stage up to the current published checklist.

A deal keeps the checklist it had when it entered its stage, so when a stage's exit criteria change
(a new pipeline version) only leads that enter the stage afterwards see the change. This script lets
you apply the current published checklist to the deals sitting in the stages you name. Ticks are
kept: a criterion that is still in the checklist keeps its tick and proof; new ones start unticked;
one that was removed simply stops being asked for (its old tick stays in the history).

    python scripts/refresh_stage_checklists.py --stage closure --stage onboarding --dry-run
    python scripts/refresh_stage_checklists.py --stage closure --stage onboarding

Uses MIGRATION_DATABASE_URL (a role that can see every org). Open and won deals are both updated
(Onboarding is ticked after the deal is won). Nothing is written with --dry-run. Idempotent.
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.leads.models import Deal
from app.modules.pipeline.engine.models import DealStageEntry
from app.modules.pipeline.engine.service import published_version, snapshot_for
from app.modules.users.models import Org

PIPELINE = "new_business"


async def main(stages: list[str], dry_run: bool) -> int:
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    changed = 0
    async with sessions() as s:
        for org in (await s.execute(select(Org))).scalars():
            version = await published_version(s, org.id, PIPELINE)
            rows = (
                await s.execute(
                    select(Deal, DealStageEntry)
                    .join(
                        DealStageEntry,
                        (DealStageEntry.deal_id == Deal.id) & DealStageEntry.exited_at.is_(None),
                    )
                    .where(
                        Deal.org_id == org.id,
                        Deal.pipeline == PIPELINE,
                        Deal.stage_key.in_(stages),
                        Deal.status.in_(("open", "won")),
                        Deal.deleted_at.is_(None),
                    )
                )
            ).all()
            for deal, entry in rows:
                fresh = await snapshot_for(s, version.id, entry.stage_key)
                if [c["key"] for c in fresh] == [c["key"] for c in entry.checklist_snapshot] and (
                    entry.version_id == version.id
                ):
                    continue
                before = [c["key"] for c in entry.checklist_snapshot]
                after = [c["key"] for c in fresh]
                print(f"{org.name}: deal {deal.id} in {entry.stage_key}: {before} -> {after}")
                changed += 1
                if not dry_run:
                    entry.checklist_snapshot = fresh
                    entry.version_id = version.id
                    deal.pipeline_version_id = version.id
        if not dry_run:
            await s.commit()
    await engine.dispose()
    print(f"{'WOULD UPDATE' if dry_run else 'UPDATED'} {changed} deal(s)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", action="append", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.stage, a.dry_run)))
