# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 4 PR-1: GST setting, catalog extension and the money constraints in the schema."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.helpers import headers as H

DB_ERRORS = (IntegrityError, DBAPIError)
KEY = "catalog.template.manage"


async def audit_rows(engine, org_id, entity_type):
    async with engine.connect() as c:
        rows = await c.execute(
            text(
                "SELECT old_value AS old, new_value AS new, reason FROM audit_log WHERE org_id = :o AND action_key = :k "
                "AND entity_type = :t AND outcome = 'ok'"
            ),
            {"o": org_id, "k": KEY, "t": entity_type},
        )
        return rows.all()


# ----------------------------------------------------------------------------- GST setting
async def test_gst_defaults_to_18_percent_for_everyone_who_can_see_customers(client, world):
    for actor in (world.north1, world.tl_north, world.admin, world.sa):
        r = await client.get("/settings/gst", headers=H(actor))
        assert r.status_code == 200, actor.role
        assert r.json() == {"gst_rate_bp": 1800, "gst_rate_percent": "18"}


async def test_gst_change_roles_reason_and_one_audit_row(client, world, admin_engine):
    def put(actor, bp=1250, reason="rate change"):
        return client.put(
            "/settings/gst", json={"gst_rate_bp": bp, "reason": reason}, headers=H(actor)
        )

    assert (await put(world.north1)).status_code == 403
    assert (await put(world.tl_north)).status_code == 403
    assert (await put(world.admin, reason=None)).status_code == 422  # Admin needs a reason
    assert (await put(world.admin, bp=10001)).status_code == 422
    assert (await put(world.admin, bp=-1)).status_code == 422
    assert await audit_rows(admin_engine, world.org_id, "billing_settings") == []

    r = await put(world.admin)
    assert r.status_code == 200
    assert r.json() == {"gst_rate_bp": 1250, "gst_rate_percent": "12.5"}  # exact, no float
    rows = await audit_rows(admin_engine, world.org_id, "billing_settings")
    assert len(rows) == 1
    assert rows[0].old == {"gst_rate_bp": 1800}
    assert rows[0].new == {"gst_rate_bp": 1250}
    assert rows[0].reason == "rate change"

    assert (await put(world.sa, bp=0, reason=None)).status_code == 200  # Super admin: no reason
    assert (await client.get("/settings/gst", headers=H(world.north1))).json()["gst_rate_bp"] == 0


# ----------------------------------------------------------------------------- catalog extension
BASE = {
    "name": "Voice agent",
    "unit": "per_minute",
    "list_price_paise": 150,
    "floor_price_paise": 100,
}


def put_products(client, actor, items, reason="qa"):
    return client.put("/products", json={"products": items, "reason": reason}, headers=H(actor))


async def test_per_minute_product_with_packs_and_fees(client, world, admin_engine):
    item = {
        **BASE,
        "pricing_model": "per_minute",
        "pack_options": [
            {"minutes": 5000, "price_paise": 650000},
            {"minutes": 1000, "price_paise": 140000},
        ],
        "allowed_contract_terms": ["quarterly", "yearly"],
        "allowed_billing_frequencies": ["quarterly"],
        "one_time_fees": [{"name": "Setup", "amount_paise": 500000}],
    }
    r = await put_products(client, world.admin, [item])
    assert r.status_code == 200, r.text
    p = r.json()[0]
    assert [x["minutes"] for x in p["pack_options"]] == [1000, 5000]  # stored in size order
    assert p["allowed_contract_terms"] == ["quarterly", "yearly"]
    assert p["one_time_fees"] == [{"name": "Setup", "amount_paise": 500000}]
    assert p["subscription_fees"] == {}
    rows = await audit_rows(admin_engine, world.org_id, "product")
    assert len(rows) == 1 and rows[0].new["pack_options"][0]["minutes"] == 1000


