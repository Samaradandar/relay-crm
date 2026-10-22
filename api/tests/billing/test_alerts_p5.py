# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P5-04/P5-05: contract-end alerts, cancellation-requested churn risk (event-driven), contract
ended without renewal, the /customers/at-risk view, and Customer.status accepting 'churned'."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.billing.alerts import CHURN_RISK
from app.modules.billing.alerts_p5 import (
    CONTRACT_APPROACHING_END,
    CONTRACT_ENDED_NO_RENEWAL,
    check_contract_approaching_end,
    check_contract_ended_no_renewal,
)
from app.modules.billing.packs import draw
from tests.billing.test_alerts import make_pack
from tests.billing.test_customer_on_won import win
from tests.billing.test_invoices import invoice_body
from tests.billing.test_subscription_draft_confirm import get_customer_id
from tests.helpers import dispatch_all, notifications_for
from tests.helpers import headers as H


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def run(eng, sql, **kw):
    async with eng.begin() as c:
        await c.execute(text(sql), kw)


async def _confirmed_customer(client, world, admin_engine, owner=None):
    """Like test_renewal_cancellation's helper, but confirms as world.admin so it works for an
    owner on either team (Admin is in scope for every customer)."""
    did, owner = await win(client, world, admin_engine, owner=owner)
    customer_id = await get_customer_id(admin_engine, did)
    sub_id = (
        await rows(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=customer_id)
    )[0].id
    body = {
        "billing_frequency": "monthly",
        "term_type": "contract",
        "contract_term": "yearly",
        "start_date": "2026-01-01",
        "contract_end_date": "2026-12-31",
        "pricing_model": "per_minute",
        "pack_price_paise": 150000,
        "pack_size_minutes": "1000",
    }
    r = await client.post(
        f"/subscriptions/{sub_id}/confirm",
        json={**body, "reason": "confirming"},
        headers=H(world.admin),
    )
    assert r.status_code == 200, r.text
    return owner, customer_id, sub_id


# --------------------------------------------------------------- contract approaching end
async def test_contract_approaching_end_fires_once_at_14_days_and_once_on_the_day(
    client, world, admin_engine
):
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    end = date(2026, 6, 30)
    await run(
        admin_engine, "UPDATE subscriptions SET contract_end_date=:e WHERE id=:s", e=end, s=sub_id
    )

    warn_day = end - timedelta(days=14)
    async with get_sessionmaker()() as s, s.begin():
        n = await check_contract_approaching_end(s, world.org_id, warn_day)
    assert n == 1
    assert any(x.type == CONTRACT_APPROACHING_END for x in await notifications_for(world.north1.id))
    assert any(
        x.type == CONTRACT_APPROACHING_END for x in await notifications_for(world.tl_north.id)
    )

    # same day again: no duplicate
    async with get_sessionmaker()() as s, s.begin():
        n2 = await check_contract_approaching_end(s, world.org_id, warn_day)
    assert n2 == 0

    # the day itself: fires again (a distinct date)
    async with get_sessionmaker()() as s, s.begin():
        n3 = await check_contract_approaching_end(s, world.org_id, end)
    assert n3 == 1
    owner_hits = [
        x for x in await notifications_for(world.north1.id) if x.type == CONTRACT_APPROACHING_END
    ]
    assert len(owner_hits) == 2  # once at -14d, once on the day

    async with get_sessionmaker()() as s, s.begin():
        n4 = await check_contract_approaching_end(s, world.org_id, end)
    assert n4 == 0


