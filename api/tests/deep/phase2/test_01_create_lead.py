# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 1: creating a lead (validation, atomicity, audit, role matrix)."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from tests.deep.phase2.support import (
    DROP,
    audit_rows,
    configure,
    due,
    headers,
    lead,
    q,
    q1,
)
from tests.helpers import dispatch_all


async def post(qc, actor, **kw):
    return await qc.post("/leads", json=lead(**kw), headers=headers(actor))


async def counts(engine, org_id, company):
    """How many rows exist for a company name: accounts, contacts, deals, pool entries."""
    acc = await q(
        engine, "SELECT id FROM accounts WHERE org_id=:o AND name=:n", o=org_id, n=company
    )
    ids = [r[0] for r in acc]
    if not ids:
        return {"accounts": 0, "contacts": 0, "deals": 0, "pool": 0, "audit": 0}
    contacts = await q1(engine, "SELECT count(*) FROM contacts WHERE account_id = ANY(:a)", a=ids)
    deals = await q(engine, "SELECT id FROM deals WHERE account_id = ANY(:a)", a=ids)
    did = [r[0] for r in deals]
    pool = await q1(engine, "SELECT count(*) FROM lead_pool_entries WHERE deal_id = ANY(:d)", d=did)
    return {
        "accounts": len(ids),
        "contacts": contacts,
        "deals": len(did),
        "pool": pool,
        "audit": 0,
    }


# ------------------------------------------------------------------------------ company name
@pytest.mark.parametrize(
    ("name", "expect"),
    [
        ("A", 201),
        ("B" * 200, 201),
        ("C" * 201, 422),
        ("", 422),
    ],
)
async def test_company_length_boundaries(qc, world, name, expect):
    await configure(qc, world)
    r = await post(qc, world.north1, company=name)
    assert r.status_code == expect, r.text
    if expect == 201:
        assert r.json()["company"] == name


@pytest.mark.parametrize(
    "name",
    [
        "अर्जुन ट्रेडर्स",  # Devanagari
        "தமிழ் டெக் லிமிடெட்",  # Tamil
        "Pizza \U0001f355 House \U0001f680",
        "<script>alert(1)</script> Traders",
        "Robert'); DROP TABLE accounts;-- Ltd",
        '"; SELECT pg_sleep(5); -- Co',
        '=HYPERLINK("http://evil","x") Corp',  # CSV injection
        "Zero​Width Co",
    ],
)
async def test_company_unicode_and_hostile_strings_round_trip(qc, admin_engine, world, name):
    await configure(qc, world)
    r = await post(qc, world.north1, company=name)
    assert r.status_code == 201, r.text
    assert r.json()["company"] == name  # stored and returned byte for byte, never executed
    again = await qc.get(f"/accounts/{r.json()['account_id']}", headers=headers(world.admin))
    assert again.json()["name"] == name
    assert (
        await q1(admin_engine, "SELECT count(*) FROM accounts WHERE org_id=:o", o=world.org_id) >= 1
    )


async def test_whitespace_collapsing_in_company_name(qc, world):
    await configure(qc, world)
    r = await post(qc, world.north1, company="  Spaced    Out \t Co  ")
    assert r.status_code == 201 and r.json()["company"] == "Spaced Out Co"


@pytest.mark.parametrize("field", ["company", "contact_name", "next_step_text"])
async def test_whitespace_only_required_fields_are_rejected(qc, world, field):
    await configure(qc, world)
    r = await post(qc, world.north1, **{field: "     "})
    assert r.status_code == 422, r.text


# ------------------------------------------------------------------------------ phone
@pytest.mark.parametrize(
    "phone",
    [
        "9876543210",
        "+91 98765 43210",
        "098765-43210",
        "+919876543210",
        "91 9876543210",
        "+91-98765-43210",
        "+91 22 2345 6789",  # landline with STD code
        "+1 415 555 2671",  # valid foreign number
    ],
)
async def test_valid_phone_forms_are_normalised_to_e164(qc, world, phone):
    await configure(qc, world)
    r = await post(qc, world.north1, phone=phone, email=DROP)
    assert r.status_code == 201, r.text
    stored = r.json()["contact"]["phone"]
    assert stored.startswith("+") and stored[1:].isdigit()
    if "415" not in phone:
        assert stored.startswith("+91")


