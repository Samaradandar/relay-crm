# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P5-06: record_churn, compute_monthly_value_lost, returning customer, reverse_churn."""

import uuid
from datetime import date

from app.modules.billing.models import Subscription
from app.modules.churn.service import compute_monthly_value_lost
from tests.billing.test_renewal_cancellation import _confirmed_customer, rows, run
from tests.helpers import headers as H

CHURN_REASONS = (
    "Price",
    "Competitor",
    "Not using the product",
    "Results not as expected",
    "Budget cuts",
    "Business closed",
    "Support issues",
    "Other",
)


async def _seed_reasons(admin_engine, org_id):
    for pos, label in enumerate(CHURN_REASONS, start=1):
        await run(
            admin_engine,
            "INSERT INTO churn_reasons (org_id, label, sort_order) VALUES (:o, :l, :p) "
            "ON CONFLICT (org_id, label) DO NOTHING",
            o=org_id,
            l=label,
            p=pos,
        )


async def test_record_churn_requires_reason_for_every_role_including_sa(
    client, world, admin_engine
):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": "2026-06-01", "reason_code": "Price"},
        headers=H(world.sa),
    )
    assert r.status_code == 422, r.text


async def test_record_churn_sets_customer_churned_and_ends_subscription(
    client, world, admin_engine
):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={
            "churn_date": "2026-06-01",
            "reason_code": "Price",
            "notes": "too expensive for them",
        },
        headers=H(world.tl_north),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["reason"] == "Price"
    assert body["monthly_value_lost_paise"] is not None
    cust = await rows(admin_engine, "SELECT status FROM customers WHERE id=:c", c=customer_id)
    assert cust[0].status == "churned"
    sub = await rows(
        admin_engine,
        "SELECT ended_at, end_reason, cancellation_status FROM subscriptions WHERE id=:s",
        s=sub_id,
    )
    assert sub[0].ended_at is not None
    assert sub[0].cancellation_status == "cancelled"


