# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P4-04: proposal template, drafting, editing, PDF, email, status, versions."""

from sqlalchemy import text

from app.platform.files import storage as storage_module
from app.platform.notifications import email as email_module
from tests.deep.phase1.kit import FakeStorage
from tests.deep.phase3.p3 import mkdeal
from tests.deep.phase3.test_05_value_discounts import add_line, product
from tests.helpers import headers as H


async def rows(eng, sql, **kw):
    async with eng.connect() as c:
        return (await c.execute(text(sql), kw)).all()


async def put_template(client, actor, name="Standard", body=None, terms="T&C apply.", reason="qa"):
    body = body or "<p>Dear {{company}}, your plan is {{plan}} at {{value_paise}} paise.</p>"
    return await client.put(
        "/settings/proposal-template",
        json={"name": name, "body": body, "terms_html": terms, "reason": reason},
        headers=H(actor),
    )


async def test_template_versions_and_admin_only(client, world):
    assert (await put_template(client, world.north1)).status_code == 403
    r1 = await put_template(client, world.admin)
    assert r1.status_code == 200 and r1.json()["version"] == 1
    r2 = await put_template(client, world.admin, body="<p>v2 {{company}}</p>")
    assert r2.status_code == 200 and r2.json()["version"] == 2
    cur = await client.get("/settings/proposal-template", headers=H(world.north1))
    assert cur.json()["version"] == 2 and cur.json()["active"] is True


async def setup_deal_with_line(client, world, eng, pricing_model="per_minute"):
    did, owner = await mkdeal(client, world, eng, owner=world.north1)
    prod = await product(client, world, f"Pack-{did}", 150, 100, unit="per_minute")
    await add_line(client, owner, did, prod["id"], 100)
    return did, owner


async def test_draft_without_a_template_is_refused(client, world, admin_engine):
    did, owner = await setup_deal_with_line(client, world, admin_engine)
    r = await client.post(f"/deals/{did}/proposals", headers=H(owner))
    assert r.status_code == 404


async def test_draft_freezes_catalog_values_and_edit_and_send(client, world, admin_engine):
    await put_template(client, world.admin)
    did, owner = await setup_deal_with_line(client, world, admin_engine)
    email_module.set_backend(email_module.MemoryBackend())
    storage_module.set_storage(FakeStorage())
    try:
        r = await client.post(f"/deals/{did}/proposals", headers=H(owner))
        assert r.status_code == 201, r.text
        prop = r.json()
        assert prop["status"] == "draft" and prop["version_no"] == 1
        assert prop["content"]["value_paise"] == 15000
        assert len(prop["items"]) == 1

        patched = await client.patch(
            f"/proposals/{prop['id']}", json={"content": {"scope": "Updated scope"}},
            headers=H(owner),
        )  # fmt: skip
        assert patched.status_code == 200
        assert patched.json()["content"]["scope"] == "Updated scope"

        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE products SET list_price_paise = 9999 WHERE name LIKE :n"),
                {"n": f"Pack-{did}%"},
            )
        reloaded = await client.get(f"/proposals/{prop['id']}", headers=H(owner))
        assert reloaded.json()["content"]["value_paise"] == 15000  # unaffected by the catalog edit

        contact_email = "accounts@acme.test"
        async with admin_engine.begin() as c:
            acc = (
                await c.execute(text("SELECT account_id FROM deals WHERE id=:d"), {"d": did})
            ).scalar()
            await c.execute(
                text(
                    "INSERT INTO contacts (org_id, account_id, name, email) VALUES (:o,:a,'Billing',:e)"
                ),
                {"o": world.org_id, "a": acc, "e": contact_email},
            )

        sent = await client.post(
            f"/proposals/{prop['id']}/send",
            json={"to": contact_email, "reason": "sending"},
            headers=H(owner),
        )
        assert sent.status_code == 200, sent.text
        out = sent.json()
        assert out["status"] == "sent" and out["sent_to"] == contact_email
        assert out["pdf_file_id"] is not None

        backend = email_module.get_backend()
        assert any(m[0] == contact_email for m in backend.sent)
        assert backend.attachments[-1][0][0] == "proposal.pdf"

        docs = await rows(
            admin_engine, "SELECT kind, status FROM documents WHERE deal_id=:d", d=did
        )
        assert any(d.kind == "proposal" and d.status == "sent" for d in docs)
    finally:
        email_module.set_backend(None)
        storage_module.set_storage(None)


async def test_pending_discount_blocks_sending(client, world, admin_engine):
    await put_template(client, world.admin)
    did, owner = await setup_deal_with_line(client, world, admin_engine)
    prop = (await client.post(f"/deals/{did}/proposals", headers=H(owner))).json()
    lines = (await client.get(f"/deals/{did}/lines", headers=H(owner))).json()
    line_id = lines["lines"][0]["id"]
    await client.patch(
        f"/deals/{did}/lines/{line_id}",
        json={"discount_pct": "50", "reason": "big ask"},
        headers=H(owner),
    )
    r = await client.post(f"/proposals/{prop['id']}/send", json={"reason": "go"}, headers=H(owner))
    assert r.status_code == 422


async def test_status_transitions_and_feedback_fills_deal_data(client, world, admin_engine):
    await put_template(client, world.admin)
    did, owner = await setup_deal_with_line(client, world, admin_engine)
    email_module.set_backend(email_module.MemoryBackend())
    storage_module.set_storage(FakeStorage())
    try:
        async with admin_engine.begin() as c:
            acc = (
                await c.execute(text("SELECT account_id FROM deals WHERE id=:d"), {"d": did})
            ).scalar()
            await c.execute(
                text(
                    "INSERT INTO contacts (org_id, account_id, name, email) VALUES (:o,:a,'B',:e)"
                ),
                {"o": world.org_id, "a": acc, "e": "b@example.com"},
            )
        prop = (await client.post(f"/deals/{did}/proposals", headers=H(owner))).json()
        await client.post(f"/proposals/{prop['id']}/send", json={"reason": "go"}, headers=H(owner))
        r = await client.post(
            f"/proposals/{prop['id']}/status",
            json={
                "status": "changes_requested",
                "feedback": "Please add more minutes",
                "reason": "fb",
            },
            headers=H(owner),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "changes_requested"
        data = await rows(admin_engine, "SELECT data FROM deals WHERE id=:d", d=did)
        assert data[0].data["feedback"] == "Please add more minutes"

        prop2 = (await client.post(f"/deals/{did}/proposals", headers=H(owner))).json()
        assert prop2["version_no"] == 2
        versions = await client.get(f"/deals/{did}/proposals", headers=H(owner))
        assert [v["version_no"] for v in versions.json()] == [1, 2]
    finally:
        email_module.set_backend(None)
        storage_module.set_storage(None)