@pytest.mark.parametrize(
    "phone",
    ["98765432", "987654321", "98765432101", "abcdefghij", "0000000000", "12-34", "+91 98765"],
)
async def test_invalid_phone_forms_are_rejected_next_to_the_field(qc, world, phone):
    await configure(qc, world)
    r = await post(qc, world.north1, phone=phone, email=DROP)
    assert r.status_code == 422, r.text
    assert r.json()["details"]["field"] == "phone"


async def test_phone_with_extension_is_not_silently_truncated(qc, world):
    await configure(qc, world)
    r = await post(qc, world.north1, phone="9876543210x12", email=DROP)
    assert r.status_code == 422


async def test_same_phone_in_different_formats_is_one_stored_value(qc, world):
    await configure(qc, world)
    a = await post(qc, world.north1, phone="098765-43210", email=DROP)
    b = await post(
        qc, world.north1, company="Different Name Entirely", phone="+91 98765 43210", email=DROP
    )
    assert a.status_code == 201
    assert b.status_code == 409 and b.json()["code"] == "duplicate_blocked"
    assert b.json()["details"]["matches"][0]["match_type"] == "phone"


# ------------------------------------------------------------------------------ email
@pytest.mark.parametrize(
    ("email", "stored"),
    [
        ("a@b.local", "a@b.local"),
        ("UPPER@CASE.COM", "upper@case.com"),
        ("a+tag@b.com", "a+tag@b.com"),
        ("user@bücher.de", "user@bücher.de"),  # IDN domain
        ("  padded@x.com  ", "padded@x.com"),
    ],
)
async def test_valid_email_forms(qc, world, email, stored):
    await configure(qc, world)
    r = await post(qc, world.north1, email=email)
    assert r.status_code == 201, r.text
    assert r.json()["contact"]["email"] == stored


@pytest.mark.parametrize(
    "email", ["a@b", "a@@b.com", "a b@c.com", "@b.com", "a@.com", "plain", "a@b.c om"]
)
async def test_invalid_email_forms(qc, world, email):
    await configure(qc, world)
    r = await post(qc, world.north1, email=email)
    assert r.status_code == 422, r.text
    assert r.json()["details"]["field"] == "email"


async def test_overlong_email_is_rejected(qc, world):
    await configure(qc, world)
    r = await post(qc, world.north1, email="a" * 250 + "@b.com")
    assert r.status_code == 422


@pytest.mark.parametrize("email", ["a@b.com.", "a@b..com"])
async def test_email_with_empty_domain_label_is_rejected(qc, world, email):
    await configure(qc, world)
    r = await post(qc, world.north1, email=email)
    assert r.status_code == 422


async def test_phone_or_email_is_required_and_both_are_allowed(qc, world):
    await configure(qc, world)
    neither = lead()
    neither.pop("email")
    r = await qc.post("/leads", json=neither, headers=headers(world.north1))
    assert r.status_code == 422 and r.json()["details"]["field"] == "phone"
    r = await post(qc, world.north1, email=DROP, phone="9876501234")
    assert r.status_code == 201
    r = await post(qc, world.north1, phone="9876501235", email="both@example.com")
    assert r.status_code == 201
    assert r.json()["contact"]["email"] == "both@example.com"
    assert r.json()["contact"]["phone"] == "+919876501235"
    r = await post(qc, world.north1, email="", phone="")
    assert r.status_code == 422


async def test_blank_phone_with_a_valid_email_is_treated_as_not_given(qc, world):
    """A form that sends phone='' next to a good email must still save (the UI sends null, but an
    API client may send an empty string)."""
    await configure(qc, world)
    assert (await post(qc, world.north1, phone="")).status_code == 201
    r = await post(qc, world.north1, phone="   ")  # documented: a blank string is 'not valid'
    assert r.status_code == 422 and r.json()["details"]["field"] == "phone"


# ------------------------------------------------------------------------------ website
@pytest.mark.parametrize(
    ("site", "domain"),
    [
        ("example.com", "example.com"),
        ("www.example.com", "example.com"),
        ("http://example.com", "example.com"),
        ("https://x.com/path?q=1", "x.com"),
        ("https://Example.COM:8080/", "example.com"),
        ("example.com/", "example.com"),
        ("  example.com  ", "example.com"),
        ("HTTPS://WWW.EXAMPLE.CO.IN/a/b", "example.co.in"),
    ],
)
async def test_valid_website_forms(qc, world, site, domain):
    await configure(qc, world)
    r = await post(qc, world.north1, website=site)
    assert r.status_code == 201, r.text
    acc = (await qc.get(f"/accounts/{r.json()['account_id']}", headers=headers(world.admin))).json()
    assert acc["domain"] == domain


