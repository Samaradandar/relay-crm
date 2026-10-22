# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P5-02: entering Updated (existing_customer, deal.won) raises a pending SubscriptionChange;
confirming it is the only thing that ever mutates billing."""

import uuid
from types import SimpleNamespace

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.billing.changes import raise_change_on_won
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
    """A won + confirmed new_business deal: one customer with a confirmed per-minute
    subscription, ready for an existing_customer change against it."""
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
    account_id = (
        await rows(admin_engine, "SELECT account_id FROM customers WHERE id=:c", c=customer_id)
    )[0].account_id
    return owner, customer_id, sub_id, account_id


async def _existing_customer_deal(engine, world, owner, account_id, change: dict) -> uuid.UUID:
    did = uuid.uuid4()
    await run(
        engine,
        """INSERT INTO deals (id, org_id, account_id, pipeline, request_type, stage_key, status,
            owner_id, team_id, lead_source, closure_change)
           VALUES (:id, :o, :a, 'existing_customer', :rt, 'closure', 'open', :u, :t,
            'Existing customer', CAST(:cc AS jsonb))""",
        id=did,
        o=world.org_id,
        a=account_id,
        rt={"top_up": "upsell_top_up", "model_switch": "plan_change"}.get(
            change["change_type"], change["change_type"]
        ),
        u=owner.id,
        t=owner.team_id,
        cc=_json(change),
    )
    return did


def _json(d: dict) -> str:
    import json

    return json.dumps(d)


async def _fire_won(deal_id: uuid.UUID, owner) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await raise_change_on_won(
            s, SimpleNamespace(payload={"deal_id": str(deal_id), "by_id": str(owner.id)})
        )


async def test_entering_updated_creates_exactly_one_pending_change(client, world, admin_engine):
    owner, customer_id, sub_id, account_id = await _confirmed_customer(client, world, admin_engine)
    change = {
        "change_type": "top_up",
        "new_plan_values": {"size_minutes": "500", "price_paise": 80000},
        "effective_date": "2026-03-01",
    }
    did = await _existing_customer_deal(admin_engine, world, owner, account_id, change)
    await _fire_won(did, owner)
    await _fire_won(did, owner)  # at-least-once delivery: must not raise a second one
    pending = await rows(
        admin_engine,
        "SELECT id, status, change_type FROM subscription_changes WHERE source_deal_id=:d",
        d=did,
    )
    assert len(pending) == 1
    assert pending[0].status == "pending"
    assert pending[0].change_type == "top_up"


async def test_unconfirmed_change_mutates_nothing(client, world, admin_engine):
    owner, customer_id, sub_id, account_id = await _confirmed_customer(client, world, admin_engine)
    change = {
        "change_type": "top_up",
        "new_plan_values": {"size_minutes": "500", "price_paise": 80000},
        "effective_date": "2026-03-01",
    }
    did = await _existing_customer_deal(admin_engine, world, owner, account_id, change)
    await _fire_won(did, owner)
    packs = await rows(
        admin_engine, "SELECT id FROM minute_packs WHERE subscription_id=:s", s=sub_id
    )
    assert len(packs) == 1  # only the one from confirmation, no top-up yet
    versions = await rows(
        admin_engine,
        "SELECT version_no FROM subscription_versions WHERE subscription_id=:s",
        s=sub_id,
    )
    assert len(versions) == 2  # draft + confirm only


async def test_confirmed_top_up_creates_a_pack_drawn_after_older_ones(client, world, admin_engine):
    owner, customer_id, sub_id, account_id = await _confirmed_customer(client, world, admin_engine)
    change = {
        "change_type": "top_up",
        "new_plan_values": {"size_minutes": "500", "price_paise": 80000},
        "effective_date": "2026-03-01",
    }
    did = await _existing_customer_deal(admin_engine, world, owner, account_id, change)
    await _fire_won(did, owner)
    change_id = (
        await rows(
            admin_engine, "SELECT id FROM subscription_changes WHERE source_deal_id=:d", d=did
        )
    )[0].id
    r = await client.post(
        f"/subscription-changes/{change_id}/confirm",
        json={"reason": "approved top-up"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    packs = await rows(
        admin_engine,
        "SELECT size_minutes, purchased_on FROM minute_packs WHERE subscription_id=:s ORDER BY purchased_on",
        s=sub_id,
    )
    assert len(packs) == 2
    assert str(packs[1].size_minutes) == "500.000"

    from decimal import Decimal

    from app.modules.billing import packs as packs_service

    async with get_sessionmaker()() as s:
        draw_order = await s.execute(
            text(
                "SELECT id, purchased_on FROM minute_packs WHERE subscription_id=:s ORDER BY purchased_on"
            ),
            {"s": sub_id},
        )
        pack_rows = draw_order.all()
        await s.commit()
    async with get_sessionmaker()() as s, s.begin():
        await packs_service.draw(s, customer_id, None, Decimal("1500"))
    ledger = await rows(
        admin_engine,
        "SELECT minute_pack_id, minutes FROM minute_ledger WHERE customer_id=:c ORDER BY created_at",
        c=customer_id,
    )
    # the older (first-confirmed) pack is drawn down before the new top-up pack
    assert str(ledger[0].minute_pack_id) == str(pack_rows[0].id)


async def test_plan_change_regenerates_schedule_only_from_effective_date(
    client, world, admin_engine
):
    owner, customer_id, sub_id, account_id = await _confirmed_customer(client, world, admin_engine)
    # make this a SaaS subscription so there is a schedule to regenerate
    await run(
        admin_engine,
        "UPDATE subscriptions SET pricing_model='saas', fee_paise=100000, billing_frequency="
        "'monthly', contract_term='yearly', start_date='2026-01-01', contract_end_date='2026-12-31', "
        "pack_price_paise=NULL, pack_size_minutes=NULL WHERE id=:s",
        s=sub_id,
    )
    from app.modules.billing.schedule import periods

    await run(admin_engine, "DELETE FROM billing_schedule WHERE subscription_id=:s", s=sub_id)
    for p in periods(__import__("datetime").date(2026, 1, 1), "monthly", "yearly"):
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
    before = await rows(
        admin_engine,
        "SELECT period_start FROM billing_schedule WHERE subscription_id=:s ORDER BY period_start",
        s=sub_id,
    )
    assert len(before) == 12

    change = {
        "change_type": "plan_change",
        "new_plan_values": {"pricing_model": "saas", "plan": "Pro", "fee_paise": 150000},
        "effective_date": "2026-06-01",
    }
    did = await _existing_customer_deal(admin_engine, world, owner, account_id, change)
    await _fire_won(did, owner)
    change_id = (
        await rows(
            admin_engine, "SELECT id FROM subscription_changes WHERE source_deal_id=:d", d=did
        )
    )[0].id
    r = await client.post(
        f"/subscription-changes/{change_id}/confirm",
        json={"reason": "approved plan change"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    rowsafter = await rows(
        admin_engine,
        "SELECT period_start, expected_amount_paise FROM billing_schedule WHERE subscription_id=:s ORDER BY period_start",
        s=sub_id,
    )
    before_effective = [r for r in rowsafter if str(r.period_start) < "2026-06-01"]
    from_effective = [r for r in rowsafter if str(r.period_start) >= "2026-06-01"]
    assert len(before_effective) == 5  # Jan..May untouched
    assert all(r.expected_amount_paise == 100000 for r in before_effective)
    assert len(from_effective) >= 1
    assert all(r.expected_amount_paise == 150000 for r in from_effective)
    versions = await rows(
        admin_engine,
        "SELECT version_no FROM subscription_versions WHERE subscription_id=:s ORDER BY version_no",
        s=sub_id,
    )
    assert [v.version_no for v in versions] == [1, 2, 3]  # draft, confirm, this change


async def test_user_cannot_confirm_a_subscription_change(client, world, admin_engine):
    owner, customer_id, sub_id, account_id = await _confirmed_customer(
        client, world, admin_engine, owner=world.north1
    )
    change = {
        "change_type": "top_up",
        "new_plan_values": {"size_minutes": "100", "price_paise": 10000},
        "effective_date": "2026-03-01",
    }
    did = await _existing_customer_deal(admin_engine, world, owner, account_id, change)
    await _fire_won(did, owner)
    change_id = (
        await rows(
            admin_engine, "SELECT id FROM subscription_changes WHERE source_deal_id=:d", d=did
        )
    )[0].id
    r = await client.post(
        f"/subscription-changes/{change_id}/confirm",
        json={"reason": "I'll just approve my own"},
        headers=H(world.north1),
    )
    assert r.status_code == 403, r.text
    pending = await rows(
        admin_engine, "SELECT status FROM subscription_changes WHERE id=:c", c=change_id
    )
    assert pending[0].status == "pending"


async def test_renewal_change_extends_contract_and_writes_a_renewal_row(
    client, world, admin_engine
):
    owner, customer_id, sub_id, account_id = await _confirmed_customer(client, world, admin_engine)
    await run(
        admin_engine,
        "UPDATE subscriptions SET contract_end_date='2026-12-31', renewal_date='2026-12-31' WHERE id=:s",
        s=sub_id,
    )
    change = {
        "change_type": "renewal",
        "new_plan_values": {"contract_term": "yearly"},
        "effective_date": "2027-01-01",
    }
    did = await _existing_customer_deal(admin_engine, world, owner, account_id, change)
    await _fire_won(did, owner)
    change_id = (
        await rows(
            admin_engine, "SELECT id FROM subscription_changes WHERE source_deal_id=:d", d=did
        )
    )[0].id
    r = await client.post(
        f"/subscription-changes/{change_id}/confirm",
        json={"reason": "renewed"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    sub = await rows(
        admin_engine,
        "SELECT contract_end_date, renewal_status FROM subscriptions WHERE id=:s",
        s=sub_id,
    )
    assert str(sub[0].contract_end_date) == "2027-12-31"
    assert sub[0].renewal_status == "renewed"
    renewals = await rows(
        admin_engine, "SELECT outcome, deal_id FROM renewals WHERE subscription_id=:s", s=sub_id
    )
    assert len(renewals) == 1
    assert renewals[0].outcome == "renewed"
    assert str(renewals[0].deal_id) == str(did)
