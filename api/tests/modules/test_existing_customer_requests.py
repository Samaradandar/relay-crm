# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 5 PR2 (P5-01): POST /accounts/{id}/requests -- permission scope, Pool skip, and the
existing-customer duplicate marker."""

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.modules.leads.models import LeadPoolEntry
from tests.billing.test_customer_on_won import win
from tests.helpers import headers


def request_body(**kw):
    body = {"request_type": "upsell_top_up", "requirement": "Needs 2000 more minutes/month"}
    body.update(kw)
    return body


async def test_account_owner_can_create_a_request_and_pool_is_skipped(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    acc = (await client.get(f"/deals/{did}", headers=headers(owner))).json()["account_id"]
    r = await client.post(f"/accounts/{acc}/requests", json=request_body(), headers=headers(owner))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["stage_key"] == "request"
    assert body["owner"]["id"] == str(owner.id)
    async with get_sessionmaker()() as s:
        entry = (
            await s.execute(select(LeadPoolEntry).where(LeadPoolEntry.deal_id == body["id"]))
        ).first()
    assert entry is None  # the Lead Pool is skipped entirely


async def test_non_owner_user_is_denied(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    acc_id = (await client.get(f"/deals/{did}", headers=headers(owner))).json()["account_id"]
    other = world.north2 if owner.id != world.north2.id else world.south1
    r = await client.post(
        f"/accounts/{acc_id}/requests", json=request_body(), headers=headers(other)
    )
    assert r.status_code in (403, 404)


async def test_team_leader_of_the_owners_team_can_create_a_request(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    acc_id = (await client.get(f"/deals/{did}", headers=headers(owner))).json()["account_id"]
    r = await client.post(
        f"/accounts/{acc_id}/requests", json=request_body(), headers=headers(world.tl_north)
    )
    assert r.status_code == 201, r.text


async def test_team_leader_of_another_team_is_denied(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    acc_id = (await client.get(f"/deals/{did}", headers=headers(owner))).json()["account_id"]
    r = await client.post(
        f"/accounts/{acc_id}/requests", json=request_body(), headers=headers(world.tl_south)
    )
    assert r.status_code in (403, 404)


async def test_admin_needs_a_reason_super_admin_does_not(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    acc_id = (await client.get(f"/deals/{did}", headers=headers(owner))).json()["account_id"]
    r = await client.post(
        f"/accounts/{acc_id}/requests", json=request_body(), headers=headers(world.admin)
    )
    assert r.status_code == 422, r.text
    r = await client.post(
        f"/accounts/{acc_id}/requests",
        json=request_body(reason="Customer asked us to raise it"),
        headers=headers(world.admin),
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        f"/accounts/{acc_id}/requests", json=request_body(), headers=headers(world.sa)
    )
    assert r.status_code == 201, r.text


async def test_duplicate_check_marks_an_existing_customer(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    acc = (await client.get(f"/deals/{did}", headers=headers(owner))).json()
    company = acc["company"]
    r = await client.post("/duplicates/check", json={"company": company}, headers=headers(owner))
    assert r.status_code == 200, r.text
    matches = r.json()["block"] + r.json()["warn"]
    assert any(m.get("is_existing_customer") for m in matches)