@pytest.mark.parametrize(
    "site", ["javascript:alert(1)", "www.", "http://", "localhost", "example", "<b>x</b>.com"]
)
async def test_invalid_website_forms_are_rejected(qc, world, site):
    await configure(qc, world)
    r = await post(qc, world.north1, website=site)
    assert r.status_code == 422, r.text
    assert r.json()["details"]["field"] == "website"


@pytest.mark.parametrize(
    "site",
    [
        "ftp://example.com",
        "exa mple.com",
        "mailto:a@b.com",
        "JavaScript://example.com/%0aalert(1)",
        "192.168.1.1",
    ],
)
async def test_website_must_be_a_real_web_address(qc, world, site):
    await configure(qc, world)
    r = await post(qc, world.north1, website=site)
    assert r.status_code == 422, r.text


# ------------------------------------------------------------------------------ source, dates, steps
async def test_lead_source_must_be_in_the_list(qc, world):
    await configure(qc, world)
    for ok in ("Website", "Referral", "LinkedIn", "Event", "Cold outbound", "Inbound call"):
        assert (await post(qc, world.north1, lead_source=ok)).status_code == 201
    for bad in ("website", "Facebook", "", " Website", None):
        r = await post(qc, world.north1, lead_source=bad)
        assert r.status_code == 422, (bad, r.text)
    body = lead()
    body.pop("lead_source")
    assert (await qc.post("/leads", json=body, headers=headers(world.north1))).status_code == 422


async def test_next_step_text_length_boundaries(qc, world):
    await configure(qc, world)
    assert (await post(qc, world.north1, next_step_text="x")).status_code == 201
    assert (await post(qc, world.north1, next_step_text="y" * 500)).status_code == 201
    assert (await post(qc, world.north1, next_step_text="y" * 501)).status_code == 422
    assert (await post(qc, world.north1, next_step_text="")).status_code == 422


async def test_due_date_yesterday_today_tomorrow(qc, world):
    await configure(qc, world)
    assert (await post(qc, world.north1, next_step_due_date=due(-1))).status_code == 422
    assert (await post(qc, world.north1, next_step_due_date=due(0))).status_code == 201
    assert (await post(qc, world.north1, next_step_due_date=due(1))).status_code == 201
    for bad in ("2026-02-30", "tomorrow", "", "31/12/2026", 20261231):
        assert (await post(qc, world.north1, next_step_due_date=bad)).status_code == 422


@pytest.mark.parametrize(
    ("utc_instant", "due_date", "expect"),
    [
        # 18:29 UTC on 10 Jan is 23:59 IST on 10 Jan: the 10th is still today in IST.
        (datetime(2027, 1, 10, 18, 29, tzinfo=UTC), "2027-01-10", 201),
        (datetime(2027, 1, 10, 18, 29, tzinfo=UTC), "2027-01-09", 422),
        # 18:31 UTC on 10 Jan is 00:01 IST on 11 Jan: the 10th is already in the past in IST.
        (datetime(2027, 1, 10, 18, 31, tzinfo=UTC), "2027-01-10", 422),
        (datetime(2027, 1, 10, 18, 31, tzinfo=UTC), "2027-01-11", 201),
        # just before UTC midnight: IST is already the next day
        (datetime(2027, 1, 10, 23, 59, tzinfo=UTC), "2027-01-10", 422),
        (datetime(2027, 1, 10, 23, 59, tzinfo=UTC), "2027-01-11", 201),
        # just after UTC midnight
        (datetime(2027, 1, 11, 0, 1, tzinfo=UTC), "2027-01-11", 201),
    ],
)
async def test_due_date_is_compared_in_ist_not_utc(
    qc, world, monkeypatch, utc_instant, due_date, expect
):
    from app.modules.leads import service

    monkeypatch.setattr(service, "utc_now", lambda: utc_instant)
    await configure(qc, world)
    r = await post(qc, world.north1, next_step_due_date=due_date)
    assert r.status_code == expect, r.text


async def test_first_contact_date_is_optional_and_may_be_in_the_past(qc, world):
    await configure(qc, world)
    assert (await post(qc, world.north1, first_contact_date="2020-01-01")).status_code == 201
    assert (await post(qc, world.north1, first_contact_date=None)).status_code == 201
    assert (await post(qc, world.north1, first_contact_date="not-a-date")).status_code == 422


