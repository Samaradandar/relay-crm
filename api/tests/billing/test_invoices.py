# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-05: invoices, versions, void, and a TL's edit going through approval."""

import uuid

from sqlalchemy import text

from tests.billing.test_customer_on_won import win
from tests.helpers import headers as H


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def get_customer_id(eng, did):
    return (await rows(eng, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did))[0].id


def invoice_body(**kw):
    return {
        "number": f"INV-{uuid.uuid4().hex[:8]}",
        "issue_date": "2026-02-01",
        "due_date": "2026-02-01",
        "amount_ex_gst_paise": 100000,
        "gst_paise": 18000,
        "reason": "first invoice",
        **kw,
    }


async def test_add_invoice_roles_and_gst_totals(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)

    def add(actor, body=None):
        return client.post(
            f"/customers/{cust}/invoices", json=body or invoice_body(), headers=H(actor)
        )

    assert (await add(world.south1)).status_code == 403  # out of scope
    r = await add(world.north1)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["total_paise"] == 118000 and out["status"] == "open" and out["version"] == 1

    versions = await rows(
        admin_engine, "SELECT version_no FROM invoice_versions WHERE invoice_id=:i", i=out["id"]
    )
    assert [v.version_no for v in versions] == [1]
    audit = await rows(
        admin_engine,
        "SELECT reason FROM audit_log WHERE org_id=:o AND action_key='invoice.add' AND entity_id=:e",
        o=world.org_id,
        e=out["id"],
    )
    assert len(audit) == 1 and audit[0].reason == "first invoice"

    dup = await add(world.north1, invoice_body(number=out["number"]))
    assert dup.status_code == 409


async def test_admin_edit_is_direct_tl_edit_needs_approval_user_denied(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices", json=invoice_body(), headers=H(world.admin)
        )
    ).json()

    assert (
        await client.patch(
            f"/invoices/{inv['id']}",
            json={"amount_ex_gst_paise": 200000, "gst_paise": 36000, "reason": "typo"},
            headers=H(world.north1),
        )
    ).status_code == 403  # User cannot edit

    r = await client.patch(
        f"/invoices/{inv['id']}",
        json={"amount_ex_gst_paise": 150000, "gst_paise": 27000, "reason": "price correction"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 202, r.text
    approval_id = r.json()["approval_id"]
    unchanged = await rows(
        admin_engine, "SELECT total_paise, version FROM invoices WHERE id=:i", i=inv["id"]
    )
    assert unchanged[0].total_paise == 118000 and unchanged[0].version == 1  # not applied yet

    dec = await client.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=H(world.admin),
    )
    assert dec.status_code == 200, dec.text
    after = await rows(
        admin_engine, "SELECT total_paise, version FROM invoices WHERE id=:i", i=inv["id"]
    )
    assert after[0].total_paise == 177000 and after[0].version == 2
    versions = await rows(
        admin_engine,
        "SELECT version_no FROM invoice_versions WHERE invoice_id=:i ORDER BY version_no",
        i=inv["id"],
    )
    assert [v.version_no for v in versions] == [1, 2]

    r2 = await client.patch(
        f"/invoices/{inv['id']}",
        json={"number": "INV-ADMIN", "reason": "admin direct edit"},
        headers=H(world.admin),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["number"] == "INV-ADMIN" and r2.json()["version"] == 3


async def test_void_is_admin_and_super_admin_only_and_stays_visible(client, world, admin_engine):
    did, owner = await win(client, world, admin_engine, owner=world.north1)
    cust = await get_customer_id(admin_engine, did)
    inv = (
        await client.post(
            f"/customers/{cust}/invoices", json=invoice_body(), headers=H(world.admin)
        )
    ).json()

    assert (
        await client.post(
            f"/invoices/{inv['id']}/void", json={"reason": "bad"}, headers=H(world.tl_north)
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/invoices/{inv['id']}/void", json={"reason": "bad"}, headers=H(world.north1)
        )
    ).status_code == 403

    r = await client.post(
        f"/invoices/{inv['id']}/void", json={"reason": "wrong amount"}, headers=H(world.admin)
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    still_there = await client.get(f"/invoices/{inv['id']}", headers=H(world.admin))
    assert still_there.status_code == 200 and still_there.json()["status"] == "void"

    r2 = await client.patch(
        f"/invoices/{inv['id']}", json={"number": "x", "reason": "no"}, headers=H(world.admin)
    )
    assert r2.status_code == 422