async def test_saas_product_and_validation(client, world):
    saas = {
        "name": "Relay SaaS",
        "unit": "fixed",
        "list_price_paise": 0,
        "floor_price_paise": 0,
        "pricing_model": "saas",
        "subscription_fees": {"monthly": 4000000, "quarterly": 11000000},
        "allowed_billing_frequencies": ["monthly", "quarterly"],
    }
    assert (await put_products(client, world.admin, [saas])).status_code == 200

    def bad(**kw):
        return put_products(client, world.admin, [{**saas, "name": "X", **kw}])

    assert (await bad(pack_options=[{"minutes": 100, "price_paise": 1}])).status_code == 422
    assert (await bad(subscription_fees={"monthly": 1, "weekly": 2})).status_code == 422
    assert (await bad(subscription_fees={"monthly": -1})).status_code == 422
    assert (await bad(allowed_billing_frequencies=["yearly"])).status_code == 422  # no yearly fee
    assert (await bad(allowed_contract_terms=[])).status_code == 422
    per_min = {**BASE, "name": "Y"}
    with_fee = {**per_min, "subscription_fees": {"monthly": 1}}
    assert (await put_products(client, world.admin, [with_fee])).status_code == 422
    dup = {**per_min, "pack_options": [{"minutes": 10, "price_paise": 1}] * 2}
    assert (await put_products(client, world.admin, [dup])).status_code == 422
    # only Admin (with reason) and Super admin may edit the catalog
    assert (await put_products(client, world.tl_north, [saas])).status_code == 403
    assert (await put_products(client, world.north1, [saas])).status_code == 403
    assert (await put_products(client, world.admin, [saas], reason=None)).status_code == 422


async def test_older_client_save_does_not_wipe_pricing_terms(client, world):
    item = {
        **BASE,
        "name": "Keep me",
        "pack_options": [{"minutes": 1000, "price_paise": 140000}],
        "one_time_fees": [{"name": "Setup", "amount_paise": 1}],
    }
    pid = (await put_products(client, world.admin, [item])).json()[0]["id"]
    older = {**BASE, "id": pid, "name": "Keep me", "list_price_paise": 160}
    p = (await put_products(client, world.admin, [older])).json()[0]
    assert p["list_price_paise"] == 160
    assert p["pack_options"] == [{"minutes": 1000, "price_paise": 140000}]
    assert p["one_time_fees"] == [{"name": "Setup", "amount_paise": 1}]


# ----------------------------------------------------------------------------- schema constraints
async def _insert_generic(conn, table, values):
    """Insert `values`, filling any other NOT NULL column that has no default with a dummy."""
    cols = (
        await conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns WHERE "
                "table_name = :t AND is_nullable = 'NO' AND column_default IS NULL"
            ),
            {"t": table},
        )
    ).all()
    row = dict(values)
    for name, dtype in cols:
        if name not in row:
            row[name] = 1 if dtype in ("integer", "bigint") else "x"
    names = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    await conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({marks})"), row)


async def seed_customer(engine, world, *, voice_id=None):
    """Account, deal and customer rows made directly (the service that does it is PR-2)."""
    acc, deal, cust = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    o, u = world.org_id, world.north1.id
    async with engine.begin() as c:
        await _insert_generic(
            c, "accounts", {"id": acc, "org_id": o, "name": f"Acme {acc.hex[:6]}"}
        )
        await _insert_generic(
            c,
            "deals",
            {
                "id": deal,
                "org_id": o,
                "account_id": acc,
                "owner_id": u,
                "created_by": u,
                "lead_source": "Website",
            },
        )
        await c.execute(
            text(
                "INSERT INTO customers (id, org_id, account_id, account_owner_id, "
                "first_won_deal_id, voice_platform_customer_id) VALUES (:c,:o,:a,:u,:d,:v)"
            ),
            {"c": cust, "o": o, "a": acc, "u": u, "d": deal, "v": voice_id},
        )
    return cust, deal


async def seed_file(engine, world):
    fid = uuid.uuid4()
    async with engine.begin() as c:
        await _insert_generic(
            c,
            "files",
            {
                "id": fid,
                "org_id": world.org_id,
                "uploaded_by": world.admin.id,
                "key": f"proof-{fid}",
            },
        )
    return fid


