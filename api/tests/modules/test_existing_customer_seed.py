# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Existing customer pipeline seed (Phase 5, first PR): 5 stages, right keys/probabilities,
and each stage has the criteria the PRD table lists. The `world` fixture already seeds it
(via seed_org_pipeline), so this reads straight from the database it set up."""

from sqlalchemy import text

EXPECTED_STAGES = {
    "request": 10,
    "proposal": 30,
    "revision": 50,
    "closure": 90,
    "updated": 100,
}

EXPECTED_CRITERIA = {
    "request": {"request_type_recorded", "requirement_documented", "contact_confirmed"},
    "proposal": {"proposal_sent", "feedback_recorded", "decision_date_set"},
    "revision": {"terms_accepted", "amendment_sent"},
    "closure": {"amendment_countersigned", "new_plan_recorded"},
    "updated": {"subscription_updated", "first_revised_invoice"},
}


async def test_existing_customer_pipeline_is_seeded_published_with_5_stages(world, admin_engine):
    async with admin_engine.begin() as c:
        version = (
            await c.execute(
                text(
                    "SELECT v.id, v.status FROM pipeline_versions v "
                    "JOIN pipelines p ON p.id = v.pipeline_id "
                    "WHERE p.org_id = :o AND p.key = 'existing_customer'"
                ),
                {"o": world.org_id},
            )
        ).one()
        version_id, status = version
        assert status == "published"

        stages = (
            await c.execute(
                text(
                    "SELECT key, probability_pct, is_entry, is_final, won_on_entry "
                    "FROM stages WHERE version_id = :v ORDER BY position"
                ),
                {"v": version_id},
            )
        ).all()
        assert [s[0] for s in stages] == ["request", "proposal", "revision", "closure", "updated"]
        for key, prob, is_entry, is_final, won in stages:
            assert prob == EXPECTED_STAGES[key]
            assert is_entry == (key == "request")
            assert is_final == (key == "updated")
            assert won == (key == "updated")

        criteria = (
            await c.execute(
                text("SELECT stage_key, key FROM stage_criteria WHERE version_id = :v"),
                {"v": version_id},
            )
        ).all()
        by_stage: dict[str, set[str]] = {}
        for stage_key, key in criteria:
            by_stage.setdefault(stage_key, set()).add(key)
        assert by_stage == EXPECTED_CRITERIA

        pipelines = (
            await c.execute(
                text(
                    "SELECT count(*) FROM pipelines WHERE org_id = :o AND key = 'existing_customer'"
                ),
                {"o": world.org_id},
            )
        ).scalar_one()
        assert pipelines == 1  # seeded once, not duplicated alongside new_business's own insert
