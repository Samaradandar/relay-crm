# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Database invariants every Phase 3 deep test (and the fuzz) must leave true."""

from tests.deep.phase3.p3 import rows


async def check_invariants(eng, org_id, *, allow_stale_flag: bool = True) -> list[str]:
    """Return a list of broken invariants (empty = all good)."""
    from app.core.db import get_sessionmaker
    from app.modules.leads.models import Deal
    from app.modules.pipeline.engine.models import DealCriterion, DealStageEntry
    from app.modules.pipeline.engine.proofs import build_ctx, run_proof
    from app.platform.audit.service import verify_chain

    bad: list[str] = []
    q = lambda sql, **kw: rows(eng, sql, o=org_id, **kw)  # noqa: E731

    for r in await q(
        "SELECT d.id, d.status, d.stage_key, "
        "(SELECT count(*) FROM deal_stage_entries e WHERE e.deal_id=d.id AND e.exited_at IS NULL) AS n_open, "
        "(SELECT e.stage_key FROM deal_stage_entries e WHERE e.deal_id=d.id AND e.exited_at IS NULL LIMIT 1) AS open_stage "
        "FROM deals d WHERE d.org_id=:o AND d.deleted_at IS NULL"
    ):
        want = 0 if r["status"] == "lost" else 1
        if r["n_open"] != want:
            bad.append(f"deal {r['id']} ({r['status']}) has {r['n_open']} open stage entries")
        elif want == 1 and r["open_stage"] != r["stage_key"]:
            bad.append(
                f"deal {r['id']}: stage_key {r['stage_key']} != open entry {r['open_stage']}"
            )
    for r in await q(
        "SELECT id, status, won_at, lost_at, lost_reason FROM deals WHERE org_id=:o AND deleted_at IS NULL"
    ):
        ok = {
            "won": r["won_at"] is not None and r["lost_at"] is None,
            "lost": r["lost_at"] is not None and r["won_at"] is None and bool(r["lost_reason"]),
            "open": r["won_at"] is None and r["lost_at"] is None and r["lost_reason"] is None,
            "pooled": r["lost_at"] is None and r["won_at"] is None,
        }.get(r["status"], False)
        if not ok:
            bad.append(f"deal {r['id']}: status {r['status']} vs won_at/lost_at {r}")
    for r in await q(
        "SELECT d.id, d.value_paise, coalesce((SELECT sum(line_total_paise) FROM deal_products p "
        "WHERE p.deal_id=d.id AND p.deleted_at IS NULL),0) AS s FROM deals d WHERE d.org_id=:o"
    ):
        if r["value_paise"] != r["s"]:
            bad.append(f"deal {r['id']}: value {r['value_paise']} != sum of lines {r['s']}")
    for r in await q(
        "SELECT d.id, (SELECT count(*) FROM next_steps n WHERE n.deal_id=d.id AND n.done_at IS NULL) AS n "
        "FROM deals d WHERE d.org_id=:o AND d.deleted_at IS NULL AND d.status IN ('open','pooled')"
    ):
        if r["n"] != 1:
            bad.append(f"deal {r['id']} has {r['n']} open next steps")
    for r in await q(
        "SELECT c.id, c.criterion_key FROM deal_criteria c JOIN deal_stage_entries e ON e.id=c.entry_id "
        "WHERE c.org_id=:o AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(e.checklist_snapshot) s "
        "WHERE s->>'key' = c.criterion_key)"
    ):
        bad.append(f"criterion row {r['id']} ({r['criterion_key']}) is not in its entry's snapshot")
    for r in await q(
        "SELECT c.id FROM deal_criteria c WHERE c.org_id=:o AND NOT EXISTS (SELECT 1 FROM deal_stage_entries e WHERE e.id=c.entry_id AND e.deal_id=c.deal_id)"
    ):
        bad.append(f"criterion row {r['id']} has no entry of its deal")
    for r in await q(
        "SELECT deal_id, entry_id, criterion_key, count(*) FROM deal_criteria WHERE org_id=:o GROUP BY 1,2,3 HAVING count(*)>1"
    ):
        bad.append(f"duplicate tick {r}")

    # proof-missing marks equal a fresh recomputation
    from sqlalchemy import select

    async with get_sessionmaker()() as s:
        deals = (
            (
                await s.execute(
                    select(Deal).where(
                        Deal.org_id == org_id,
                        Deal.deleted_at.is_(None),
                        Deal.status.in_(("open", "won")),
                    )
                )
            )
            .scalars()
            .all()
        )
        for deal in deals:
            crit = (
                (
                    await s.execute(
                        select(DealCriterion).where(
                            DealCriterion.deal_id == deal.id,
                            DealCriterion.verify_status != "overridden",
                        )
                    )
                )
                .scalars()
                .all()
            )
            crit = [c for c in crit if not (c.proof_ref or {}).get("forced")]  # planted by setup
            if not crit:
                if deal.flags.get("proof_missing") and not allow_stale_flag:
                    bad.append(f"deal {deal.id}: proof_missing flag without any tick")
                continue
            ctx = await build_ctx(s, deal, recheck=True)
            missing_now = False
            for c in crit:
                entry = await s.get(DealStageEntry, c.entry_id)
                item = next(i for i in entry.checklist_snapshot if i["key"] == c.criterion_key)
                res = await run_proof(ctx, item["proof_type"], item["proof_config"], c.proof_ref)
                if (not res.ok) != c.proof_missing:
                    bad.append(
                        f"deal {deal.id}: {c.criterion_key} proof_missing={c.proof_missing}, fresh check says ok={res.ok}"
                    )
                missing_now = missing_now or not res.ok
            if missing_now and not deal.flags.get("proof_missing"):
                bad.append(f"deal {deal.id}: a tick lacks proof but the deal is not flagged")
            if deal.flags.get("proof_missing") and not missing_now and not allow_stale_flag:
                bad.append(f"deal {deal.id}: flagged proof_missing but every proof is present")
        res = await verify_chain(s, org_id)
        if not res.ok:
            bad.append(f"audit chain broken: {res.problem} at {res.first_bad_seq}")
    return bad