async def test_first_contact_date_in_the_future_is_rejected(qc, world):
    await configure(qc, world)
    r = await post(qc, world.north1, first_contact_date=due(30))
    assert r.status_code == 422


# ------------------------------------------------------------------------------ extra fields
@pytest.mark.parametrize(
    "extra",
    [
        # owner_id is a real field now (TL/Admin pick the owner); see test_assign_on_create.py
        {"status": "open"},
        {"team_id": str(uuid.uuid4())},
        {"generated_by": str(uuid.uuid4())},
        {"org_id": str(uuid.uuid4())},
        {"value_paise": 100},
        {"id": str(uuid.uuid4())},
        {"created_by": str(uuid.uuid4())},
        {"assignment_method": "manual"},
    ],
)
async def test_extra_json_fields_are_rejected(qc, admin_engine, world, extra):
    await configure(qc, world)
    before = await q1(admin_engine, "SELECT count(*) FROM deals WHERE org_id=:o", o=world.org_id)
    r = await post(qc, world.north1, **extra)
    assert r.status_code == 422, r.text
    assert (
        await q1(admin_engine, "SELECT count(*) FROM deals WHERE org_id=:o", o=world.org_id)
        == before
    )


async def test_wrong_content_types_and_bodies(qc, world):
    await configure(qc, world)
    h = headers(world.north1)
    r = await qc.post("/leads", content="company=x", headers={**h, "content-type": "text/plain"})
    assert r.status_code in (415, 422)
    r = await qc.post(
        "/leads", content="{not json", headers={**h, "content-type": "application/json"}
    )
    assert r.status_code == 422
    r = await qc.post("/leads", json=[1, 2], headers=h)
    assert r.status_code == 422
    r = await qc.post("/leads", json=None, headers=h)
    assert r.status_code == 422
    r = await qc.post("/leads", json=lead(company=12345), headers=h)
    assert r.status_code == 422
    r = await qc.post("/leads", json=lead(create_anyway="maybe"), headers=h)
    assert r.status_code == 422


async def test_enormous_payload_does_not_crash(qc, world):
    await configure(qc, world)
    r = await post(qc, world.north1, industry="I" * 2_000_000, city="C" * 1000)
    assert r.status_code < 500


async def test_industry_and_city_have_a_length_limit(qc, world):
    await configure(qc, world)
    r = await post(qc, world.north1, industry="I" * 5000, city="C" * 5000)
    assert r.status_code == 422


# ------------------------------------------------------------------------------ authentication
async def test_unauthenticated_and_forged_tokens(qc, world):
    body = lead()
    assert (await qc.post("/leads", json=body)).status_code == 401
    for bad in (
        "Bearer",
        "Bearer ",
        "Bearer garbage",
        "Basic abc",
        "bearer eyJhbGciOiJub25lIn0.e30.",
    ):
        r = await qc.post("/leads", json=body, headers={"Authorization": bad})
        assert r.status_code == 401, (bad, r.text)


# ------------------------------------------------------------------------------ roles
async def test_role_matrix_for_creating_a_lead(qc, admin_engine, world):
    await configure(qc, world)
    for actor in (world.north1, world.south1, world.tl_north, world.tl_south):
        r = await post(qc, actor)
        assert r.status_code == 201, (actor.name, r.text)
    r = await post(qc, world.admin)
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await post(qc, world.admin, reason="  ")
    assert r.status_code == 422
    r = await post(qc, world.admin, reason="entered for the sales head")
    assert r.status_code == 201
    audit = await audit_rows(admin_engine, r.json()["id"])
    assert ("lead.create", "ok", "deal", "entered for the sales head", "screen", "admin", None) in [
        tuple(a) for a in audit
    ]
    r = await post(qc, world.sa)
    assert r.status_code == 201
    r = await post(qc, world.sa, reason="because")
    assert r.status_code == 201
    denied = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='lead.create' AND outcome='denied'",
        o=world.org_id,
    )
    assert denied == 2  # the two Admin attempts without a reason


