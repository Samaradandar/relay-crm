# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 7: companies, contacts and search."""

import uuid

import pytest

from tests.deep.phase2.support import (
    configure,
    create,
    headers,
    make_world,
    q,
    q1,
)


def acct(qc, actor, **kw):
    body = {"name": uuid.uuid4().hex + " Holdings", **kw}
    return qc.post("/accounts", json=body, headers=headers(actor))


async def new_account(qc, actor=None, **kw):
    r = await acct(qc, actor, **kw)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------------------ accounts
async def test_account_create_roles_and_reason_rules(qc, admin_engine, world):
    await configure(qc, world)
    for actor in (world.north1, world.tl_south, world.sa):
        r = await acct(qc, actor, website="https://" + uuid.uuid4().hex[:8] + ".example")
        assert r.status_code == 201, (actor.name, r.text)
    r = await acct(qc, world.admin)
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    assert (await acct(qc, world.admin, reason="entered for sales head")).status_code == 201
    assert (await qc.post("/accounts", json={"name": "x"})).status_code == 401
    assert (await acct(qc, world.north1, owner_id=str(uuid.uuid4()))).status_code == 422
    assert (
        await qc.post("/accounts", json={"name": ""}, headers=headers(world.north1))
    ).status_code == 422
    assert (
        await qc.post("/accounts", json={"name": "N" * 201}, headers=headers(world.north1))
    ).status_code == 422
    assert (
        await qc.post("/accounts", json={"name": "N" * 200}, headers=headers(world.north1))
    ).status_code == 201
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE org_id=:o AND entity_type='account' AND action_key='lead.create' AND actor_role='admin'",
            o=world.org_id,
        )
        == 1
    )


async def test_the_admins_reason_for_creating_an_account_is_recorded(qc, admin_engine, world):
    await configure(qc, world)
    assert (await acct(qc, world.admin, reason="entered for sales head")).status_code == 201
    rows = await q(
        admin_engine,
        "SELECT reason FROM audit_log WHERE org_id=:o AND entity_type='account' AND actor_role='admin' AND outcome='ok'",
        o=world.org_id,
    )
    assert [r[0] for r in rows] == ["entered for sales head"]


async def test_admin_account_create_needs_a_reason_even_with_create_anyway(qc, world):
    await configure(qc, world)
    r = await qc.post(
        "/accounts",
        json={"name": "No Reason Co", "create_anyway": True},
        headers=headers(world.admin),
    )
    assert r.status_code == 422


@pytest.mark.parametrize("site", ["javascript:alert(1)", "http://", "<b>x</b>.com", "not a site"])
async def test_account_website_is_validated_like_on_the_lead_form(qc, world, site):
    await configure(qc, world)
    r = await acct(qc, world.north1, website=site)
    assert r.status_code == 422, r.text


async def test_account_patch_website_validation_and_domain_conflicts(qc, world):
    await configure(qc, world)
    owner_lead = await create(
        qc, world.north1, company="Patch Subject Co", website="patch-subject.example"
    )
    owner = next(a for a in world.all_actors if str(a.id) == owner_lead["owner"]["id"])
    other = await create(
        qc, world.north1, company="Another Distinct Name", website="another-distinct.example"
    )
    h = headers(owner)
    r = await qc.patch(
        f"/accounts/{owner_lead['account_id']}",
        json={"website": "https://www.another-distinct.example/x"},
        headers=headers(world.admin),
    )
    assert r.status_code == 422  # admin needs a reason first
    r = await qc.patch(
        f"/accounts/{owner_lead['account_id']}",
        json={"website": "https://www.another-distinct.example/x"},
        headers=h,
    )
    assert r.status_code == 409 and "already uses" in r.json()["message"]
    r = await qc.patch(
        f"/accounts/{owner_lead['account_id']}",
        json={"website": "new-site.example", "industry": "Retail", "city": "Pune"},
        headers=h,
    )
    assert (
        r.status_code == 200
        and r.json()["domain"] == "new-site.example"
        and r.json()["city"] == "Pune"
    )
    r = await qc.patch(f"/accounts/{owner_lead['account_id']}", json={"website": None}, headers=h)
    assert r.status_code == 200 and r.json()["domain"] is None and r.json()["website"] is None
    assert (
        await qc.patch(f"/accounts/{owner_lead['account_id']}", json={"name": " "}, headers=h)
    ).status_code in (200, 422)
    assert (
        await qc.patch(
            f"/accounts/{owner_lead['account_id']}", json={"id": str(uuid.uuid4())}, headers=h
        )
    ).status_code == 422
    assert (
        await qc.patch(
            f"/accounts/{owner_lead['account_id']}",
            json={"merged_into_id": other["account_id"]},
            headers=h,
        )
    ).status_code == 422
    assert other["id"]


