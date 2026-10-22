# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P5-03: renewal status defaulting, manual renewal/cancellation recording, renewal_rate()."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.billing.metrics import renewal_rate
from tests.billing.test_customer_on_won import win
from tests.billing.test_subscription_draft_confirm import get_customer_id
from tests.helpers import headers as H


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def run(eng, sql, **kw):
    async with eng.begin() as c:
        await c.execute(text(sql), kw)


async def _confirmed_customer(client, world, admin_engine, owner=None):
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
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    return owner, customer_id, sub_id


async def test_confirming_a_subscription_defaults_renewal_status_upcoming(
    client, world, admin_engine
):
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    sub = await rows(admin_engine, "SELECT renewal_status FROM subscriptions WHERE id=:s", s=sub_id)
    assert sub[0].renewal_status == "upcoming"


async def test_record_not_renewing_sets_status_and_writes_renewal_row(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    r = await client.post(
        f"/subscriptions/{sub_id}/renewal",
        json={"outcome": "not_renewing", "reason": "Customer declined"},
        headers=H(world.north1),
    )
    assert r.status_code == 200, r.text
    assert r.json()["renewal_status"] == "not_renewing"
    sub = await rows(admin_engine, "SELECT renewal_status FROM subscriptions WHERE id=:s", s=sub_id)
    assert sub[0].renewal_status == "not_renewing"
    ren = await rows(
        admin_engine,
        "SELECT outcome, deal_id, reason, recorded_by FROM renewals WHERE subscription_id=:s",
        s=sub_id,
    )
    assert len(ren) == 1
    assert ren[0].outcome == "not_renewing"
    assert ren[0].deal_id is None
    assert ren[0].reason == "Customer declined"
    assert str(ren[0].recorded_by) == str(world.north1.id)


async def test_record_renewal_denied_without_reason(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    r = await client.post(
        f"/subscriptions/{sub_id}/renewal",
        json={"outcome": "not_renewing"},
        headers=H(world.north1),
    )
    assert r.status_code == 422, r.text


async def test_record_renewal_denied_for_non_owner_user(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    # a different User (not the account owner, not a TL/Admin) is out of "own" scope
    other_user = world.north2 if hasattr(world, "north2") else world.south1
    r = await client.post(
        f"/subscriptions/{sub_id}/renewal",
        json={"outcome": "renewed", "reason": "trying anyway"},
        headers=H(other_user),
    )
    assert r.status_code in (403, 404), r.text


async def test_owner_can_record_renewed_manually(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    r = await client.post(
        f"/subscriptions/{sub_id}/renewal",
        json={"outcome": "renewed", "reason": "renewed by phone"},
        headers=H(world.north1),
    )
    assert r.status_code == 200, r.text
    assert r.json()["renewal_status"] == "renewed"


async def test_sa_denied_without_reason_on_renewal_cancellation_churn(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/subscriptions/{sub_id}/renewal",
        json={"outcome": "renewed"},
        headers=H(world.sa),
    )
    assert r.status_code == 422, r.text
    r2 = await client.post(
        f"/subscriptions/{sub_id}/cancellation",
        json={"action": "request"},
        headers=H(world.sa),
    )
    assert r2.status_code == 422, r2.text


async def test_cancellation_request_sets_status_and_timestamp(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/subscriptions/{sub_id}/cancellation",
        json={"action": "request", "reason": "customer unhappy"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    assert r.json()["cancellation_status"] == "requested"
    sub = await rows(
        admin_engine,
        "SELECT cancellation_status, cancellation_requested_at FROM subscriptions WHERE id=:s",
        s=sub_id,
    )
    assert sub[0].cancellation_status == "requested"
    assert sub[0].cancellation_requested_at is not None


async def test_cancellation_cancel_flags_future_schedule_not_past(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    # make this a SaaS sub with a schedule to cancel
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
    # mark one past period as already invoiced, so it must survive untouched
    invoice_id = uuid.uuid4()
    await run(
        admin_engine,
        "INSERT INTO invoices (id, org_id, customer_id, subscription_id, number, issue_date, "
        "due_date, amount_ex_gst_paise, gst_paise, total_paise, status) VALUES "
        "(:id, :o, :c, :s, 'INV-1', '2026-01-01', '2026-01-01', 100000, 18000, 118000, 'open')",
        id=invoice_id,
        o=world.org_id,
        c=customer_id,
        s=sub_id,
    )
    await run(
        admin_engine,
        "UPDATE billing_schedule SET invoice_id=:i WHERE subscription_id=:s AND period_start='2026-01-01'",
        i=invoice_id,
        s=sub_id,
    )

    r = await client.post(
        f"/subscriptions/{sub_id}/cancellation",
        json={"action": "cancel", "end_date": "2026-06-30", "reason": "customer requested"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancellation_status"] == "cancelled"
    assert body["ended_at"] is not None
    assert body["end_reason"] == "customer requested"

    sched = await rows(
        admin_engine,
        "SELECT period_start, cancelled, invoice_id FROM billing_schedule WHERE subscription_id=:s "
        "ORDER BY period_start",
        s=sub_id,
    )
    for row in sched:
        if row.invoice_id is not None:
            assert row.cancelled is False  # invoiced row untouched
        elif str(row.period_start) <= "2026-06-30":
            assert row.cancelled is False  # on/before the chosen end date: untouched
        else:
            assert row.cancelled is True  # after the chosen end date: flagged, never deleted
    assert len(sched) == 12  # append-only: nothing was deleted

    inv = await rows(admin_engine, "SELECT status FROM invoices WHERE id=:i", i=invoice_id)
    assert inv[0].status == "open"  # existing invoice untouched


async def test_cancellation_revert_resets_status(client, world, admin_engine):
    owner, customer_id, sub_id = await _confirmed_customer(client, world, admin_engine)
    r = await client.post(
        f"/subscriptions/{sub_id}/cancellation",
        json={"action": "cancel", "reason": "cancel for revert test"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    r2 = await client.post(
        f"/subscriptions/{sub_id}/cancellation",
        json={"action": "revert", "reason": "mistake, reverting"},
        headers=H(world.tl_north),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["cancellation_status"] == "none"
    sub = await rows(
        admin_engine,
        "SELECT cancellation_status, ended_at, end_reason FROM subscriptions WHERE id=:s",
        s=sub_id,
    )
    assert sub[0].cancellation_status == "none"
    assert sub[0].ended_at is None
    assert sub[0].end_reason is None


# ------------------------------------------------------------------------- renewal_rate()
async def test_renewal_rate_ratio_over_a_small_dataset(client, world, admin_engine):
    org_id = world.org_id
    sub_ids = []
    for _ in range(3):
        _, _, sub_id = await _confirmed_customer(client, world, admin_engine)
        sub_ids.append(sub_id)
    outcomes = ["renewed", "renewed", "not_renewing"]
    ends = [date(2026, 3, 31), date(2026, 3, 15), date(2026, 3, 20)]
    for sub_id, outcome, end in zip(sub_ids, outcomes, ends, strict=True):
        await run(
            admin_engine,
            "UPDATE subscriptions SET renewal_status=:rs, contract_end_date=:end WHERE id=:s",
            rs=outcome,
            end=end,
            s=sub_id,
        )
    async with get_sessionmaker()() as s:
        rate = await renewal_rate(s, org_id, date(2026, 3, 1), date(2026, 3, 31))
    assert rate == Decimal(2) / Decimal(3)


async def test_renewal_rate_none_when_no_contracts_end_in_period(world, database):
    async with get_sessionmaker()() as s:
        rate = await renewal_rate(s, world.org_id, date(2030, 1, 1), date(2030, 1, 31))
    assert rate is None