# --------------------------------------------------------------- cancellation requested (event-driven)
async def test_cancellation_requested_alerts_admins_and_sa(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    r = await client.post(
        f"/subscriptions/{sub_id}/cancellation",
        json={"action": "request", "reason": "customer unhappy"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    await dispatch_all()
    assert any(x.type == CHURN_RISK for x in await notifications_for(world.admin.id))
    assert any(x.type == CHURN_RISK for x in await notifications_for(world.sa.id))

    # redelivery (outbox at-least-once) must not double-notify
    before = len([x for x in await notifications_for(world.admin.id) if x.type == CHURN_RISK])
    from app.modules.billing.alerts_p5 import churn_risk_on_cancellation_requested
    from app.platform.outbox.models import OutboxEvent

    async with get_sessionmaker()() as s, s.begin():
        event = (
            await s.execute(
                text(
                    "SELECT id, org_id, type, payload FROM outbox_events WHERE type="
                    "'subscription.cancellation_requested' ORDER BY id DESC LIMIT 1"
                )
            )
        ).first()
        ev = OutboxEvent(org_id=event.org_id, type=event.type, payload=event.payload)
        await churn_risk_on_cancellation_requested(s, ev)
    after = len([x for x in await notifications_for(world.admin.id) if x.type == CHURN_RISK])
    assert after == before


# --------------------------------------------------------------- contract ended, no renewal
async def test_contract_ended_without_renewal_fires_once_day_after_only_when_upcoming(
    client, world, admin_engine
):
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    end = date(2026, 5, 31)
    await run(
        admin_engine, "UPDATE subscriptions SET contract_end_date=:e WHERE id=:s", e=end, s=sub_id
    )
    day_after = end + timedelta(days=1)

    async with get_sessionmaker()() as s, s.begin():
        n = await check_contract_ended_no_renewal(s, world.org_id, day_after)
    assert n == 1
    assert any(
        x.type == CONTRACT_ENDED_NO_RENEWAL for x in await notifications_for(world.north1.id)
    )
    assert any(x.type == CONTRACT_ENDED_NO_RENEWAL for x in await notifications_for(world.admin.id))

    async with get_sessionmaker()() as s, s.begin():
        n2 = await check_contract_ended_no_renewal(s, world.org_id, day_after)
    assert n2 == 0  # dedup

    # a second subscription that WAS renewed must never alert
    owner2, customer_id2, sub_id2 = await _confirmed_customer(
        client, world, admin_engine, owner=world.south1
    )
    await run(
        admin_engine,
        "UPDATE subscriptions SET contract_end_date=:e, renewal_status='renewed' WHERE id=:s",
        e=end,
        s=sub_id2,
    )
    async with get_sessionmaker()() as s, s.begin():
        n3 = await check_contract_ended_no_renewal(s, world.org_id, day_after)
    assert n3 == 0
    assert not any(
        x.type == CONTRACT_ENDED_NO_RENEWAL for x in await notifications_for(world.south1.id)
    )


# --------------------------------------------------------------- GET /customers/at-risk
async def test_at_risk_view_scoping_and_reasons(client, world, admin_engine):
    # scenario 1: cancellation requested only (owned by north1)
    owner1, cust1, sub1 = await _confirmed_customer(client, world, admin_engine, owner=world.north1)
    r = await client.post(
        f"/subscriptions/{sub1}/cancellation",
        json={"action": "request", "reason": "at risk"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text

    # scenario 2: overdue payment + minutes ran out (owned by south1, different team)
    owner2, cust2, sub2 = await _confirmed_customer(client, world, admin_engine, owner=world.south1)
    past_due = date(2026, 1, 1)
    await client.post(
        f"/customers/{cust2}/invoices",
        json=invoice_body(issue_date="2025-12-01", due_date=past_due.isoformat()),
        headers=H(world.admin),
    )
    await run(admin_engine, "DELETE FROM minute_packs WHERE customer_id=:c", c=cust2)
    await make_pack(admin_engine, world, cust2, sub2, size="10")
    async with get_sessionmaker()() as s, s.begin():
        await draw(s, cust2, None, Decimal("10"), today=past_due)

    # scenario 3: contract ending soon, renewal still upcoming (owned by north1)
    owner3, cust3, sub3 = await _confirmed_customer(client, world, admin_engine, owner=world.north1)
    await run(
        admin_engine,
        "UPDATE subscriptions SET contract_end_date=:e WHERE id=:s",
        e=date.today() + timedelta(days=7),
        s=sub3,
    )

    # Admin / SA see all three
    for actor in (world.admin, world.sa):
        r = await client.get("/customers/at-risk", headers=H(actor))
        assert r.status_code == 200, r.text
        ids = {row["customer_id"] for row in r.json()}
        assert {str(cust1), str(cust2), str(cust3)} <= ids
        by_id = {row["customer_id"]: row["reasons"] for row in r.json()}
        assert "cancellation_requested" in by_id[str(cust1)]
        assert "payment_overdue" in by_id[str(cust2)]
        assert "minutes_ran_out" in by_id[str(cust2)]
        assert "contract_ending_soon" in by_id[str(cust3)]

    # TL north sees only their team's (cust1, cust3), not south's cust2
    r = await client.get("/customers/at-risk", headers=H(world.tl_north))
    assert r.status_code == 200, r.text
    ids = {row["customer_id"] for row in r.json()}
    assert str(cust1) in ids and str(cust3) in ids
    assert str(cust2) not in ids

    # north1 (a rep) sees only their own
    r = await client.get("/customers/at-risk", headers=H(world.north1))
    assert r.status_code == 200, r.text
    ids = {row["customer_id"] for row in r.json()}
    assert str(cust1) in ids and str(cust3) in ids
    assert str(cust2) not in ids

    # south1 sees only their own
    r = await client.get("/customers/at-risk", headers=H(world.south1))
    assert r.status_code == 200, r.text
    ids = {row["customer_id"] for row in r.json()}
    assert ids == {str(cust2)}


# --------------------------------------------------------------- Customer.status widened
async def test_customer_status_can_be_set_to_churned(client, world, admin_engine):
    owner, customer_id = (await _confirmed_customer(client, world, admin_engine))[0:2]
    await run(admin_engine, "UPDATE customers SET status='churned' WHERE id=:c", c=customer_id)
    row = await rows(admin_engine, "SELECT status FROM customers WHERE id=:c", c=customer_id)
    assert row[0].status == "churned"
