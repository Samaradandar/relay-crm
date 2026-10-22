# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 7 (P7-04) leakage test: a value changed AFTER a stage entry's snapshot time must never
be visible to that row -- the one rule the whole training-data builder exists to protect.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.modules.ml.dataset import EDIT_KEYS, _value_as_of, build_dataset
from tests.modules.journey import run_to


@pytest.fixture
async def session(database):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database.app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_value_changed_after_snapshot_is_never_visible_to_that_row(
    client, world, admin_engine, session
):
    deal, owner = await run_to(client, world, admin_engine, "onboarding")
    deal_id = uuid.UUID(deal["id"])
    async with admin_engine.begin() as c:
        snapshot_at = (
            await c.execute(
                text(
                    "SELECT entered_at FROM deal_stage_entries WHERE deal_id=:d ORDER BY entered_at LIMIT 1"
                ),
                {"d": deal_id},
            )
        ).scalar_one()
    assert snapshot_at.tzinfo is not None

    after_snapshot = snapshot_at + timedelta(hours=1)
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO audit_log (id, org_id, actor_role, action_key, entity_type, entity_id, "
                "occurred_at, new_value, prev_hash, row_hash) VALUES "
                "(:i, :o, 'admin', 'deal.edit', 'deal', :d, :at, :nv, 'x', 'y')"
            ),
            {
                "i": uuid.uuid4(),
                "o": world.org_id,
                "d": str(deal_id),
                "at": after_snapshot,
                "nv": '{"value_paise": 999999999}',
            },
        )
        await c.execute(text("UPDATE deals SET value_paise=999999999 WHERE id=:d"), {"d": deal_id})

    # at the snapshot time, before that edit: the leaked value must not come through.
    value_at_snapshot = await _value_as_of(
        session, world.org_id, deal_id, "value_paise", snapshot_at, 999999999
    )
    assert value_at_snapshot != 999999999

    # the dataset builder itself must carry the same guarantee for every row of this deal.
    ds = await build_dataset(session, world.org_id)
    rows_for_this_deal = [r for r in ds["rows"] if r["deal_id"] == str(deal_id)]
    assert rows_for_this_deal
    assert all(r["value_paise"] != 999999999 for r in rows_for_this_deal)


async def test_no_rep_or_team_field_is_a_feature():
    from app.modules.ml.dataset import FEATURE_LIST

    forbidden = {"owner_id", "owner", "team_id", "team", "rep", "assigned_by", "created_by"}
    assert not (set(FEATURE_LIST) & forbidden)


async def test_edit_keys_cover_the_fields_the_builder_reconstructs():
    assert "deal.edit" in EDIT_KEYS
    assert "deal.close_date.change" in EDIT_KEYS