async def test_record_churn_flags_future_billing_schedule(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    await run(
        admin_engine,
        "UPDATE subscriptions SET pricing_model='saas', fee_paise=100000, pack_price_paise=NULL, "
        "pack_size_minutes=NULL WHERE id=:s",
        s=sub_id,
    )
    from app.modules.billing.schedule import periods

    await run(admin_engine, "DELETE FROM billing_schedule WHERE subscription_id=:s", s=sub_id)
    for p in periods(date(2026, 1, 1), "monthly", "yearly"):
        await run(
            admin_engine,
            "INSERT INTO billing_schedule (id, org_id, subscription_id, period_start, period_end, "
            "due_date, expected_amount_paise) VALUES (:id, :o, :s, :ps, :pe, :due, :amt)",
            id=uuid.uuid4(),
            o=world.org_id,
            s=sub_id,
            ps=p.start,
            pe=p.end,
            due=p.due,
            amt=100000,
        )
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": "2026-06-30", "reason_code": "Budget cuts", "notes": "cutting budget"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 201, r.text
    sched = await rows(
        admin_engine,
        "SELECT period_start, cancelled FROM billing_schedule WHERE subscription_id=:s "
        "ORDER BY period_start",
        s=sub_id,
    )
    for row in sched:
        if str(row.period_start) <= "2026-06-30":
            assert row.cancelled is False
        else:
            assert row.cancelled is True


async def test_record_churn_stores_monthly_value_once_not_retroactive(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": "2026-06-01", "reason_code": "Other", "notes": "n"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 201, r.text
    event_id = r.json()["id"]
    first_value = r.json()["monthly_value_lost_paise"]
    # a pack purchased after churn must never change the stored figure
    await run(
        admin_engine,
        "INSERT INTO minute_packs (id, org_id, customer_id, subscription_id, size_minutes, "
        "price_paise, purchased_on) VALUES (:id, :o, :c, :s, 1000, 999999, '2026-06-15')",
        id=uuid.uuid4(),
        o=world.org_id,
        c=customer_id,
        s=sub_id,
    )
    stored = await rows(
        admin_engine, "SELECT monthly_value_lost_paise FROM churn_events WHERE id=:e", e=event_id
    )
    assert stored[0].monthly_value_lost_paise == first_value


def _sub(pricing_model="saas", fee_paise=120000, billing_frequency="monthly"):
    return Subscription(
        org_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        source_deal_id=uuid.uuid4(),
        pricing_model=pricing_model,
        fee_paise=fee_paise,
        billing_frequency=billing_frequency,
    )


def test_compute_monthly_value_saas_monthly():
    assert (
        compute_monthly_value_lost(
            _sub(fee_paise=120000, billing_frequency="monthly"), [], date(2026, 6, 1)
        )
        == 120000
    )


def test_compute_monthly_value_saas_quarterly():
    assert (
        compute_monthly_value_lost(
            _sub(fee_paise=300000, billing_frequency="quarterly"), [], date(2026, 6, 1)
        )
        == 100000
    )


def test_compute_monthly_value_saas_yearly():
    assert (
        compute_monthly_value_lost(
            _sub(fee_paise=1200000, billing_frequency="yearly"), [], date(2026, 6, 1)
        )
        == 100000
    )


def test_compute_monthly_value_per_minute_full_3_months():
    sub = _sub(pricing_model="per_minute", fee_paise=None, billing_frequency=None)
    history = [
        (date(2026, 4, 10), 150000),
        (date(2026, 5, 10), 150000),
        (date(2026, 6, 1), 150000),
    ]
    assert compute_monthly_value_lost(sub, history, date(2026, 6, 15)) == 150000


def test_compute_monthly_value_per_minute_fewer_than_3_months():
    sub = _sub(pricing_model="per_minute", fee_paise=None, billing_frequency=None)
    history = [(date(2026, 6, 1), 150000)]
    assert compute_monthly_value_lost(sub, history, date(2026, 6, 15)) == 150000


def test_compute_monthly_value_per_minute_no_history():
    sub = _sub(pricing_model="per_minute", fee_paise=None, billing_frequency=None)
    assert compute_monthly_value_lost(sub, [], date(2026, 6, 15)) == 0


async def test_reverse_churn_denied_for_user_and_tl(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": "2026-06-01", "reason_code": "Other", "notes": "n"},
        headers=H(world.tl_north),
    )
    event_id = r.json()["id"]
    for actor in (world.north1, world.tl_north):
        resp = await client.post(
            f"/churn/{event_id}/reverse", json={"reason": "mistake"}, headers=H(actor)
        )
        assert resp.status_code == 403, resp.text


async def test_reverse_churn_admin_sets_reversed_fields_and_unchurns_customer(
    client, world, admin_engine
):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": "2026-06-01", "reason_code": "Other", "notes": "n"},
        headers=H(world.tl_north),
    )
    event_id = r.json()["id"]
    resp = await client.post(
        f"/churn/{event_id}/reverse",
        json={"reason": "recorded by mistake"},
        headers=H(world.admin),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reversed_at"] is not None
    assert body["reversed_by"] == str(world.admin.id)
    cust = await rows(admin_engine, "SELECT status FROM customers WHERE id=:c", c=customer_id)
    assert cust[0].status == "active"


async def test_returning_customer_request_auto_links_churn_event(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": "2026-06-01", "reason_code": "Other", "notes": "n"},
        headers=H(world.tl_north),
    )
    event_id = r.json()["id"]
    acc = await rows(admin_engine, "SELECT account_id FROM customers WHERE id=:c", c=customer_id)
    account_id = acc[0].account_id
    r2 = await client.post(
        f"/accounts/{account_id}/requests",
        json={"request_type": "return", "requirement": "wants to come back"},
        headers=H(world.north1),
    )
    assert r2.status_code == 201, r2.text
    linked = await rows(
        admin_engine, "SELECT returned_deal_id FROM churn_events WHERE id=:e", e=event_id
    )
    assert linked[0].returned_deal_id is not None


async def test_churned_account_open_existing_customer_deals_flagged(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    acc = await rows(admin_engine, "SELECT account_id FROM customers WHERE id=:c", c=customer_id)
    account_id = acc[0].account_id
    r0 = await client.post(
        f"/accounts/{account_id}/requests",
        json={"request_type": "upsell_top_up", "requirement": "wants more minutes"},
        headers=H(world.north1),
    )
    assert r0.status_code == 201, r0.text
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": "2026-06-01", "reason_code": "Other", "notes": "n"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 201, r.text
    deals = await rows(
        admin_engine,
        "SELECT flags FROM deals WHERE account_id=:a AND pipeline='existing_customer' "
        "AND status='open'",
        a=account_id,
    )
    assert deals, "expected an open existing_customer deal"
    assert all((d.flags or {}).get("churn_close_needed") is True for d in deals)
