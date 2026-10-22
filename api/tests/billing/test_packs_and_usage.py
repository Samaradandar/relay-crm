# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-07/08: minute packs, drawdown, splitting, corrections, overage, usage-sync mechanics."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.modules.billing.packs import draw, list_packs
from app.modules.usage_feed.models import UsageRecord
from app.modules.usage_feed.service import Page, UsageRow, run_sync
from tests.billing.test_customer_on_won import win
from tests.helpers import headers as H


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def get_customer_id(eng, did):
    return (await rows(eng, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did))[0].id


async def make_pack(
    engine, world, customer_id, sub_id, size="1000", price=150000, purchased="2026-01-01"
):
    pack_id = uuid.uuid4()
    purchased_date = date.fromisoformat(purchased) if isinstance(purchased, str) else purchased
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO minute_packs (id, org_id, customer_id, subscription_id, "
                "size_minutes, price_paise, purchased_on) VALUES (:p,:o,:c,:s,:m,:pr,:d)"
            ),
            {
                "p": pack_id,
                "o": world.org_id,
                "c": customer_id,
                "s": sub_id,
                "m": size,
                "pr": price,
                "d": purchased_date,
            },
        )
    return pack_id


async def get_sub_id(engine, cust):
    return (await rows(engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust))[0].id