async def test_account_patch_junk_website_is_refused(qc, world):
    await configure(qc, world)
    d = await create(qc, world.north1, company="Junk Site Co", website="junk-site.example")
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    r = await qc.patch(
        f"/accounts/{d['account_id']}",
        json={"website": "javascript:alert(1)"},
        headers=headers(owner),
    )
    assert r.status_code == 422


async def test_account_patch_roles_and_scope_and_audit(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1, company="Scoped Co", website="scoped-co.example")
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    url = f"/accounts/{d['account_id']}"
    for actor in (world.south1, world.tl_south):
        assert (
            await qc.patch(url, json={"city": "Delhi"}, headers=headers(actor))
        ).status_code == 404
    other_rep = world.north2 if owner.id == world.north1.id else world.north1
    assert (
        await qc.patch(url, json={"city": "Delhi"}, headers=headers(world.tl_north))
    ).status_code == 200
    assert (
        await qc.patch(url, json={"city": "Goa"}, headers=headers(world.admin))
    ).status_code == 422
    assert (
        await qc.patch(url, json={"city": "Goa", "reason": "fix"}, headers=headers(world.admin))
    ).status_code == 200
    assert (
        await qc.patch(url, json={"city": "Kochi"}, headers=headers(world.sa))
    ).status_code == 200
    assert other_rep
    rows = await q(
        admin_engine,
        "SELECT old_value->>'city', new_value->>'city', reason FROM audit_log WHERE entity_id=:e AND action_key='deal.edit' AND outcome='ok' ORDER BY seq",
        e=d["account_id"],
    )
    assert [tuple(x) for x in rows] == [
        (None, "Delhi", None),
        ("Delhi", "Goa", "fix"),
        ("Goa", "Kochi", None),
    ]
    other = await make_world(admin_engine)
    assert (
        await qc.patch(url, json={"city": "x", "reason": "x"}, headers=headers(other.admin))
    ).status_code == 404
    assert (await qc.get(url, headers=headers(other.admin))).status_code == 404


async def test_a_standalone_account_is_visible_only_to_its_creator_and_admins(qc, world):
    await configure(qc, world)
    a = await new_account(qc, world.north1)
    url = f"/accounts/{a['id']}"
    assert (await qc.get(url, headers=headers(world.north1))).status_code == 200
    for actor in (world.north2, world.tl_north, world.south1, world.tl_south):
        assert (await qc.get(url, headers=headers(actor))).status_code == 404, actor.name
        assert (
            a["id"]
            not in (await qc.get("/accounts", params={"q": a["name"]}, headers=headers(actor))).text
        )
    for actor in (world.admin, world.sa):
        assert (await qc.get(url, headers=headers(actor))).status_code == 200


async def test_accounts_endpoints_have_no_delete(qc, world):
    await configure(qc, world)
    a = await new_account(qc, world.sa)
    for method in ("delete", "put"):
        r = await getattr(qc, method)(f"/accounts/{a['id']}", headers=headers(world.sa))
        assert r.status_code in (404, 405)
    assert (
        await qc.delete("/contacts/" + str(uuid.uuid4()), headers=headers(world.sa))
    ).status_code in (404, 405)


async def test_nothing_is_hard_deleted_by_deleting_a_deal(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    assert (
        await qc.post(
            f"/deals/{d['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
        )
    ).status_code == 204
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM deals WHERE id=:i AND deleted_at IS NOT NULL",
            i=uuid.UUID(d["id"]),
        )
        == 1
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM accounts WHERE id=:i AND deleted_at IS NULL",
            i=uuid.UUID(d["account_id"]),
        )
        == 1
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM contacts WHERE account_id=:i",
            i=uuid.UUID(d["account_id"]),
        )
        == 1
    )
    deleted = (await qc.get("/deleted/deals", headers=headers(world.admin))).json()
    assert [x["id"] for x in deleted] == [d["id"]]
    assert (await qc.get(f"/accounts/{d['account_id']}", headers=headers(world.admin))).json()[
        "deals"
    ] == []
    r = await qc.post(
        f"/deals/{d['id']}/restore", json={"reason": "oops"}, headers=headers(world.admin)
    )
    assert r.status_code == 200


