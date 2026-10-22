# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-09/P4-10: alerts fire once per (customer, type, ref, date) to the right people."""

from datetime import date, timedelta

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.billing.alerts import (
    AWAITING_CONFIRMATION,
    LOW_BALANCE,
    PAYMENT_DUE,
    PAYMENT_OVERDUE,
    RUN_OUT,
    check_awaiting_confirmation,
    check_balances,
    check_overage,
    check_payment_due_and_overdue,
)
from app.modules.billing.packs import draw
from tests.billing.test_customer_on_won import win
from tests.billing.test_invoices import invoice_body
from tests.helpers import headers as H
from tests.helpers import notifications_for


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def get_customer_id(eng, did):
    return (await rows(eng, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did))[0].id


async def get_sub_id(engine, cust):
    return (await rows(engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust))[0].id


async def make_pack(engine, world, customer_id, sub_id, size="1000"):
    import uuid

    pid = uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO minute_packs (id, org_id, customer_id, subscription_id, "
                "size_minutes, price_paise, purchased_on) VALUES (:p,:o,:c,:s,:m,1,'2026-01-01')"
            ),
            {"p": pid, "o": world.org_id, "c": customer_id, "s": sub_id, "m": size},
        )
    return pid


async def test_low_balance_and_run_out_fire_once_to_owner_and_tl(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub = await get_sub_id(admin_engine, cust)
    await make_pack(admin_engine, world, cust, sub, size="100")

    today = date(2026, 3, 1)
    async with get_sessionmaker()() as s, s.begin():
        await draw(s, cust, None, __import__("decimal").Decimal("81"), today=today)  # 19% left

    async with get_sessionmaker()() as s, s.begin():
        n = await check_balances(s, world.org_id, today)
    assert n == 1
    owner_notifs = await notifications_for(world.north1.id)
    assert any(x.type == LOW_BALANCE for x in owner_notifs)
    tl_notifs = await notifications_for(world.tl_north.id)
    assert any(x.type == LOW_BALANCE for x in tl_notifs)

    # same day again: no duplicate
    async with get_sessionmaker()() as s, s.begin():
        n2 = await check_balances(s, world.org_id, today)
    assert n2 == 0
    assert len([x for x in await notifications_for(world.north1.id) if x.type == LOW_BALANCE]) == 1

    async with get_sessionmaker()() as s, s.begin():
        from decimal import Decimal

        await draw(s, cust, None, Decimal("19"), today=today)  # balance now 0
        n3 = await check_balances(s, world.org_id, today)
    assert n3 == 1
    assert any(x.type == RUN_OUT for x in await notifications_for(world.north1.id))
    admin_notifs = await notifications_for(world.admin.id)
    assert any("churn" in x.type for x in admin_notifs)


async def test_overage_alerts_admins_once(client, world, admin_engine):
    from decimal import Decimal

    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    async with get_sessionmaker()() as s, s.begin():
        await draw(s, cust, None, Decimal("10"))  # no pack at all: pure overage

    today = date.today()
    async with get_sessionmaker()() as s, s.begin():
        n = await check_overage(s, world.org_id, today)
    assert n == 1
    assert any(x.type == "billing.overage" for x in await notifications_for(world.admin.id))
    async with get_sessionmaker()() as s, s.begin():
        n2 = await check_overage(s, world.org_id, today)
    assert n2 == 0


async def test_payment_due_14_days_before_on_the_day_and_overdue(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    due = date(2026, 3, 15)
    await client.post(
        f"/customers/{cust}/invoices",
        json=invoice_body(
            issue_date="2026-02-01",
            due_date=due.isoformat(),
            amount_ex_gst_paise=100000,
            gst_paise=18000,
        ),
        headers=H(world.admin),
    )

    async with get_sessionmaker()() as s, s.begin():
        n = await check_payment_due_and_overdue(s, world.org_id, due - timedelta(days=14))
    assert n == 1
    assert any(x.type == PAYMENT_DUE for x in await notifications_for(world.north1.id))

    async with get_sessionmaker()() as s, s.begin():
        n2 = await check_payment_due_and_overdue(s, world.org_id, due)
    assert n2 == 1  # a distinct day fires again

    async with get_sessionmaker()() as s, s.begin():
        n3 = await check_payment_due_and_overdue(s, world.org_id, due + timedelta(days=1))
    assert n3 == 1
    owner_types = [x.type for x in await notifications_for(world.north1.id)]
    assert owner_types.count(PAYMENT_OVERDUE) == 1
    assert any("churn" in x.type for x in await notifications_for(world.admin.id))


async def test_awaiting_confirmation_daily_to_tl_and_admins(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    today = date.today()
    async with get_sessionmaker()() as s, s.begin():
        n = await check_awaiting_confirmation(s, world.org_id, today)
    assert n == 1
    assert any(x.type == AWAITING_CONFIRMATION for x in await notifications_for(world.tl_north.id))
    assert any(x.type == AWAITING_CONFIRMATION for x in await notifications_for(world.admin.id))

    async with get_sessionmaker()() as s, s.begin():
        n2 = await check_awaiting_confirmation(s, world.org_id, today)
    assert n2 == 0  # once per day
