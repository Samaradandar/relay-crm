# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Deep adversarial tests for Phase 4 billing: concurrency races, cross-org isolation spot
checks, boundary values, and idempotency beyond the existing test_* files in tests/billing/.

Run: .\\.venv\\Scripts\\python.exe -m pytest tests/billing/deep -q -p no:cacheprovider --no-cov
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.billing.packs import draw
from app.modules.billing.subscriptions import update_contract
from app.platform.context import RequestInfo
from tests.billing.test_customer_on_won import win
from tests.billing.test_invoices import invoice_body
from tests.billing.test_packs_and_usage import get_sub_id, make_pack
from tests.helpers import headers as H

pytestmark = pytest.mark.asyncio


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
                f"INSERT INTO files ({', '.join(values)}) VALUES "
                f"({', '.join(':' + k for k in values)})"
            ),
            values,
        )
    return fid


# --------------------------------------------------------------------------- packs / concurrency
async def test_concurrent_draws_never_overdraw_a_pack(client, world, admin_engine):
    """Two draws of 60 minutes each against a single 100-minute pack, run concurrently in
    separate sessions/transactions (simulating two usage-sync workers), must never let the ledger
    sum exceed the pack size. BUG-P4-DEEP-01 if it does (lost-update race: both draws read the
    same stale balance before either commits)."""
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub = await get_sub_id(admin_engine, cust)
    pack = await make_pack(admin_engine, world, cust, sub, size="100", purchased="2026-01-01")

    async def one_draw():
        async with get_sessionmaker()() as s, s.begin():
            await draw(s, cust, None, Decimal("60"))

    await asyncio.gather(one_draw(), one_draw())

    ledger = await rows(
        admin_engine,
        "SELECT COALESCE(SUM(minutes),0) AS s FROM minute_ledger WHERE minute_pack_id=:p",
        p=pack,
    )
    drawn = ledger[0].s
    overage = await rows(
        admin_engine,
        "SELECT COALESCE(SUM(minutes),0) AS s FROM overage_items WHERE customer_id=:c",
        c=cust,
    )
    # invariant: whatever wasn't drawn from the pack must show up as overage - no minutes may be
    # silently lost, and the pack must never record more than its size.
    assert drawn <= 100, f"pack overdrawn: ledger sum {drawn} > pack size 100"
    assert drawn + overage[0].s == 120


# --------------------------------------------------------------------------- cross-org isolation
async def second_org(admin_engine):
    from app.modules.pipeline.seeds import seed_org_pipeline

    org_id = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id, name) VALUES (:i, 'Other org')"), {"i": org_id})
        await seed_org_pipeline(c, org_id)
    return org_id


async def test_update_contract_cannot_touch_a_customer_from_another_org(
    client, world, admin_engine
):
    """A confirmed subscription belongs to org A. An Admin from org B, given org A's customer_id
    (guessed/leaked UUID), must not be able to read or edit it via update_contract."""
    other_org = await second_org(admin_engine)
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)

    # Fabricate an Admin actor that belongs to `other_org` but reuse world.admin's id to exercise
    # update_contract directly (bypassing the HTTP layer's own org-from-token binding, the way an
    # application bug or a forged header further up the stack could).
    from app.platform.context import Actor
    from app.platform.permissions.roles import Role

    foreign_admin = Actor(
        id=uuid.uuid4(),
        org_id=other_org,
        role=Role.ADMIN.value,
        team_id=None,
        name="Foreign Admin",
        email="foreign@other.local",
    )
    async with admin_engine.begin() as c:
        await c.execute(
            text("INSERT INTO users (id, org_id, email, name, role) VALUES (:i,:o,:e,:n,:r)"),
            {
                "i": foreign_admin.id,
                "o": other_org,
                "e": foreign_admin.email,
                "n": foreign_admin.name,
                "r": Role.ADMIN.value,
            },
        )

    from app.core.errors import NotFound, OutOfScope, PermissionDenied

    with pytest.raises((NotFound, OutOfScope, PermissionDenied)):
        await update_contract(
            foreign_admin,
            RequestInfo(origin="test"),
            cust,
            {"fee_paise": 1, "reason": "cross-org probe"},
        )