async def test_deleting_and_restoring_a_waiting_lead_keeps_one_pool_entry(qc, admin_engine, world):
    from tests.deep.phase2.support import invariant_violations, put_settings

    await put_settings(qc, world.admin, world, default_cap=0)
    d = await create(qc, world.north1)
    assert (
        await qc.post(
            f"/deals/{d['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
        )
    ).status_code == 204
    rows = await q(
        admin_engine,
        "SELECT left_at IS NOT NULL, left_reason FROM lead_pool_entries WHERE deal_id=:d",
        d=uuid.UUID(d["id"]),
    )
    assert [tuple(x) for x in rows] == [(True, "deleted")]
    assert (await qc.get("/pool", headers=headers(world.admin))).json() == []
    assert (
        await qc.post(
            f"/deals/{d['id']}/restore", json={"reason": "oops"}, headers=headers(world.admin)
        )
    ).status_code == 200
    assert len((await qc.get("/pool", headers=headers(world.admin))).json()) == 1
    assert not await invariant_violations(admin_engine, world.org_id)


# ------------------------------------------------------------------------------ contacts
async def new_contact(qc, actor, account_id, expect=201, **kw):
    body = {
        "account_id": account_id,
        "name": "Meera",
        "email": f"m{uuid.uuid4().hex[:6]}@acct.example",
        **kw,
    }
    r = await qc.post("/contacts", json=body, headers=headers(actor))
    assert r.status_code == expect, r.text
    return r.json()