async def seed_invoice(engine, world, cust, number, ex=100000, gst=18000):
    iid = uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO invoices (id, org_id, customer_id, number, issue_date, due_date, "
                "amount_ex_gst_paise, gst_paise, total_paise) VALUES "
                "(:i,:o,:c,:n,'2026-01-01','2026-01-01',:e,:g,:t)"
            ),
            {"i": iid, "o": world.org_id, "c": cust, "n": number, "e": ex, "g": gst, "t": ex + gst},
        )
    return iid


async def seed_payment(engine, world, cust, amount, proof):
    pid = uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO payments (id, org_id, customer_id, amount_paise, received_on, method, "
                "proof_file_id, recorded_by) VALUES (:p,:o,:c,:a,'2026-01-02','UPI',:f,:u)"
            ),
            {"p": pid, "o": world.org_id, "c": cust, "a": amount, "f": proof, "u": world.admin.id},
        )
    return pid


async def allocate(engine, world, pay, inv, amount):
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO payment_allocations (org_id, payment_id, invoice_id, amount_paise) "
                "VALUES (:o,:p,:i,:a)"
            ),
            {"o": world.org_id, "p": pay, "i": inv, "a": amount},
        )


async def test_allocations_can_never_exceed_payment_or_invoice(admin_engine, world):
    cust, _ = await seed_customer(admin_engine, world)
    proof = await seed_file(admin_engine, world)
    inv = await seed_invoice(admin_engine, world, cust, "INV-1")  # total 118000
    inv2 = await seed_invoice(admin_engine, world, cust, "INV-2")
    pay = await seed_payment(admin_engine, world, cust, 150000, proof)

    await allocate(admin_engine, world, pay, inv, 100000)  # a part payment of the invoice
    with pytest.raises(DB_ERRORS):
        await allocate(admin_engine, world, pay, inv, 18001)  # one paisa over the invoice total
    with pytest.raises(DB_ERRORS):
        await allocate(admin_engine, world, pay, inv2, 50001)  # one paisa over the payment
    await allocate(admin_engine, world, pay, inv, 18000)  # exactly the rest of the invoice
    await allocate(admin_engine, world, pay, inv2, 32000)  # exactly the rest of the payment
    with pytest.raises(DB_ERRORS):
        await allocate(admin_engine, world, pay, inv2, 1)


async def test_allocation_across_customers_is_refused(admin_engine, world):
    a, _ = await seed_customer(admin_engine, world)
    b, _ = await seed_customer(admin_engine, world)
    proof = await seed_file(admin_engine, world)
    inv = await seed_invoice(admin_engine, world, b, "INV-X")
    pay = await seed_payment(admin_engine, world, a, 1000, proof)
    with pytest.raises(DB_ERRORS):
        await allocate(admin_engine, world, pay, inv, 1000)


async def test_invoice_total_cannot_drop_below_allocated(admin_engine, world):
    cust, _ = await seed_customer(admin_engine, world)
    proof = await seed_file(admin_engine, world)
    inv = await seed_invoice(admin_engine, world, cust, "INV-E")
    pay = await seed_payment(admin_engine, world, cust, 100000, proof)
    await allocate(admin_engine, world, pay, inv, 100000)
    with pytest.raises(DB_ERRORS):
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE invoices SET amount_ex_gst_paise = 50000, gst_paise = 9000, "
                    "total_paise = 59000 WHERE id = :i"
                ),
                {"i": inv},
            )