async def test_usage_draws_oldest_non_expired_pack_first_and_splits_across_packs(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub = await get_sub_id(admin_engine, cust)
    old_pack = await make_pack(admin_engine, world, cust, sub, size="100", purchased="2026-01-01")
    new_pack = await make_pack(admin_engine, world, cust, sub, size="100", purchased="2026-01-15")

    async with get_sessionmaker()() as s, s.begin():
        await draw(s, cust, None, Decimal("150"))  # bigger than the old pack alone

    old_ledger = await rows(
        admin_engine, "SELECT minutes FROM minute_ledger WHERE minute_pack_id=:p", p=old_pack
    )
    new_ledger = await rows(
        admin_engine, "SELECT minutes FROM minute_ledger WHERE minute_pack_id=:p", p=new_pack
    )
    assert sum(r.minutes for r in old_ledger) == 100  # fully drained first
    assert sum(r.minutes for r in new_ledger) == 50  # the rest from the newer pack

    async with get_sessionmaker()() as s:
        out = await list_packs(s, world.admin, cust)
    assert out["balance_minutes"] == Decimal("50")


async def test_usage_with_no_pack_or_expired_pack_becomes_unbilled_overage(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)

    async with get_sessionmaker()() as s, s.begin():
        await draw(s, cust, None, Decimal("42"))  # no pack at all

    overage = await rows(
        admin_engine,
        "SELECT minutes, reason, status FROM overage_items WHERE customer_id=:c",
        c=cust,
    )
    assert len(overage) == 1
    assert (
        overage[0].minutes == 42
        and overage[0].reason == "no_pack"
        and overage[0].status == "unbilled"
    )


async def test_balance_is_always_the_sum_of_ledger_rows(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub = await get_sub_id(admin_engine, cust)
    pack = await make_pack(admin_engine, world, cust, sub, size="1000")

    async with get_sessionmaker()() as s, s.begin():
        await draw(s, cust, None, Decimal("123.456"))
    ledger_sum = await rows(
        admin_engine,
        "SELECT COALESCE(SUM(minutes),0) AS s FROM minute_ledger WHERE minute_pack_id=:p",
        p=pack,
    )
    async with get_sessionmaker()() as s:
        out = await list_packs(s, world.admin, cust)
    assert out["packs"][0]["balance_minutes"] == Decimal("1000") - ledger_sum[0].s


async def test_manual_pack_add_is_admin_and_sa_only_with_reason(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)

    def add(actor, reason="topped up"):
        return client.post(
            f"/customers/{cust}/packs",
            json={
                "size_minutes": "500",
                "price_paise": 75000,
                "purchased_on": "2026-02-01",
                "reason": reason,
            },
            headers=H(actor),
        )

    assert (await add(world.tl_north)).status_code == 403
    assert (await add(world.north1)).status_code == 403
    assert (await add(world.admin, reason=None)).status_code == 422
    r = await add(world.admin)
    assert r.status_code == 201, r.text
    assert r.json()["size_minutes"] == "500"


async def test_usage_sync_inserts_once_and_a_correction_adjusts_by_the_difference(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub = await get_sub_id(admin_engine, cust)
    await make_pack(admin_engine, world, cust, sub, size="1000")
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE customers SET voice_platform_customer_id=:v WHERE id=:c"),
            {"v": "voice-1", "c": cust},
        )

    occurred = datetime(2026, 2, 1, tzinfo=UTC)
    page1 = Page(
        records=[UsageRow("rec-1", "voice-1", occurred, Decimal("10.000"), revision=1)],
        next_cursor="1",
    )

    calls = {"n": 0}

    async def fetch(since, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            return page1
        if calls["n"] == 2:  # the SAME record, same revision: must be a no-op
            return Page(records=page1.records, next_cursor="1")
        if calls["n"] == 3:  # a correction: revision 2, fewer minutes
            return Page(
                records=[UsageRow("rec-1", "voice-1", occurred, Decimal("7.500"), revision=2)],
                next_cursor="2",
            )
        return Page(records=[], next_cursor=since)

    r1 = await run_sync(world.org_id, get_sessionmaker(), fetch)
    assert r1["pulled"] == 1
    r2 = await run_sync(world.org_id, get_sessionmaker(), fetch)
    assert r2["pulled"] == 1  # the page is re-fetched, but the record is a no-op

    async with get_sessionmaker()() as s:
        stored = (
            (await s.execute(select(UsageRecord).where(UsageRecord.customer_id == cust)))
            .scalars()
            .all()
        )
    assert len(stored) == 1  # inserted exactly once despite two pulls of the same record
    assert stored[0].minutes == Decimal("10.000")

    r3 = await run_sync(world.org_id, get_sessionmaker(), fetch)
    assert r3["pulled"] == 1

    async with get_sessionmaker()() as s:
        stored2 = (
            (await s.execute(select(UsageRecord).where(UsageRecord.customer_id == cust)))
            .scalars()
            .all()
        )
    assert len(stored2) == 1
    assert stored2[0].minutes == Decimal("7.500") and stored2[0].revision == 2

    ledger = await rows(
        admin_engine,
        "SELECT minutes, kind FROM minute_ledger WHERE customer_id=:c ORDER BY created_at",
        c=cust,
    )
    assert [(r.minutes, r.kind) for r in ledger] == [
        (Decimal("10.000"), "draw"),
        (Decimal("-2.500"), "correction"),
    ]


async def test_unmatched_usage_is_queued_not_dropped(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)

    async def fetch(since, limit):
        if since:
            return Page(records=[], next_cursor=since)
        return Page(
            records=[
                UsageRow("u-1", "no-such-customer", datetime(2026, 2, 1, tzinfo=UTC), Decimal("5"))
            ],
            next_cursor="1",
        )

    await run_sync(world.org_id, get_sessionmaker(), fetch)
    r = await client.get("/usage/unmatched", headers=H(world.admin))
    assert r.status_code == 200
    assert any(u["source_record_id"] == "u-1" for u in r.json())
    assert (await client.get("/usage/unmatched", headers=H(world.north1))).status_code == 403


async def test_voice_mapping_is_admin_and_sa_only(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    assert (
        await client.patch(
            f"/customers/{cust}/voice-mapping",
            json={"voice_platform_customer_id": "v-9", "reason": "mapped"},
            headers=H(world.tl_north),
        )
    ).status_code == 403
    r = await client.patch(
        f"/customers/{cust}/voice-mapping",
        json={"voice_platform_customer_id": "v-9", "reason": "mapped"},
        headers=H(world.admin),
    )
    assert r.status_code == 200
    assert r.json()["voice_platform_customer_id"] == "v-9"
