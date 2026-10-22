# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-01: a Won deal makes one customer; the winning rep keeps the account; scope on reads."""

import uuid

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.modules.customers.service import on_deal_won
from app.modules.leads.models import Deal
from app.platform.outbox.models import OutboxEvent
from tests.deep.phase3.p3 import mkdeal
from tests.helpers import dispatch_all
from tests.helpers import headers as H


async def win(client, world, eng, owner=None):
    did, owner = await mkdeal(client, world, eng, owner=owner, stage="closure", all_ticked=True)
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "onboarding"}, headers=H(owner))
    assert r.status_code == 200, r.text
    await dispatch_all()
    return did, owner


async def one(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def test_win_creates_one_customer_and_winning_rep_keeps_the_account(
    client, world, admin_engine
):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    rows = await one(
        admin_engine,
        "SELECT c.id, c.account_owner_id, c.first_won_deal_id, c.status, a.account_owner_id AS ao "
        "FROM customers c JOIN accounts a ON a.id = c.account_id WHERE c.org_id = :o",
        o=world.org_id,
    )
    assert len(rows) == 1
    cust = rows[0]
    assert cust.account_owner_id == owner.id == cust.ao
    assert str(cust.first_won_deal_id) == str(did)
    assert cust.status == "active"
    audit = await one(
        admin_engine,
        "SELECT reason, new_value FROM audit_log WHERE org_id = :o AND action_key = 'customer.create'",
        o=world.org_id,
    )
    assert len(audit) == 1 and audit[0].reason == "Deal won"
    created = await one(
        admin_engine,
        "SELECT count(*) AS n FROM outbox_events WHERE org_id = :o AND type = 'customer.created'",
        o=world.org_id,
    )
    assert created[0].n == 1


async def test_replaying_the_won_event_creates_nothing_new(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine)
    async with get_sessionmaker()() as s, s.begin():
        event = (
            await s.execute(
                text("SELECT id FROM outbox_events WHERE org_id=:o AND type='deal.won'"),
                {"o": world.org_id},
            )
        ).first()
        ev = await s.get(OutboxEvent, event.id)
        assert ev is not None
        await on_deal_won(s, ev)  # at-least-once delivery: the same event again
        await on_deal_won(s, ev)
        deal = await s.get(Deal, did)
        assert deal is not None
    customers = await one(
        admin_engine, "SELECT id FROM customers WHERE org_id = :o", o=world.org_id
    )
    audit = await one(
        admin_engine,
        "SELECT id FROM audit_log WHERE org_id = :o AND action_key = 'customer.create'",
        o=world.org_id,
    )
    assert len(customers) == 1 and len(audit) == 1


async def test_customer_reads_follow_scope(client, world, admin_engine):
    await win(client, world, admin_engine, owner=world.north1)
    cid = (await one(admin_engine, "SELECT id FROM customers WHERE org_id=:o", o=world.org_id))[
        0
    ].id

    async def ids(actor):
        r = await client.get("/customers", headers=H(actor))
        assert r.status_code == 200, (actor.role, r.text)
        return [c["id"] for c in r.json()]

    for allowed in (world.north1, world.tl_north, world.admin, world.sa):
        assert str(cid) in await ids(allowed), allowed.role
        got = await client.get(f"/customers/{cid}", headers=H(allowed))
        assert got.status_code == 200
        assert got.json()["account_owner_id"] == str(world.north1.id)
    for denied in (world.north2, world.south1, world.tl_south):
        assert str(cid) not in await ids(denied), denied.role
        assert (await client.get(f"/customers/{cid}", headers=H(denied))).status_code == 404
    assert (
        await client.get(f"/customers/{uuid.uuid4()}", headers=H(world.admin))
    ).status_code == 404