BAD_ROWS = [
    # GST must add up: total = ex-GST + GST, to the paisa
    "INSERT INTO invoices (org_id, customer_id, number, issue_date, due_date, "
    "amount_ex_gst_paise, gst_paise, total_paise) "
    "VALUES (:o,:c,'BAD-1','2026-01-01','2026-01-01',100,18,119)",
    # negative money
    "INSERT INTO invoices (org_id, customer_id, number, issue_date, due_date, "
    "amount_ex_gst_paise, gst_paise, total_paise) "
    "VALUES (:o,:c,'BAD-2','2026-01-01','2026-01-01',-1,0,-1)",
    # a payment must carry proof
    "INSERT INTO payments (org_id, customer_id, amount_paise, received_on, method, recorded_by) "
    "VALUES (:o,:c,100,'2026-01-01','UPI',:u)",
    # a receipt cannot be negative
    "INSERT INTO payments (org_id, customer_id, amount_paise, received_on, method, "
    "proof_file_id, recorded_by) VALUES (:o,:c,-100,'2026-01-01','UPI',:f,:u)",
    # a correction needs the payment it reverses and a reason
    "INSERT INTO payments (org_id, customer_id, kind, amount_paise, received_on, method, "
    "proof_file_id, recorded_by) VALUES (:o,:c,'correction',-100,'2026-01-01','UPI',:f,:u)",
    # ledger: a draw is positive, a correction is never zero
    "INSERT INTO minute_ledger (org_id, customer_id, minutes, kind) VALUES (:o,:c,-1,'draw')",
    "INSERT INTO minute_ledger (org_id, customer_id, minutes, kind) VALUES (:o,:c,0,'correction')",
]


@pytest.mark.parametrize("sql", BAD_ROWS)
async def test_constraints_reject_bad_money_rows(admin_engine, world, sql):
    cust, _ = await seed_customer(admin_engine, world)
    proof = await seed_file(admin_engine, world)
    with pytest.raises(DB_ERRORS):
        async with admin_engine.begin() as c:
            await c.execute(
                text(sql), {"o": world.org_id, "c": cust, "f": proof, "u": world.admin.id}
            )


async def test_usage_source_id_unique_per_org_and_minutes_keep_3_places(admin_engine, world):
    cust, _ = await seed_customer(admin_engine, world)
    ins = (
        "INSERT INTO usage_records (org_id, customer_id, source_record_id, occurred_at, minutes) "
        "VALUES (:o,:c,:s,now(),:m)"
    )
    async with admin_engine.begin() as c:
        await c.execute(text(ins), {"o": world.org_id, "c": cust, "s": "r1", "m": "12.345"})
        got = (
            await c.execute(text("SELECT minutes FROM usage_records WHERE source_record_id='r1'"))
        ).scalar()
    assert str(got) == "12.345"
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as c:
            await c.execute(text(ins), {"o": world.org_id, "c": cust, "s": "r1", "m": "1"})


async def test_one_customer_per_account_one_subscription_per_deal(admin_engine, world):
    cust, deal = await seed_customer(admin_engine, world)
    async with admin_engine.connect() as c:
        acc = (
            await c.execute(text("SELECT account_id FROM customers WHERE id=:c"), {"c": cust})
        ).scalar()
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO customers (org_id, account_id, account_owner_id, "
                    "first_won_deal_id) VALUES (:o,:a,:u,:d)"
                ),
                {"o": world.org_id, "a": acc, "u": world.north1.id, "d": deal},
            )
    sub = (
        "INSERT INTO subscriptions (org_id, customer_id, source_deal_id, pricing_model) "
        "VALUES (:o,:c,:d,'per_minute')"
    )
    async with admin_engine.begin() as c:
        await c.execute(text(sub), {"o": world.org_id, "c": cust, "d": deal})
    with pytest.raises(IntegrityError):  # re-winning the same deal never makes a second
        async with admin_engine.begin() as c:
            await c.execute(text(sub), {"o": world.org_id, "c": cust, "d": deal})


async def test_money_history_is_append_only_for_the_app_role(database, admin_engine, world):
    """relay_app (the API's role) cannot rewrite payments, allocations or ledger rows."""
    cust, _ = await seed_customer(admin_engine, world)
    proof = await seed_file(admin_engine, world)
    pay = await seed_payment(admin_engine, world, cust, 500, proof)
    app_engine = create_async_engine(database.app_url)
    try:
        for stmt in (
            "UPDATE payments SET amount_paise = 1 WHERE id = :p",
            "DELETE FROM payments WHERE id = :p",
            "UPDATE minute_ledger SET minutes = 1",
            "DELETE FROM payment_allocations",
        ):
            with pytest.raises(DBAPIError):
                async with app_engine.begin() as c:
                    await c.execute(text(stmt), {"p": pay} if ":p" in stmt else {})
    finally:
        await app_engine.dispose()