async def test_add_payment_rejects_another_orgs_customer_id(client, world, admin_engine):
    """IDOR spot check on POST /customers/{id}/payments across orgs (task item 10)."""
    other_org = await second_org(admin_engine)
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    proof = await make_file(admin_engine, world)

    from app.platform.context import Actor
    from app.platform.permissions.roles import Role

    async with admin_engine.begin() as c:
        uid = uuid.uuid4()
        await c.execute(
            text("INSERT INTO users (id, org_id, email, name, role) VALUES (:i,:o,:e,:n,:r)"),
            {"i": uid, "o": other_org, "e": "x@other.local", "n": "X", "r": Role.ADMIN.value},
        )
    foreign_admin = Actor(
        id=uid,
        org_id=other_org,
        role=Role.ADMIN.value,
        team_id=None,
        name="X",
        email="x@other.local",
    )

    from app.core.errors import NotFound, OutOfScope, PermissionDenied
    from app.modules.billing.payments import add_payment

    with pytest.raises((NotFound, OutOfScope, PermissionDenied)):
        await add_payment(
            foreign_admin,
            RequestInfo(origin="test"),
            cust,
            {
                "amount_paise": 100000,
                "received_on": "2026-02-05",
                "method": "UPI",
                "proof_file_id": str(proof),
                "reason": "cross-org probe",
            },
        )


# --------------------------------------------------------------------------- GST boundary/audit
async def test_gst_rate_boundaries_rejected_outside_0_to_10000_bp(client, world):
    r = await client.put(
        "/settings/gst", json={"gst_rate_bp": -1, "reason": "x"}, headers=H(world.admin)
    )
    assert r.status_code == 422
    r = await client.put(
        "/settings/gst", json={"gst_rate_bp": 10001, "reason": "x"}, headers=H(world.admin)
    )
    assert r.status_code == 422
    r = await client.put(
        "/settings/gst", json={"gst_rate_bp": 10000, "reason": "x"}, headers=H(world.admin)
    )
    assert r.status_code == 200


async def test_gst_rate_change_does_not_touch_existing_invoice_amounts(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices",
            json=invoice_body(amount_ex_gst_paise=100000, gst_paise=18000),
            headers=H(world.admin),
        )
    ).json()
    r = await client.put(
        "/settings/gst",
        json={"gst_rate_bp": 500, "reason": "rate change mid-cycle"},
        headers=H(world.admin),
    )
    assert r.status_code == 200
    got = (await client.get(f"/customers/{cust}/invoices", headers=H(world.admin))).json()
    same = [i for i in got if i["id"] == inv["id"]][0]
    assert same["gst_paise"] == 18000  # untouched by the later rate change


# --------------------------------------------------------------------------- invoice numbering
async def test_duplicate_invoice_number_within_org_rejected(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    body = invoice_body(number="DUP-001")
    r1 = await client.post(f"/customers/{cust}/invoices", json=body, headers=H(world.admin))
    assert r1.status_code == 201
    body2 = invoice_body(number="DUP-001")
    r2 = await client.post(f"/customers/{cust}/invoices", json=body2, headers=H(world.admin))
    assert r2.status_code == 409


async def test_concurrent_invoice_creation_same_number_only_one_wins(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    body = invoice_body(number="RACE-001")

    async def post():
        return await client.post(f"/customers/{cust}/invoices", json=body, headers=H(world.admin))

    results = await asyncio.gather(post(), post())
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409]
    count = await rows(admin_engine, "SELECT count(*) AS n FROM invoices WHERE number='RACE-001'")
    assert count[0].n == 1


# --------------------------------------------------------------------------- payments
async def test_payment_allocations_in_cannot_double_allocate_same_invoice_past_its_total(
    client, world, admin_engine
):
    """Caller sends the same invoice id twice in `allocations`, each within the invoice total
    alone but together exceeding it. The DB trigger (payment_allocation_guard) must refuse the
    second allocation; the whole request must fail, not partially apply."""
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices",
            json=invoice_body(amount_ex_gst_paise=100000, gst_paise=0),
            headers=H(world.admin),
        )
    ).json()
    proof = await make_file(admin_engine, world)
    r = await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 150000,
            "received_on": "2026-02-05",
            "method": "UPI",
            "proof_file_id": str(proof),
            "reason": "double-alloc probe",
            "allocations": [
                {"invoice_id": inv["id"], "amount_paise": 80000},
                {"invoice_id": inv["id"], "amount_paise": 70000},
            ],
        },
        headers=H(world.admin),
    )
    assert r.status_code >= 400
    allocated = await rows(
        admin_engine,
        "SELECT COALESCE(SUM(amount_paise),0) AS s FROM payment_allocations WHERE invoice_id=:i",
        i=inv["id"],
    )
    assert allocated[0].s == 0  # the whole payment+allocations rolled back together


