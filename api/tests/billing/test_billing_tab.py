# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-11: the customer billing tab aggregates subscription, packs, invoices, payments, revenue."""

from sqlalchemy import text

from tests.billing.test_customer_on_won import win
from tests.billing.test_invoices import invoice_body
from tests.billing.test_payments import make_file
from tests.helpers import headers as H


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def get_customer_id(eng, did):
    return (await rows(eng, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did))[0].id


async def test_billing_tab_aggregates_everything_and_revenue_toggles_gst(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub_id = (
        await rows(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust)
    )[0].id
    await client.post(
        f"/subscriptions/{sub_id}/confirm",
        json={
            "billing_frequency": "monthly",
            "term_type": "plan",
            "contract_term": "monthly",
            "start_date": "2026-02-01",
            "pricing_model": "per_minute",
            "pack_price_paise": 150000,
            "pack_size_minutes": "1000",
            "reason": "confirm",
        },
        headers=H(world.admin),
    )
    await client.post(
        f"/customers/{cust}/invoices",
        json=invoice_body(amount_ex_gst_paise=100000, gst_paise=18000),
        headers=H(world.admin),
    )
    proof = await make_file(admin_engine, world)
    await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 60000,
            "received_on": "2026-02-05",
            "method": "UPI",
            "proof_file_id": str(proof),
            "reason": "part",
        },
        headers=H(world.admin),
    )

    r = await client.get(f"/customers/{cust}/billing", headers=H(world.north1))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["subscription"]["status"] == "confirmed"
    assert len(out["subscription_versions"]) == 2  # drafted, then confirmed
    assert len(out["invoices"]) == 1
    assert len(out["payments"]) == 1
    assert out["payment_status"] in ("due", "overdue", "paid")
    assert out["revenue_ex_gst_paise"] == 100000
    assert out["revenue_paise"] == 100000  # default: ex-GST

    r2 = await client.get(
        f"/customers/{cust}/billing", params={"gst_inclusive": True}, headers=H(world.north1)
    )
    out2 = r2.json()
    assert out2["revenue_paise"] == 118000
    assert out2["revenue_ex_gst_paise"] + out2["revenue_gst_paise"] == out2["revenue_paise"]


async def test_billing_tab_follows_customer_scope(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    assert (
        await client.get(f"/customers/{cust}/billing", headers=H(world.south1))
    ).status_code == 404
    assert (
        await client.get(f"/customers/{cust}/billing", headers=H(world.tl_north))
    ).status_code == 200
    assert (
        await client.get(f"/customers/{cust}/billing", headers=H(world.admin))
    ).status_code == 200