async def test_creator_is_generated_by_never_the_owner_unless_the_engine_picks_them(qc, world):
    # a rep's lead is held for the Team Leader of the rep's OWN team (default team is South here)
    await configure(qc, world, team=world.south_team)
    d = (await post(qc, world.north1)).json()
    assert d["generated_by"] == "Priya" and d["awaiting_team_leader"] is True
    assert d["owner"] is None and d["team_name"] == "Team North"
    # the creator cannot open the pending lead (scoping unchanged)
    r = await qc.get(f"/deals/{d['id']}", headers=headers(world.north1))
    assert r.status_code == 404
    # the leader's automatic choice then gives it to a North rep, still not "generated by" logic
    r = await qc.post(
        f"/pool/{d['id']}/decide", json={"action": "auto"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 200 and r.json()["owner"]["id"] in {
        str(world.north1.id),
        str(world.north2.id),
    }


async def test_deactivated_creator_token_stops_working(qc, admin_engine, world):
    await configure(qc, world)
    async with admin_engine.begin() as c:
        from sqlalchemy import text

        await c.execute(
            text("UPDATE users SET is_active=false WHERE id=:i"), {"i": world.north1.id}
        )
    r = await post(qc, world.north1)
    assert r.status_code == 401


# ------------------------------------------------------------------------------ audit and outbox
async def test_one_deal_audit_row_one_pooled_event_and_the_assignment_rows(qc, admin_engine, world):
    await configure(qc, world)
    d = (await post(qc, world.tl_north, company="Audit Probe Co")).json()  # a rep's lead is held
    deal_rows = [tuple(r)[:2] for r in await audit_rows(admin_engine, d["id"])]
    assert deal_rows.count(("lead.create", "ok")) == 1
    assert deal_rows.count(("lead.assigned", "ok")) == 1
    assert len(deal_rows) == 2
    ev = await q(
        admin_engine,
        "SELECT payload FROM outbox_events WHERE org_id=:o AND type='deal.pooled'",
        o=world.org_id,
    )
    assert [e[0]["deal_id"] for e in ev if e[0]["deal_id"] == d["id"]] == [d["id"]]
    # one row per record written, not one per request: account, contact, deal
    per_entity = await q(
        admin_engine,
        "SELECT entity_type, count(*) FROM audit_log WHERE org_id=:o AND action_key='lead.create' "
        "GROUP BY 1 ORDER BY 1",
        o=world.org_id,
    )
    assert {a: n for a, n in per_entity} == {"account": 1, "contact": 1, "deal": 1}
    origins = await q(
        admin_engine, "SELECT DISTINCT origin FROM audit_log WHERE org_id=:o", o=world.org_id
    )
    assert {o[0] for o in origins} <= {"screen", "system_job"}
    sys_rows = await q(
        admin_engine,
        "SELECT actor_role, origin, note FROM audit_log WHERE entity_id=:e AND action_key='lead.assigned'",
        e=d["id"],
    )
    assert sys_rows[0][1] == "system_job" and "Round-robin" in sys_rows[0][2]


async def test_override_and_create_anyway_audit_rows(qc, admin_engine, world):
    await configure(qc, world)
    first = (
        await post(qc, world.north1, company="Zephyr Foods Pvt", website="zephyr-foods.com")
    ).json()
    # create anyway (similar name only)
    r = await post(
        qc,
        world.north2,
        company="Zephyr Foods Pvt Ltd",
        create_anyway=True,
        reason="different firm",
    )
    assert r.status_code == 201
    keys = [tuple(x)[:2] for x in await audit_rows(admin_engine, r.json()["id"])]
    assert (
        keys.count(("duplicate.create_anyway", "ok")) == 1
        and keys.count(("lead.create", "ok")) == 1
    )
    # override a block (Admin, same domain)
    r2 = await post(
        qc,
        world.admin,
        company="Zephyr Again",
        website="https://www.zephyr-foods.com/",
        override_block=True,
        reason="new contact there",
    )
    assert r2.status_code == 201 and r2.json()["account_id"] == first["account_id"]
    keys2 = [tuple(x)[:2] for x in await audit_rows(admin_engine, r2.json()["id"])]
    assert keys2.count(("duplicate.override_or_merge", "ok")) == 1
    rows = await q(
        admin_engine,
        "SELECT match_type, in_scope, reason, by_user_id FROM duplicate_overrides WHERE deal_id=:d",
        d=uuid.UUID(r2.json()["id"]),
    )
    assert rows and all(x[2] == "new contact there" and x[3] == world.admin.id for x in rows)
    # no new account was created by the override
    n = await q1(
        admin_engine,
        "SELECT count(*) FROM accounts WHERE org_id=:o AND name='Zephyr Again'",
        o=world.org_id,
    )
    assert n == 0


# ------------------------------------------------------------------------------ atomicity
async def _assert_nothing_left(admin_engine, world, company):
    c = await counts(admin_engine, world.org_id, company)
    assert c == {"accounts": 0, "contacts": 0, "deals": 0, "pool": 0, "audit": 0}, c
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE org_id=:o AND outcome='ok' AND new_value->>'company'=:c",
            o=world.org_id,
            c=company,
        )
        == 0
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM outbox_events WHERE org_id=:o AND type='deal.pooled'",
            o=world.org_id,
        )
        == 0
    )


