# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-02: subscription drafted on Won, confirmed with roles, versions, billing schedule."""

from sqlalchemy import text

from tests.billing.test_customer_on_won import win
from tests.helpers import headers as H


async def one(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def test_win_drafts_exactly_one_subscription_that_bills_nothing(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine)
    subs = await one(
        admin_engine, "SELECT id, status, version FROM subscriptions WHERE source_deal_id=:d", d=did
    )
    assert len(subs) == 1
    assert subs[0].status == "draft" and subs[0].version == 1
    sched = await one(
        admin_engine, "SELECT id FROM billing_schedule WHERE subscription_id=:s", s=subs[0].id
    )
    assert sched == []  # an unconfirmed subscription generates no schedule
    versions = await one(
        admin_engine,
        "SELECT version_no FROM subscription_versions WHERE subscription_id=:s",
        s=subs[0].id,
    )
    assert [v.version_no for v in versions] == [1]


async def test_replaying_won_event_never_makes_a_second_draft(client, world, admin_engine):
    from app.core.db import get_sessionmaker
    from app.modules.billing.draft import draft_on_won
    from app.modules.leads.models import Deal
    from app.platform.outbox.models import OutboxEvent

    did, owner = await win(client, world, admin_engine)
    async with get_sessionmaker()() as s, s.begin():
        ev_row = (
            await s.execute(
                text("SELECT id FROM outbox_events WHERE org_id=:o AND type='deal.won'"),
                {"o": world.org_id},
            )
        ).first()
        ev = await s.get(OutboxEvent, ev_row.id)
        assert ev is not None
        await draft_on_won(s, ev)
        await draft_on_won(s, ev)
        assert await s.get(Deal, did) is not None
    subs = await one(admin_engine, "SELECT id FROM subscriptions WHERE source_deal_id=:d", d=did)
    assert len(subs) == 1


async def get_customer_id(eng, did):
    return (await one(eng, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did))[0].id


async def test_confirm_roles_and_shape_validation(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub_id = (await one(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust))[
        0
    ].id

    def confirm(actor, body=None, reason="confirming"):
        payload = {**(body or {}), "reason": reason}
        return client.post(f"/subscriptions/{sub_id}/confirm", json=payload, headers=H(actor))

    assert (await confirm(world.north1)).status_code == 403  # Users view only
    assert (await confirm(world.south1)).status_code == 403  # out of scope team

    # Incomplete: the draft is missing required fields, so a bare confirm fails validation
    r = await confirm(world.tl_north)
    assert r.status_code == 422, r.text

    body = {
        "billing_frequency": "monthly",
        "term_type": "contract",
        "contract_term": "yearly",
        "start_date": "2026-02-01",
        "pricing_model": "per_minute",
        "pack_price_paise": 150000,
        "pack_size_minutes": "1000",
    }
    r = await confirm(world.tl_north, body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "confirmed"
    assert out["confirmed_by"] == str(world.tl_north.id)
    assert out["version"] == 2

    # confirming a per-minute subscription creates its first minute pack from the deal
    pack = await one(
        admin_engine,
        "SELECT size_minutes, price_paise FROM minute_packs WHERE subscription_id=:s",
        s=sub_id,
    )
    assert len(pack) == 1
    assert pack[0].size_minutes == 1000 and pack[0].price_paise == 150000

    # already confirmed: a second confirm attempt is refused
    assert (await confirm(world.admin, body)).status_code == 422
    assert (
        len(
            await one(
                admin_engine, "SELECT id FROM minute_packs WHERE subscription_id=:s", s=sub_id
            )
        )
        == 1
    )  # the refused re-confirm did not create a second pack

    rows = await one(
        admin_engine,
        "SELECT reason FROM audit_log WHERE org_id=:o AND action_key=:k AND entity_id=:e",
        o=world.org_id,
        k="contract_dates.set_and_subscription.confirm",
        e=str(sub_id),
    )
    assert len(rows) == 1 and rows[0].reason == "confirming"


async def test_saas_confirm_generates_the_billing_schedule_in_advance(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub_id = (await one(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust))[
        0
    ].id
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE subscriptions SET pricing_model='saas', fee_paise=400000 WHERE id=:s"),
            {"s": sub_id},
        )
    body = {
        "billing_frequency": "quarterly",
        "term_type": "contract",
        "contract_term": "yearly",
        "start_date": "2026-01-31",
        "fee_paise": 400000,
        "reason": "confirm",
    }
    r = await client.post(f"/subscriptions/{sub_id}/confirm", json=body, headers=H(world.admin))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["contract_end_date"] == "2027-01-30"
    rows = await one(
        admin_engine,
        "SELECT period_start, period_end, due_date, expected_amount_paise FROM billing_schedule "
        "WHERE subscription_id=:s ORDER BY period_start",
        s=sub_id,
    )
    starts = [str(r.period_start) for r in rows]
    assert starts == ["2026-01-31", "2026-04-30", "2026-07-31", "2026-10-31"]
    assert str(rows[0].period_end) == "2026-04-29"  # the day before the next period starts
    assert all(r.due_date == r.period_start for r in rows)  # billed in advance
    assert all(r.expected_amount_paise == 400000 for r in rows)


async def test_confirmed_subscription_contract_dates_can_be_adjusted(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    sub_id = (await one(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=cust))[
        0
    ].id
    body = {
        "billing_frequency": "monthly", "term_type": "plan", "contract_term": "monthly",
        "start_date": "2026-02-01", "reason": "confirming",
        "pricing_model": "per_minute", "pack_price_paise": 150000, "pack_size_minutes": "1000",
    }  # fmt: skip
    confirmed = await client.post(
        f"/subscriptions/{sub_id}/confirm", json=body, headers=H(world.admin)
    )
    assert confirmed.status_code == 200, confirmed.text
    r = await client.patch(
        f"/customers/{cust}/subscription",
        json={"renewal_date": "2027-03-01", "reason": "customer asked to push renewal"},
        headers=H(world.admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["renewal_date"] == "2027-03-01"
    assert r.json()["version"] == 3
    versions = await one(
        admin_engine,
        "SELECT version_no, reason FROM subscription_versions WHERE subscription_id=:s "
        "ORDER BY version_no",
        s=sub_id,
    )
    assert [v.version_no for v in versions] == [1, 2, 3]
    assert versions[2].reason == "customer asked to push renewal"


async def test_pending_queue_follows_customer_view_scope(client, world, admin_engine):
    """Users may only view (PRD: Users can view only), so their own draft still shows here;
    confirming it is a separate, denied action (test_confirm_roles_and_shape_validation)."""
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    r = await client.get("/subscriptions/pending", headers=H(world.tl_north))
    assert r.status_code == 200
    assert any(s["source_deal_id"] == str(did) for s in r.json())
    r2 = await client.get("/subscriptions/pending", headers=H(world.tl_south))
    assert all(s["source_deal_id"] != str(did) for s in r2.json())
    r3 = await client.get("/subscriptions/pending", headers=H(world.north1))
    assert r3.status_code == 200
    assert any(s["source_deal_id"] == str(did) for s in r3.json())
    r4 = await client.get("/subscriptions/pending", headers=H(world.south1))
    assert all(s["source_deal_id"] != str(did) for s in r4.json())
