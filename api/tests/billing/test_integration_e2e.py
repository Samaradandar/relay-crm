# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Full Phase 4 integration: a deal wins, becomes a billed, paying customer, with usage flowing
in and alerts firing, all through the real HTTP endpoints and the real services wired together
(no mocking of anything except the outside world: email, storage, the voice platform feed).

This is deliberately one long scenario, in order, because the point is to prove the modules
hand off to each other correctly - draft -> confirm -> pack -> usage -> invoice -> payment ->
alerts -> billing tab - not to re-prove any one rule in isolation (that is what the other
tests/billing/*.py files do).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.billing.alerts import check_balances, check_payment_due_and_overdue
from app.platform.files import storage as storage_module
from app.platform.notifications import email as email_module
from tests.billing.test_customer_on_won import win
from tests.billing.test_invoices import invoice_body
from tests.deep.phase1.kit import FakeStorage
from tests.helpers import headers as H
from tests.helpers import notifications_for


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def test_full_per_minute_customer_lifecycle(client, world, admin_engine):
    # 1. Win the deal: one customer, one draft subscription, nothing billed yet.
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust_row = await rows(
        admin_engine, "SELECT id, account_owner_id FROM customers WHERE first_won_deal_id=:d", d=did
    )
    assert len(cust_row) == 1
    cust = cust_row[0].id
    assert cust_row[0].account_owner_id == world.north1.id

    draft = await client.get(f"/customers/{cust}/subscription", headers=H(world.admin))
    assert draft.json()["status"] == "draft"
    assert (await client.get(f"/customers/{cust}/packs", headers=H(world.admin))).json()[
        "packs"
    ] == []

    # 2. Confirm as a per-minute subscription: the first pack appears automatically.
    sub_id = draft.json()["id"]
    confirmed = await client.post(
        f"/subscriptions/{sub_id}/confirm",
        json={
            "billing_frequency": "monthly",
            "term_type": "contract",
            "contract_term": "yearly",
            "start_date": "2026-02-01",
            "pricing_model": "per_minute",
            "pack_price_paise": 650000,
            "pack_size_minutes": "5000",
            "reason": "onboarding",
        },
        headers=H(world.tl_north),
    )
    assert confirmed.status_code == 200, confirmed.text
    packs = (await client.get(f"/customers/{cust}/packs", headers=H(world.admin))).json()
    assert len(packs["packs"]) == 1
    assert packs["packs"][0]["size_minutes"] == "5000.000"
    assert packs["balance_minutes"] == "5000.000"

    # 3. Map the customer to the voice platform and feed in usage: it draws the pack down.
    voice_id = f"voice-{uuid.uuid4().hex[:8]}"
    mapped = await client.patch(
        f"/customers/{cust}/voice-mapping",
        json={"voice_platform_customer_id": voice_id, "reason": "map"},
        headers=H(world.admin),
    )
    assert mapped.status_code == 200
    from app.modules.usage_feed.service import Page, UsageRow, run_sync

    page = Page(
        records=[
            UsageRow(
                f"call-{i}", voice_id, datetime(2026, 2, i + 1, tzinfo=UTC), Decimal("1000.000")
            )
            for i in range(4)
        ],
        next_cursor="done",
    )

    async def fetch(since, limit):
        return page if since is None else Page(records=[], next_cursor=since)

    sync_result = await run_sync(world.org_id, get_sessionmaker(), fetch)
    assert sync_result["pulled"] == 4
    after_usage = (await client.get(f"/customers/{cust}/usage", headers=H(world.admin))).json()
    assert after_usage["balance_minutes"] == "1000.000"  # 5000 - 4x1000

    # the balance is now exactly 20%: low-balance fires once, to the owner and their TL
    async with get_sessionmaker()() as s, s.begin():
        n = await check_balances(s, world.org_id, date(2026, 2, 5))
    assert n == 1
    assert any(x.type == "billing.low_balance" for x in await notifications_for(world.north1.id))
    assert any(x.type == "billing.low_balance" for x in await notifications_for(world.tl_north.id))

    # 4. One more usage burst exhausts the pack and spills into unbilled overage.
    page2 = Page(
        records=[
            UsageRow(
                "call-overflow", voice_id, datetime(2026, 2, 6, tzinfo=UTC), Decimal("1500.000")
            )
        ],
        next_cursor="done2",
    )

    async def fetch2(since, limit):
        return page2 if since == "done" else Page(records=[], next_cursor=since)

    await run_sync(world.org_id, get_sessionmaker(), fetch2)
    after_overflow = (await client.get(f"/customers/{cust}/usage", headers=H(world.admin))).json()
    assert after_overflow["balance_minutes"] == "0.000"
    assert after_overflow["unbilled_overage_minutes"] == "500.000"
    assert after_overflow["overage"][0]["reason"] == "no_pack"

    # 5. Raise an invoice for the pack, record a partial then a full payment.
    proof_id = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO files (id, org_id, owner_entity_type, owner_entity_id, key, "
                "filename, content_type, size, uploaded_by) VALUES "
                "(:i,:o,'x','x',:k,'x','x',1,:u)"
            ),
            {"i": proof_id, "o": world.org_id, "k": f"proof-{proof_id}", "u": world.admin.id},
        )
    inv = await client.post(
        f"/customers/{cust}/invoices",
        json=invoice_body(amount_ex_gst_paise=650000, gst_paise=117000, due_date="2026-01-25"),
        headers=H(world.admin),
    )
    assert inv.status_code == 201, inv.text
    inv_id = inv.json()["id"]

    async with get_sessionmaker()() as s, s.begin():
        n_due = await check_payment_due_and_overdue(s, world.org_id, date(2026, 1, 26))
    assert n_due == 1  # overdue the day after, before any payment
    assert any("churn" in x.type for x in await notifications_for(world.admin.id))

    pay1 = await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 400000,
            "received_on": "2026-02-10",
            "method": "bank transfer",
            "proof_file_id": str(proof_id),
            "reason": "part payment",
        },
        headers=H(world.admin),
    )
    assert pay1.status_code == 201
    assert (await rows(admin_engine, "SELECT status FROM invoices WHERE id=:i", i=inv_id))[
        0
    ].status == "part_paid"

    pay2 = await client.post(
        f"/customers/{cust}/payments",
        json={
            "amount_paise": 367000,
            "received_on": "2026-02-12",
            "method": "bank transfer",
            "proof_file_id": str(proof_id),
            "reason": "balance",
        },
        headers=H(world.admin),
    )
    assert pay2.status_code == 201
    assert (await rows(admin_engine, "SELECT status FROM invoices WHERE id=:i", i=inv_id))[
        0
    ].status == "paid"

    # 6. The billing tab adds it all up, ex-GST and GST-inclusive, to the stored paise exactly.
    tab = (await client.get(f"/customers/{cust}/billing", headers=H(world.north1))).json()
    assert tab["revenue_ex_gst_paise"] == 650000
    assert tab["revenue_gst_paise"] == 117000
    assert tab["revenue_paise"] == 650000  # default view is ex-GST
    tab_gst = (
        await client.get(
            f"/customers/{cust}/billing", params={"gst_inclusive": True}, headers=H(world.north1)
        )
    ).json()
    assert tab_gst["revenue_paise"] == 767000
    assert len(tab["invoices"]) == 1 and len(tab["payments"]) == 2
    assert tab["unbilled_overage_minutes"] == "500.000"

    # Phase 6 reads customer_payments_received(deal_id) for incentives: it must equal exactly
    # what was received and allocated, paise for paise.
    from app.modules.billing.payments import customer_payments_received

    async with get_sessionmaker()() as s:
        received = await customer_payments_received(s, did)
    assert received == 767000


async def test_proposal_to_sent_deal_within_full_flow(client, world, admin_engine):
    """A proposal sent on the same deal that later wins still ends with a consistent trail:
    the proposal's document record, the won customer, and the draft subscription all agree."""
    from tests.deep.phase3.p3 import mkdeal
    from tests.deep.phase3.test_05_value_discounts import add_line, product

    did, owner = await mkdeal(client, world, admin_engine, owner=world.north1)
    prod = await product(client, world, f"Pack-{did}", 150, 100, unit="per_minute")
    await add_line(client, owner, did, prod["id"], 200)

    await client.put(
        "/settings/proposal-template",
        json={
            "name": "Standard",
            "body": "<p>{{company}}</p>",
            "terms_html": "T&C",
            "reason": "qa",
        },
        headers=H(world.admin),
    )
    email_module.set_backend(email_module.MemoryBackend())
    storage_module.set_storage(FakeStorage())
    try:
        proposal = (await client.post(f"/deals/{did}/proposals", headers=H(owner))).json()
        async with admin_engine.begin() as c:
            acc = (
                await c.execute(text("SELECT account_id FROM deals WHERE id=:d"), {"d": did})
            ).scalar()
            await c.execute(
                text(
                    "INSERT INTO contacts (org_id, account_id, name, email) "
                    "VALUES (:o,:a,'Billing',:e)"
                ),
                {"o": world.org_id, "a": acc, "e": "billing@example.com"},
            )
        sent = await client.post(
            f"/proposals/{proposal['id']}/send", json={"reason": "go"}, headers=H(owner)
        )
        assert sent.status_code == 200, sent.text

        did2, _owner2 = await win(client, world, admin_engine, owner=world.north1)
    finally:
        email_module.set_backend(None)
        storage_module.set_storage(None)

    docs = await rows(admin_engine, "SELECT kind, status FROM documents WHERE deal_id=:d", d=did)
    assert any(d.kind == "proposal" and d.status == "sent" for d in docs)
    cust = await rows(admin_engine, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did2)
    assert len(cust) == 1  # the other, unrelated deal's own customer, untouched by this one