async def test_failure_inside_the_transaction_leaves_nothing_behind(
    qc, admin_engine, world, monkeypatch
):
    from app.modules.leads import service

    await configure(qc, world)

    async def boom(*a, **kw):
        raise RuntimeError("injected failure after the account, contact and deal were written")

    monkeypatch.setattr(service, "start_deal", boom)
    r = await post(qc, world.north1, company="Atomic Probe One")
    assert r.status_code == 500
    await _assert_nothing_left(admin_engine, world, "Atomic Probe One")


async def test_missing_published_pipeline_leaves_nothing_behind(qc, admin_engine, world):
    from sqlalchemy import text

    await configure(qc, world)
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE pipeline_versions SET status='archived' WHERE status='published' AND "
                "pipeline_id IN (SELECT id FROM pipelines WHERE org_id=:o)"
            ),
            {"o": world.org_id},
        )
    r = await post(qc, world.north1, company="Atomic Probe Two")
    assert r.status_code == 409, r.text
    await _assert_nothing_left(admin_engine, world, "Atomic Probe Two")


async def test_failure_in_the_audit_step_rolls_everything_back(
    qc, admin_engine, world, monkeypatch
):
    from app.platform.uow import UnitOfWork

    await configure(qc, world)
    real = UnitOfWork.audit

    async def flaky(self, key, *a, **kw):
        if key == "duplicate.create_anyway" or (key == "lead.create" and a and a[0] == "deal"):
            raise RuntimeError("injected audit failure")
        return await real(self, key, *a, **kw)

    monkeypatch.setattr(UnitOfWork, "audit", flaky)
    r = await post(qc, world.north1, company="Atomic Probe Three")
    assert r.status_code == 500
    await _assert_nothing_left(admin_engine, world, "Atomic Probe Three")


async def test_inline_engine_failure_still_creates_the_lead_and_the_outbox_assigns_it(
    qc, world, monkeypatch
):
    from app.modules.leads import router

    await configure(qc, world)

    async def boom(*a, **kw):
        raise RuntimeError("engine down")

    monkeypatch.setattr(router, "assign_pooled", boom)
    r = await post(qc, world.tl_north)
    assert r.status_code == 201 and r.json()["status"] == "pooled"
    monkeypatch.undo()
    await dispatch_all()
    got = (await qc.get(f"/deals/{r.json()['id']}", headers=headers(world.admin))).json()
    assert got["status"] == "open" and got["owner"] is not None


async def test_creating_the_same_company_twice_at_once_gives_one_winner_and_no_500(
    qc, admin_engine, world
):
    await configure(qc, world)
    body = lead("Race Condition Foods", website="race-foods.example")
    rs = await asyncio.gather(
        *(
            qc.post(
                "/leads", json={**body, "email": f"r{i}@example.com"}, headers=headers(world.north1)
            )
            for i in range(12)
        )
    )
    codes = sorted(r.status_code for r in rs)
    assert 500 not in codes, codes
    assert codes.count(201) == 1, codes
    assert set(codes) <= {201, 409}, codes
    n = await q1(
        admin_engine,
        "SELECT count(*) FROM accounts WHERE org_id=:o AND domain='race-foods.example'",
        o=world.org_id,
    )
    assert n == 1


async def test_creating_the_same_phone_twice_at_once_does_not_duplicate_the_contact(
    qc, admin_engine, world
):
    await configure(qc, world)
    rs = await asyncio.gather(
        *(
            qc.post(
                "/leads",
                json=lead(f"Phone Race {i} Industries Unique{i}", email=DROP, phone="9811122233"),
                headers=headers(world.north1),
            )
            for i in range(6)
        )
    )
    codes = sorted(r.status_code for r in rs)
    assert 500 not in codes, codes
    accounts_with_phone = await q1(
        admin_engine,
        "SELECT count(DISTINCT account_id) FROM contacts WHERE org_id=:o AND phone_e164='+919811122233'",
        o=world.org_id,
    )
    assert accounts_with_phone == 1, (
        f"{accounts_with_phone} accounts share one phone number: {codes}"
    )