async def test_payment_cannot_be_recorded_against_a_voided_invoice_via_explicit_allocation(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices",
            json=invoice_body(amount_ex_gst_paise=100000, gst_paise=0),
            headers=H(world.admin),
        )
    ).json()
    v = await client.post(
        f"/invoices/{inv['id']}/void", json={"reason": "cancelled"}, headers=H(world.admin)
    )
    assert v.status_code == 200
    proof = await make_file(admin_engine, world)
    r = await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 50000,
            "received_on": "2026-02-05",
            "method": "UPI",
            "proof_file_id": str(proof),
            "reason": "pay a voided invoice",
            "allocations": [{"invoice_id": inv["id"], "amount_paise": 50000}],
        },
        headers=H(world.admin),
    )
    # A voided invoice must never receive money (BUG-P4-DEEP-01, fixed in payments.add_payment).
    assert r.status_code == 422, "allowed a payment to allocate against a voided invoice: " + r.text


# --------------------------------------------------------------------------- subscription confirm
async def test_confirming_an_already_confirmed_subscription_is_rejected(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub_id = (
        await rows(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust)
    )[0].id
    body = {
        "pricing_model": "saas",
        "fee_paise": 50000,
        "billing_frequency": "monthly",
        "term_type": "plan",
        "contract_term": "monthly",
        "start_date": "2026-02-01",
        "reason": "confirm",
    }
    r1 = await client.post(f"/subscriptions/{sub_id}/confirm", json=body, headers=H(world.admin))
    assert r1.status_code == 200
    r2 = await client.post(f"/subscriptions/{sub_id}/confirm", json=body, headers=H(world.admin))
    assert r2.status_code == 422, r2.text


async def test_confirm_missing_required_field_rejected_not_partially_applied(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub_id = (
        await rows(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust)
    )[0].id
    body = {
        "pricing_model": "saas",
        "billing_frequency": "monthly",
        "term_type": "plan",
        "contract_term": "monthly",
        "start_date": "2026-02-01",
        "reason": "missing fee",
        # fee_paise deliberately omitted - required for a saas subscription
    }
    r = await client.post(f"/subscriptions/{sub_id}/confirm", json=body, headers=H(world.admin))
    assert r.status_code == 422
    status = await rows(admin_engine, "SELECT status FROM subscriptions WHERE id=:s", s=sub_id)
    assert status[0].status == "draft"  # confirm must not half-apply fields then fail


async def test_user_role_cannot_confirm_subscription(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub_id = (
        await rows(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust)
    )[0].id
    body = {
        "pricing_model": "saas",
        "fee_paise": 50000,
        "billing_frequency": "monthly",
        "term_type": "plan",
        "contract_term": "monthly",
        "start_date": "2026-02-01",
        "reason": "confirm",
    }
    r = await client.post(f"/subscriptions/{sub_id}/confirm", json=body, headers=H(world.north1))
    assert r.status_code in (403, 404)
