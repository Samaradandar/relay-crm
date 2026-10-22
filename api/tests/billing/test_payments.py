# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-06: payments, allocation, derived invoice status, paid-through, reversal, Paid/Due/Overdue."""

import uuid

from sqlalchemy import text

from tests.billing.test_customer_on_won import win
from tests.billing.test_invoices import invoice_body
from tests.helpers import headers as H


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def get_customer_id(eng, did):
    return (await rows(eng, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did))[0].id


async def make_file(engine, world):
    fid = uuid.uuid4()
    async with engine.begin() as c:
        cols = (
            await c.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE "
                    "table_name='files' AND is_nullable='NO' AND column_default IS NULL"
                )
            )
        ).all()
        values = {
            "id": fid,
            "org_id": world.org_id,
            "uploaded_by": world.admin.id,
            "key": f"k-{fid}",
        }
        for name, dtype in cols:
            if name not in values:
                values[name] = 1 if dtype in ("integer", "bigint") else "x"
        await c.execute(
            text(
                f"INSERT INTO files ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"
            ),
            values,
        )
    return fid


async def test_partial_then_full_payment_marks_invoice_part_paid_then_paid(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices",
            json=invoice_body(amount_ex_gst_paise=100000, gst_paise=18000),
            headers=H(world.admin),
        )
    ).json()
    proof = await make_file(admin_engine, world)

    def pay(amount, actor=None):
        return client.post(
            f"/customers/{cust}/payments",
            json={
                "amount_paise": amount,
                "received_on": "2026-02-05",
                "method": "UPI",
                "proof_file_id": str(proof),
                "reason": "received",
            },
            headers=H(actor or world.admin),
        )

    assert (await pay(50000, world.north1)).status_code == 403  # Users view only

    r1 = await pay(50000)
    assert r1.status_code == 201, r1.text
    assert r1.json()["unallocated_paise"] == 0
    after1 = (await rows(admin_engine, "SELECT status FROM invoices WHERE id=:i", i=inv["id"]))[0]
    assert after1.status == "part_paid"

    r2 = await pay(68000)
    assert r2.status_code == 201
    after2 = (await rows(admin_engine, "SELECT status FROM invoices WHERE id=:i", i=inv["id"]))[0]
    assert after2.status == "paid"

    total_alloc = await rows(
        admin_engine,
        "SELECT COALESCE(SUM(amount_paise),0) AS s FROM payment_allocations WHERE invoice_id=:i",
        i=inv["id"],
    )
    assert total_alloc[0].s == 118000  # never more than the invoice total


async def test_unallocated_remainder_shows_as_credit(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    await client.post(
        f"/customers/{cust}/invoices",
        json=invoice_body(amount_ex_gst_paise=100000, gst_paise=18000),
        headers=H(world.admin),
    )
    proof = await make_file(admin_engine, world)
    r = await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 200000,
            "received_on": "2026-02-05",
            "method": "UPI",
            "proof_file_id": str(proof),
            "reason": "overpaid",
        },
        headers=H(world.admin),
    )
    assert r.status_code == 201
    assert r.json()["unallocated_paise"] == 200000 - 118000


async def test_explicit_allocations_cannot_exceed_invoice_or_payment(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices",
            json=invoice_body(amount_ex_gst_paise=100000, gst_paise=18000),
            headers=H(world.admin),
        )
    ).json()
    proof = await make_file(admin_engine, world)
    r = await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 50000,
            "received_on": "2026-02-05",
            "method": "UPI",
            "proof_file_id": str(proof),
            "reason": "x",
            "allocations": [{"invoice_id": inv["id"], "amount_paise": 118001}],
        },
        headers=H(world.admin),
    )
    assert r.status_code in (422, 500, 400)  # the DB trigger refuses an over-allocation


async def test_reversal_restores_invoice_status_and_keeps_history(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices",
            json=invoice_body(amount_ex_gst_paise=100000, gst_paise=18000),
            headers=H(world.admin),
        )
    ).json()
    proof = await make_file(admin_engine, world)
    pay = (
        await client.post(
            f"/customers/{cust}/payments",
            json={
                "amount_paise": 118000,
                "received_on": "2026-02-05",
                "method": "UPI",
                "proof_file_id": str(proof),
                "reason": "full",
            },
            headers=H(world.admin),
        )
    ).json()
    assert (await rows(admin_engine, "SELECT status FROM invoices WHERE id=:i", i=inv["id"]))[
        0
    ].status == "paid"

    assert (
        await client.post(
            f"/payments/{pay['id']}/reverse",
            json={"reason": "wrong account"},
            headers=H(world.tl_north),
        )
    ).status_code == 403  # Admin / SA only

    rev = await client.post(
        f"/payments/{pay['id']}/reverse", json={"reason": "wrong account"}, headers=H(world.admin)
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["kind"] == "correction" and rev.json()["amount_paise"] == -118000
    assert (await rows(admin_engine, "SELECT status FROM invoices WHERE id=:i", i=inv["id"]))[
        0
    ].status == "open"
    original = await rows(
        admin_engine, "SELECT amount_paise FROM payments WHERE id=:p", p=pay["id"]
    )
    assert original[0].amount_paise == 118000  # the original row is untouched


async def test_payment_status_paid_due_overdue(client, world, admin_engine):
    from app.core.db import get_sessionmaker
    from app.modules.billing.payments import payment_status

    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)

    async def status() -> str:
        async with get_sessionmaker()() as s:
            return await payment_status(s, cust)

    assert await status() == "paid"  # no invoices at all

    inv = (
        await client.post(
            f"/customers/{cust}/invoices",
            json=invoice_body(
                issue_date="2099-01-01",
                due_date="2099-01-01",
                amount_ex_gst_paise=100000,
                gst_paise=18000,
            ),
            headers=H(world.admin),
        )
    ).json()
    assert await status() == "paid"  # far-future due date, no invoice due soon

    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE invoices SET due_date = CURRENT_DATE + 7 WHERE id = :i"), {"i": inv["id"]}
        )
    assert await status() == "due"  # within the 14-day window

    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE invoices SET due_date = CURRENT_DATE - 1 WHERE id = :i"), {"i": inv["id"]}
        )
    assert await status() == "overdue"

    proof = await make_file(admin_engine, world)
    await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 118000,
            "received_on": "2026-02-05",
            "method": "UPI",
            "proof_file_id": str(proof),
            "reason": "paid in full",
        },
        headers=H(world.admin),
    )
    assert await status() == "paid"


async def test_customer_payments_received_matches_allocations_exactly(client, world, admin_engine):
    from app.core.db import get_sessionmaker
    from app.modules.billing.payments import customer_payments_received

    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    await client.post(
        f"/customers/{cust}/invoices",
        json=invoice_body(amount_ex_gst_paise=100000, gst_paise=18000),
        headers=H(world.admin),
    )
    proof = await make_file(admin_engine, world)
    await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 70000,
            "received_on": "2026-02-05",
            "method": "UPI",
            "proof_file_id": str(proof),
            "reason": "part",
        },
        headers=H(world.admin),
    )
    async with get_sessionmaker()() as s:
        received = await customer_payments_received(s, did)
    assert received == 70000