async def test_contact_create_validation(qc, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    aid = d["account_id"]
    h = headers(owner)
    ok = await new_contact(
        qc, owner, aid, phone="+91 98765 43210", role="CEO", is_decision_maker=True
    )
    assert (
        ok["phone"] == "+919876543210" and ok["is_decision_maker"] is True and ok["role"] == "CEO"
    )
    await new_contact(qc, owner, aid, 409, email=None)  # neither phone nor email
    await new_contact(
        qc, owner, aid, 422, email="not-an-email"
    )  # BUG-P2-31 fixed: a bad value is refused
    assert (
        await qc.post(
            "/contacts", json={"account_id": aid, "name": "", "email": "a@b.co"}, headers=h
        )
    ).status_code == 422
    assert (
        await qc.post(
            "/contacts", json={"account_id": aid, "name": "N" * 201, "email": "a@b.co"}, headers=h
        )
    ).status_code == 422
    assert (
        await qc.post(
            "/contacts",
            json={"account_id": aid, "name": "x", "email": "a@b.co", "id": str(uuid.uuid4())},
            headers=h,
        )
    ).status_code == 422
    assert (
        await qc.post(
            "/contacts", json={"account_id": "nope", "name": "x", "email": "a@b.co"}, headers=h
        )
    ).status_code == 422
    assert (
        await qc.post("/contacts", json={"account_id": aid, "name": "x", "email": "a@b.co"})
    ).status_code == 401


@pytest.mark.parametrize("bad", [{"phone": "12345"}, {"phone": "abcdefghij"}])
async def test_contact_with_one_good_and_one_bad_channel_is_refused(qc, world, bad):
    await configure(qc, world)
    d = await create(qc, world.north1)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    r = await qc.post(
        "/contacts",
        json={"account_id": d["account_id"], "name": "Meera", "email": "meera@acct.example", **bad},
        headers=headers(owner),
    )
    assert r.status_code in (409, 422), r.text


async def test_contact_patch_rules(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1, phone="9876500001", email="first@acct.example")
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    cid = d["contact"]["id"]
    h = headers(owner)
    url = f"/contacts/{cid}"
    assert (await qc.patch(url, json={"phone": "garbage"}, headers=h)).status_code == 422
    assert (await qc.patch(url, json={"email": "garbage"}, headers=h)).status_code == 422
    assert (await qc.patch(url, json={"name": ""}, headers=h)).status_code == 422
    r = await qc.patch(url, json={"phone": None}, headers=h)
    assert (
        r.status_code == 200
        and r.json()["phone"] is None
        and r.json()["email"] == "first@acct.example"
    )
    assert (
        await qc.patch(url, json={"email": None}, headers=h)
    ).status_code == 409  # would leave no way to reach them
    r = await qc.patch(
        url,
        json={"is_decision_maker": True, "role": " Owner ", "email": "NEW@Acct.Example"},
        headers=h,
    )
    assert (
        r.json()["is_decision_maker"] is True
        and r.json()["role"] == "Owner"
        and r.json()["email"] == "new@acct.example"
    )
    r = await qc.patch(url, json={"is_decision_maker": False}, headers=h)
    assert r.json()["is_decision_maker"] is False
    assert (
        await qc.patch(url, json={"account_id": str(uuid.uuid4())}, headers=h)
    ).status_code == 422
    for actor in (world.south1, world.tl_south):
        assert (await qc.patch(url, json={"role": "x"}, headers=headers(actor))).status_code == 404
    assert (
        await qc.patch(url, json={"role": "x"}, headers=headers(world.admin))
    ).status_code == 422
    assert (
        await qc.patch(url, json={"role": "x", "reason": "fix"}, headers=headers(world.admin))
    ).status_code == 200
    assert (
        await qc.patch(f"/contacts/{uuid.uuid4()}", json={"role": "x"}, headers=h)
    ).status_code == 404
    other = await make_world(admin_engine)
    assert (
        await qc.patch(url, json={"role": "x", "reason": "y"}, headers=headers(other.admin))
    ).status_code == 404
    new = await new_contact(qc, owner, d["account_id"])
    assert new["account_id"] == d["account_id"]
    rows = await q(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE entity_id=:e AND action_key='deal.edit' AND outcome='ok'",
        e=cid,
    )
    assert rows[0][0] == 4


async def test_contact_create_for_accounts_out_of_scope_or_in_another_org(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    other = await make_world(admin_engine)
    await new_contact(qc, world.south1, d["account_id"], 404)
    await new_contact(qc, world.tl_south, d["account_id"], 404)
    await new_contact(qc, other.admin, d["account_id"], 404, reason="x")
    await new_contact(qc, world.north1, str(uuid.uuid4()), 404)
    await new_contact(qc, world.admin, d["account_id"], 422)  # no reason
    await new_contact(qc, world.admin, d["account_id"], 201, reason="added by sales head")


async def test_contact_writes_leave_exactly_the_documented_audit_rows(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    before = await q1(
        admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id
    )
    c = await new_contact(qc, owner, d["account_id"])
    assert (
        await q1(admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id)
        == before + 1
    )  # a rep: one row
    before = await q1(
        admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id
    )
    await new_contact(qc, world.admin, d["account_id"], reason="why")
    assert (
        await q1(admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id)
        == before + 2
    )  # an Admin: contact + account note
    assert c["id"]


async def test_there_is_no_contact_list_endpoint(qc, world):
    """GAP: the Phase 2 prompt lists GET /contacts and GET /contacts/{id}; they do not exist.
    Contacts come with the account page."""
    await configure(qc, world)
    assert (await qc.get("/contacts", headers=headers(world.admin))).status_code in (404, 405)
    assert (
        await qc.get(f"/contacts/{uuid.uuid4()}", headers=headers(world.admin))
    ).status_code in (404, 405)


# ------------------------------------------------------------------------------ search
async def test_account_search_scope_ranking_and_hostile_input(qc, admin_engine, world):
    await configure(qc, world)
    mine = await create(qc, world.tl_north, company="Sunrise Textiles Pvt Ltd")
    owner = next(a for a in world.all_actors if str(a.id) == mine["owner"]["id"])
    await configure(qc, world, team=world.south_team)
    theirs = await create(
        qc, world.tl_north, company="Sunrise Textile Mills", create_anyway=True, reason="other firm"
    )
    away = next(a for a in world.all_actors if str(a.id) == theirs["owner"]["id"])
    h = headers(owner)
    got = (await qc.get("/accounts", params={"q": "sunrise"}, headers=h)).json()
    assert [a["id"] for a in got] == [mine["account_id"]]
    assert {
        a["id"]
        for a in (await qc.get("/accounts", params={"q": "sunrise"}, headers=headers(away))).json()
    } == {theirs["account_id"]}
    assert {
        a["id"]
        for a in (
            await qc.get("/accounts", params={"q": "sunrise"}, headers=headers(world.admin))
        ).json()
    } == {mine["account_id"], theirs["account_id"]}
    assert [
        a["id"]
        for a in (await qc.get("/accounts", params={"q": "SUNRISE TEXTILES"}, headers=h)).json()
    ] == [mine["account_id"]]
    assert [
        a["id"]
        for a in (await qc.get("/accounts", params={"q": "sunrse textils"}, headers=h)).json()
    ] == [mine["account_id"]]  # a typo still finds it
    assert (await qc.get("/accounts", headers=h)).status_code == 200
    for nasty in (
        "'; DROP TABLE accounts; --",
        "%",
        "_",
        "\\",
        "<script>alert(1)</script>",
        "अ" * 3000,
        "a%b",
        " ",
    ):
        r = await qc.get("/accounts", params={"q": nasty}, headers=h)
        assert r.status_code == 200, (nasty[:20], r.status_code)
        assert all(a["id"] in {mine["account_id"], mine["account_id"]} for a in r.json())
    assert (
        await q1(admin_engine, "SELECT count(*) FROM accounts WHERE org_id=:o", o=world.org_id) == 2
    )
    assert (await qc.get("/accounts", params={"q": "sunrise"})).status_code == 401
    other = await make_world(admin_engine)
    assert (
        await qc.get("/accounts", params={"q": "sunrise"}, headers=headers(other.admin))
    ).json() == []


async def test_account_search_is_capped_at_25_without_paging(qc, admin_engine, world):
    """RISK-P2-06: /accounts returns at most 25 rows and has no limit/offset parameters."""
    await configure(qc, world)
    from sqlalchemy import text

    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO accounts (org_id, name, created_by) SELECT :o, 'Bulk Account ' || g, :u FROM generate_series(1,40) g"
            ),
            {"o": world.org_id, "u": world.sa.id},
        )
    got = (await qc.get("/accounts", headers=headers(world.admin))).json()
    assert len(got) == 25
    assert (
        len(
            (
                await qc.get(
                    "/accounts", params={"limit": 100, "offset": 25}, headers=headers(world.admin)
                )
            ).json()
        )
        == 25
    )  # ignored


async def test_merged_and_deleted_accounts_are_hidden_from_search(qc, admin_engine, world):
    from sqlalchemy import text

    await configure(qc, world)
    a = await create(qc, world.north1, company="Hidden Heron Works")
    await create(qc, world.north1, company="Visible Vulture Works")
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE accounts SET deleted_at=now() WHERE id=:i"), {"i": a["account_id"]}
        )
    names = {
        x["name"]
        for x in (
            await qc.get("/accounts", params={"q": "works"}, headers=headers(world.admin))
        ).json()
    }
    assert names == {"Visible Vulture Works"}
    assert (
        await qc.get(f"/accounts/{a['account_id']}", headers=headers(world.admin))
    ).status_code == 404


async def test_account_page_and_timeline_roles(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(qc, world.north1, company="Timeline Probe Co")
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    for actor in (owner, world.tl_north, world.admin, world.sa):
        r = await qc.get(f"/accounts/{d['account_id']}/timeline", headers=headers(actor))
        assert r.status_code == 200, actor.name
    for actor in (world.south1, world.tl_south):
        assert (
            await qc.get(f"/accounts/{d['account_id']}/timeline", headers=headers(actor))
        ).status_code == 404
    assert (await qc.get(f"/accounts/{d['account_id']}/timeline")).status_code == 401
    assert (await qc.get("/accounts/not-a-uuid", headers=headers(world.admin))).status_code == 422
    other = await make_world(admin_engine)
    assert (
        await qc.get(f"/accounts/{d['account_id']}/timeline", headers=headers(other.admin))
    ).status_code == 404
    assert (await qc.get(f"/accounts/{d['account_id']}/merge")).status_code in (404, 405)


async def test_deals_listing_limits_and_filters(qc, world):
    await configure(qc, world)
    for _ in range(3):
        await create(qc, world.north1)
    h = headers(world.admin)
    assert len((await qc.get("/deals", params={"limit": 2}, headers=h)).json()) == 2
    assert (await qc.get("/deals", params={"limit": 0}, headers=h)).json() == []
    assert len((await qc.get("/deals", params={"limit": 10_000}, headers=h)).json()) == 3
    assert (await qc.get("/deals", params={"limit": "x"}, headers=h)).status_code == 422
    assert (await qc.get("/deals", params={"owner_id": "x"}, headers=h)).status_code == 422
    assert (
        await qc.get("/deals", params={"status": "'; DROP TABLE deals;--"}, headers=h)
    ).json() == []
    assert (await qc.get("/deals", params={"lead_source": "<script>"}, headers=h)).json() == []


async def test_deals_listing_with_a_negative_limit(qc, world):
    await configure(qc, world)
    r = await qc.get("/deals", params={"limit": -1}, headers=headers(world.admin))
    assert r.status_code < 500
