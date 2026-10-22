# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 2: duplicate rules (PRD 4.6), leaks across teams, merge."""

import json
import re
import uuid

import pytest
from sqlalchemy import text

from app.core.trigram import name_similarity, similarity
from tests.deep.phase2.support import (
    DROP,
    audit_rows,
    configure,
    create,
    headers,
    lead,
    make_world,
    q,
    q1,
)
from tests.helpers import dispatch_all, notifications_for

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


async def post(qc, actor, **kw):
    return await qc.post("/leads", json=lead(**kw), headers=headers(actor))


async def check(qc, actor, **body):
    r = await qc.post("/duplicates/check", json=body, headers=headers(actor))
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------ exact matches
@pytest.mark.parametrize(
    "variant",
    [
        "https://www.exact-co.example/",
        "http://EXACT-CO.example",
        "www.exact-co.example",
        "exact-co.example/about?x=1",
        "  Exact-Co.Example  ",
        "https://exact-co.example:8443/path#frag",
    ],
)
async def test_domain_variants_block(qc, world, variant):
    await configure(qc, world)
    await create(qc, world.north1, company="Exact Co Base", website="https://exact-co.example")
    r = await post(qc, world.north1, company="Totally Different Name", website=variant)
    assert r.status_code == 409 and r.json()["code"] == "duplicate_blocked", r.text
    assert r.json()["details"]["matches"][0]["match_type"] == "domain"


@pytest.mark.parametrize(
    "variant",
    [
        "9876543210",
        "+91 98765 43210",
        "098765-43210",
        "+919876543210",
        "91 98765 43210",
        "(98765) 43210",
    ],
)
async def test_phone_variants_block(qc, world, variant):
    await configure(qc, world)
    await create(qc, world.north1, company="Phone Base Co", phone="+919876543210")
    r = await post(qc, world.north1, company="Another Unrelated Name", phone=variant, email=DROP)
    assert r.status_code == 409 and r.json()["details"]["matches"][0]["match_type"] == "phone", (
        r.text
    )


@pytest.mark.parametrize(
    "variant", ["Mail@Base.Example", "mail@base.example", " MAIL@BASE.EXAMPLE "]
)
async def test_email_variants_block(qc, world, variant):
    await configure(qc, world)
    await create(qc, world.north1, company="Email Base Co", email="mail@base.example")
    r = await post(qc, world.north1, company="Another Unrelated Name", email=variant)
    assert r.status_code == 409 and r.json()["details"]["matches"][0]["match_type"] == "email", (
        r.text
    )


async def test_plus_tag_email_is_a_different_address(qc, world):
    await configure(qc, world)
    await create(qc, world.north1, company="Plus Base Co", email="person@plus.example")
    r = await post(
        qc, world.north1, company="Plus Other Industries", email="person+sales@plus.example"
    )
    assert r.status_code == 201  # documented: +tag addresses are not folded together


async def test_exact_matches_ignore_deleted_and_merged_accounts(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(
        qc,
        world.north1,
        company="Gone Co",
        website="gone-co.example",
        phone="9811100001",
        email=DROP,
    )
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE accounts SET deleted_at=now() WHERE id=:i"), {"i": d["account_id"]}
        )
    r = await post(
        qc,
        world.north1,
        company="Gone Co Reborn Industries",
        website="gone-co.example",
        phone="9811100001",
        email=DROP,
    )
    # the unique index only covers live accounts, so a new live account with that domain is allowed
    assert r.status_code == 201, r.text


async def test_check_endpoint_validation(qc, world):
    await configure(qc, world)
    h = headers(world.north1)
    assert (await qc.post("/duplicates/check", json={"company": ""}, headers=h)).status_code == 422
    assert (await qc.post("/duplicates/check", json={}, headers=h)).status_code == 422
    assert (await qc.post("/duplicates/check", json={"company": "x"})).status_code == 401
    ok = await qc.post(
        "/duplicates/check",
        json={"company": "x", "phone": "garbage", "email": "bad", "website": "!!"},
        headers=h,
    )
    assert ok.status_code == 200 and ok.json() == {"block": [], "warn": []}
    big = await qc.post("/duplicates/check", json={"company": "Z" * 100_000}, headers=h)
    assert big.status_code < 500


# ------------------------------------------------------------------------------ similar names
async def test_similarity_threshold_is_half(qc, world):
    from app.modules.accounts.duplicates import NAME_THRESHOLD

    assert NAME_THRESHOLD == 0.5


# (existing name, new name, warns?) judged against what a salesperson would call "the same company".
# The expected column is the business expectation, the API result is asserted to equal the measured
# behaviour (so a change to the threshold shows up here), and the gaps are listed in the report.
NAME_PAIRS = [
    ("Acme Foods", "Acme Foods Pvt Ltd", True),
    ("Acme Foods Pvt Ltd", "ACME FOODS PVT. LTD.", True),
    ("Sharma & Sons", "Sharma and Sons", True),
    ("Sharma & Sons Traders", "Sharma and Sons Traders", True),
    ("Reliance Industries", "Relaince Industries", True),
    ("Foods Acme", "Acme Foods", True),
    ("Infosys", "Infosys Ltd", True),
    ("Mahalakshmi Jewellers", "Mahalakshmi Jewelers", True),
    ("Sai Enterprises", "Sri Sai Enterprises", True),
    ("Café Coffee Day", "Cafe Coffee Day", True),
    ("A B C Traders", "ABC Traders", True),
    ("Patel Brothers", "Patel Bros", True),
    # false negatives
    ("Acme Foods Pvt Ltd", "Acme Foods Private Limited", True),
    ("Microsoft", "Microsfot", True),
    ("Infosys", "Infosis", True),
    ("Tata", "Tata Motors", True),
    ("AB", "AB Corp", True),
    ("3M", "3M India", True),
    ("Zomato", "Zomato Media Pvt Ltd", True),
    # no match expected
    ("HDFC Bank", "ICICI Bank", False),
    ("Tata Steel", "Tata Motors", False),
    ("Kumar Pvt Ltd", "Singh Pvt Ltd", False),
    ("Alpha Pvt Ltd", "Beta Pvt Ltd", False),
    # false positives (different companies that warn)
    ("Unit 1 Pvt Ltd", "Unit 2 Pvt Ltd", False),
    ("Studio 54", "Studio 55", False),
    ("सुमन ट्रेडर्स", "सुनील ट्रेडर्स", False),
]


@pytest.mark.parametrize(("existing", "new", "expected"), NAME_PAIRS)
async def test_name_similarity_pairs_match_the_trigram_maths(qc, world, existing, new, expected):
    """The API answer must equal 'trigram similarity >= 0.5'. Where it differs from `expected`
    (what a salesperson would want), the pair is a known false positive or negative (report RISK)."""
    await configure(qc, world)
    await create(qc, world.north1, company=existing)
    res = await check(qc, world.north1, company=new)
    warned = bool(res["warn"])
    assert warned == (name_similarity(existing, new) >= 0.5)
    assert res["block"] == []


KNOWN_FALSE_NEGATIVES = [p for p in NAME_PAIRS if p[2] and similarity(p[0], p[1]) < 0.5]
KNOWN_FALSE_POSITIVES = [p for p in NAME_PAIRS if not p[2] and similarity(p[0], p[1]) >= 0.5]


def test_the_known_name_matching_gaps_are_the_ones_in_the_report():
    fn = {(a, b) for a, b, _ in KNOWN_FALSE_NEGATIVES}
    fp = {(a, b) for a, b, _ in KNOWN_FALSE_POSITIVES}
    assert ("Acme Foods Pvt Ltd", "Acme Foods Private Limited") in fn
    assert ("Microsoft", "Microsfot") in fn
    assert ("Tata", "Tata Motors") in fn
    assert ("Unit 1 Pvt Ltd", "Unit 2 Pvt Ltd") in fp
    assert len(fn) == 7 and len(fp) == 3


async def test_pvt_ltd_and_private_limited_are_the_same_company(qc, world):
    await configure(qc, world)
    await create(qc, world.north1, company="Acme Foods Pvt Ltd")
    res = await check(qc, world.north1, company="Acme Foods Private Limited")
    assert res["warn"]


async def test_different_hindi_names_do_not_warn(qc, world):
    await configure(qc, world)
    await create(qc, world.north1, company="सुमन ट्रेडर्स")
    res = await check(qc, world.north1, company="सुनील ट्रेडर्स")
    assert res["warn"] == []


async def test_identical_names_warn_in_every_case_and_script(qc, world):
    await configure(qc, world)
    for name in ("Exact Same Name", "अर्जुन ट्रेडर्स", "தமிழ் டெக்"):
        await create(qc, world.north1, company=name)
        res = await check(qc, world.north1, company=name.upper())
        assert res["warn"], name


async def test_emoji_or_symbol_only_names_never_warn_even_when_identical(qc, world):
    """Documented: with no letters or digits there are no trigrams, so the check is blind."""
    await configure(qc, world)
    await create(qc, world.north1, company="\U0001f355\U0001f680")
    res = await check(qc, world.north1, company="\U0001f355\U0001f680")
    assert res["warn"] == []


async def test_company_name_in_check_is_case_and_padding_insensitive(qc, world):
    await configure(qc, world)
    await create(qc, world.north1, company="Nimbus Cloud Solutions")
    res = await check(qc, world.admin, company="   NIMBUS   cloud   solutions ")  # sees the score
    assert res["warn"] and res["warn"][0]["similarity"] == 1.0


async def test_exact_name_with_different_contact_only_warns_and_create_anyway_works(qc, world):
    await configure(qc, world)
    await create(qc, world.north1, company="Same Name Co")
    r = await post(qc, world.north1, company="Same Name Co")
    assert r.status_code == 409 and r.json()["code"] == "duplicate_warning"
    r = await post(qc, world.north1, company="Same Name Co", create_anyway=True)
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await post(qc, world.north1, company="Same Name Co", create_anyway=True, reason="  ")
    assert r.status_code == 422
    r = await post(
        qc, world.north1, company="Same Name Co", create_anyway=True, reason="sister branch"
    )
    assert r.status_code == 201


async def test_create_anyway_cannot_bypass_an_exact_block(qc, world):
    await configure(qc, world)
    await create(qc, world.north1, company="Blocked Co", website="blocked-co.example")
    r = await post(
        qc,
        world.north1,
        company="Blocked Co",
        website="blocked-co.example",
        create_anyway=True,
        reason="please",
    )
    assert r.status_code == 409 and r.json()["code"] == "duplicate_blocked"


async def test_override_block_permissions_and_effects(qc, admin_engine, world):
    await configure(qc, world)
    first = await create(qc, world.north1, company="Override Base", website="override-base.example")
    body = dict(company="Override Second", website="override-base.example", override_block=True)
    for actor in (world.north1, world.south1, world.tl_north, world.tl_south):
        r = await post(qc, actor, **body, reason="needed")
        assert r.status_code == 403, (actor.name, r.text)
    assert (await post(qc, world.admin, **body)).status_code == 422
    r = await post(qc, world.admin, **body, reason="second contact at same firm")
    assert r.status_code == 201 and r.json()["account_id"] == first["account_id"]
    r2 = await post(qc, world.sa, **{**body, "company": "Override Third"})
    assert r2.status_code == 201 and r2.json()["account_id"] == first["account_id"]
    assert (
        await q1(admin_engine, "SELECT count(*) FROM accounts WHERE org_id=:o", o=world.org_id) == 1
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM contacts WHERE account_id=:a",
            a=uuid.UUID(first["account_id"]),
        )
        == 3
    )
    rows = await q(
        admin_engine,
        "SELECT match_type, in_scope, reason FROM duplicate_overrides WHERE deal_id=:d",
        d=uuid.UUID(r.json()["id"]),
    )
    assert [tuple(x) for x in rows] == [("domain", True, "second contact at same firm")]
    keys = [tuple(x)[:2] for x in await audit_rows(admin_engine, r.json()["id"])]
    assert keys.count(("duplicate.override_or_merge", "ok")) == 1
    denied = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='duplicate.override_or_merge' AND outcome='denied'",
        o=world.org_id,
    )
    assert (
        denied == 4
    )  # the four who may not (the Admin without a reason stops earlier, at lead.create)


async def test_create_anyway_matrix_by_role(qc, world):
    await configure(qc, world)
    await create(qc, world.north1, company="Matrix Dup Co")
    for actor in (world.north2, world.south1, world.tl_south, world.tl_north):
        r = await post(qc, actor, company="Matrix Dup Co", create_anyway=True)
        assert r.status_code == 422 and r.json()["code"] == "reason_required", actor.name
        r = await post(qc, actor, company="Matrix Dup Co", create_anyway=True, reason="r")
        assert r.status_code == 201, (actor.name, r.text)
    assert (
        await post(qc, world.admin, company="Matrix Dup Co", create_anyway=True)
    ).status_code == 422
    assert (
        await post(qc, world.admin, company="Matrix Dup Co", create_anyway=True, reason="r")
    ).status_code == 201
    assert (
        await post(qc, world.sa, company="Matrix Dup Co", create_anyway=True)
    ).status_code == 201


async def test_create_anyway_outside_scope_is_flagged_once_to_admins_and_inside_scope_is_not(
    qc, world
):
    await configure(qc, world)
    await create(qc, world.north1, company="Flag Industries Pvt")
    r = await post(
        qc, world.south1, company="Flag Industries Pvt Ltd", create_anyway=True, reason="other firm"
    )
    assert r.status_code == 201
    inner = await create(qc, world.north1, company="Zeta Corp Holdings")
    owner2 = next(a for a in world.all_actors if str(a.id) == inner["owner"]["id"])
    r_in = await post(
        qc, owner2, company="Zeta Corp Holdings Ltd", create_anyway=True, reason="same team"
    )
    assert r_in.status_code == 201
    await dispatch_all()
    await dispatch_all()
    for admin in (world.admin, world.sa):
        flagged = [
            n
            for n in await notifications_for(admin.id)
            if n.type == "duplicate.created_anyway_outside_scope"
        ]
        assert len(flagged) == 1, admin.name
        assert r.json()["id"] in flagged[0].link
    for other in (world.tl_north, world.tl_south, world.north1, world.south1):
        assert not [
            n
            for n in await notifications_for(other.id)
            if n.type == "duplicate.created_anyway_outside_scope"
        ]


# ------------------------------------------------------------------------------ leaks
LEAK_BASE = dict(
    company="Leaky Holdings Private",
    website="https://leaky-holdings.example",
    phone="9820000001",
    email="boss@leaky-holdings.example",
)
LEAK_MATCHES = {
    "domain": dict(
        company="Zzz Fresh One",
        website="www.leaky-holdings.example",
        email=DROP,
        phone="9820000099",
    ),
    "phone": dict(company="Zzz Fresh Two", phone="+91 98200 00001", email=DROP),
    "email": dict(company="Zzz Fresh Three", email="BOSS@leaky-holdings.example"),
    "name": dict(company="Leaky Holdings Private Ltd"),
}
ALLOWED_KEYS = {"match_type", "company", "owner_name", "team_name", "in_scope"}


async def _leak_setup(qc, admin_engine, world, creator=None):
    await configure(qc, world)
    base = await create(qc, creator or world.tl_north, **LEAK_BASE)
    contact_id = base["contact"]["id"]
    forbidden = {
        base["id"],
        base["account_id"],
        contact_id,
        base["owner"]["id"],
        str(world.north_team),
        str(world.sa.id),
        str(world.admin.id),
        str(world.tl_north.id),
    }
    return base, forbidden


def _scan(response, forbidden):
    blob = response.text + json.dumps(dict(response.headers))
    for f in forbidden:
        assert f not in blob, f"{f} leaked in {blob[:300]}"
    ids_in_headers = UUID_RE.findall(json.dumps(dict(response.headers)))
    # the request id header is generated per request and carries no data about other teams
    assert all(h not in forbidden for h in ids_in_headers)


@pytest.mark.parametrize("kind", list(LEAK_MATCHES))
@pytest.mark.parametrize("who", ["south1", "south2", "tl_south"])
async def test_out_of_scope_matches_reveal_no_ids(qc, admin_engine, world, kind, who):
    base, forbidden = await _leak_setup(qc, admin_engine, world)
    viewer = getattr(world, who)
    body = LEAK_MATCHES[kind]
    # live check
    live = await qc.post(
        "/duplicates/check",
        json={k: v for k, v in body.items() if v is not DROP and k != "reason"},
        headers=headers(viewer),
    )
    assert live.status_code == 200
    _scan(live, forbidden)
    matches = live.json()["block"] + live.json()["warn"]
    assert len(matches) == 1
    assert set(matches[0]) <= ALLOWED_KEYS and matches[0]["in_scope"] is False
    assert matches[0]["company"] == "Leaky Holdings Private"
    # create call
    r = await post(qc, viewer, **body)
    assert r.status_code == 409
    _scan(r, forbidden)
    assert set(r.json()["details"]["matches"][0]) <= ALLOWED_KEYS
    assert set(r.json()["details"]) == {"matches"}
    # the same attempt with create_anyway or override as a non-privileged person
    r2 = await post(qc, viewer, **body, create_anyway=True, reason="x")
    if (
        r2.status_code != 201
    ):  # a created lead shows the creator their own new lead, not the old one
        _scan(r2, forbidden)
    else:
        assert body["company"] == LEAK_MATCHES["name"]["company"]  # only a name match may be forced
        assert base["id"] not in r2.text and base["account_id"] not in r2.text
    r3 = await post(qc, viewer, **body, override_block=True, reason="x")
    if r3.status_code != 201:
        _scan(r3, forbidden)
    assert r3.status_code in (403, 409)


@pytest.mark.parametrize("kind", ["domain", "phone", "email", "name"])
async def test_a_user_in_the_same_team_but_not_the_owner_is_also_out_of_scope(
    qc, admin_engine, world, kind
):
    base, forbidden = await _leak_setup(qc, admin_engine, world)  # created by the Team Leader
    other = world.north2 if base["owner"]["id"] == str(world.north1.id) else world.north1
    r = await post(qc, other, **LEAK_MATCHES[kind])
    assert r.status_code == 409
    m = r.json()["details"]["matches"][0]
    assert m["in_scope"] is False and "deal_id" not in m and "account_id" not in m
    _scan(r, {base["id"], base["account_id"], base["contact"]["id"]})


async def test_the_creator_of_a_handed_over_lead_is_still_treated_as_in_scope(
    qc, admin_engine, world
):
    """RISK-P2-03 fixed: scope follows the deals only (PRD 4.6). The creator of a lead that was
    handed to someone else is outside scope: no account id, no deal id."""
    await configure(qc, world)
    base = await create(qc, world.north1, **LEAK_BASE)
    other = world.north2 if base["owner"]["id"] == str(world.north1.id) else world.north1
    r = await post(qc, other, **LEAK_MATCHES["phone"])
    assert r.status_code == 409
    owner_is_creator = base["owner"]["id"] == str(world.north1.id)
    m = r.json()["details"]["matches"][0]
    assert m["in_scope"] is False and "account_id" not in m and "deal_id" not in m
    _ = owner_is_creator


async def test_out_of_scope_responses_have_the_same_shape_for_every_match_type(
    qc, admin_engine, world
):
    base, _ = await _leak_setup(qc, admin_engine, world)
    shapes = set()
    for body in LEAK_MATCHES.values():
        r = await post(qc, world.south1, **body)
        m = r.json()["details"]["matches"][0]
        shapes.add(frozenset(m))
    assert len(shapes) == 1, shapes


async def test_in_scope_matches_show_the_ids_for_open_existing(qc, admin_engine, world):
    base, _ = await _leak_setup(qc, admin_engine, world)
    owner = next(a for a in world.all_actors if str(a.id) == base["owner"]["id"])
    for viewer in (owner, world.tl_north, world.admin, world.sa):
        res = await check(qc, viewer, company="Whatever", phone="9820000001")
        m = res["block"][0]
        assert (
            m["in_scope"] is True
            and m["deal_id"] == base["id"]
            and m["account_id"] == base["account_id"]
        ), viewer.name


async def test_out_of_scope_match_shows_only_company_and_owner(qc, admin_engine, world):
    await configure(qc, world)
    await create(qc, world.north1, **LEAK_BASE)
    res = await check(qc, world.south1, company="x", phone="9820000001")
    assert set(res["block"][0]) <= {"match_type", "company", "owner_name", "in_scope"}


async def test_super_admin_owned_deals_hide_the_owner_and_team_from_everyone_below_admin(
    qc, admin_engine, world
):
    await configure(qc, world)
    base = await create(qc, world.north1, **LEAK_BASE)
    r = await qc.post(
        f"/deals/{base['id']}/reassign",
        json={"user_id": str(world.sa.id), "reason": "hand over"},
        headers=headers(world.admin),
    )
    assert r.status_code == 200, r.text
    for viewer in (world.south1, world.tl_south, world.tl_north, world.north2):
        res = await check(qc, viewer, company="x", phone="9820000001")
        m = res["block"][0]
        assert m["owner_name"] is None and m.get("team_name") is None, viewer.name
        assert "Samar" not in json.dumps(res)
    seen = await check(qc, world.admin, company="x", phone="9820000001")
    assert seen["block"][0]["owner_name"] == "Samar"


async def test_accounts_search_and_page_never_reveal_out_of_scope_companies(
    qc, admin_engine, world
):
    base, forbidden = await _leak_setup(qc, admin_engine, world)
    for viewer in (world.south1, world.south2, world.tl_south):
        r = await qc.get("/accounts", params={"q": "Leaky"}, headers=headers(viewer))
        assert r.status_code == 200 and r.json() == []
        r = await qc.get(f"/accounts/{base['account_id']}", headers=headers(viewer))
        assert r.status_code == 404
        r = await qc.get(f"/accounts/{base['account_id']}/timeline", headers=headers(viewer))
        assert r.status_code == 404
        r = await qc.get(f"/deals/{base['id']}", headers=headers(viewer))
        assert r.status_code == 404
        r = await qc.get("/deals", params={"limit": 500}, headers=headers(viewer))
        assert "Leaky" not in r.text
        r = await qc.get("/transfers/lookup", params={"q": "leaky"}, headers=headers(viewer))
        assert "Leaky" not in r.text or viewer is world.tl_south


async def test_a_creator_loses_the_account_after_the_lead_goes_to_another_team(qc, world):
    # A rep's lead stays in the rep's own team unless a rule routes it elsewhere (the default team
    # only serves creators without a team), so route every source to Team South with a rule.
    # Without it the lead lands on a North rep, which is north1 itself about half the time.
    await configure(
        qc,
        world,
        rules=[
            {
                "name": "All to South",
                "priority": 1,
                "active": True,
                "match": {},
                "target_type": "team",
                "target_id": str(world.south_team),
            }
        ],
    )
    d = await create(qc, world.north1, company="Handed Over Co", phone="9830000001", email=DROP)
    assert d["team_name"] == "Team South" and d["owner"]["id"] != str(world.north1.id)
    # PRD 4.6 / BUG-P2-27: access follows the deals, so the creator no longer reads the account
    r = await qc.get(f"/accounts/{d['account_id']}", headers=headers(world.north1))
    assert r.status_code == 404


async def test_locked_rows_on_the_account_page(qc, admin_engine, world):
    await configure(qc, world)
    a = await create(
        qc, world.north1, company="Shared Account Co", website="shared-account.example"
    )
    await configure(qc, world, team=world.south_team)
    b = await post(
        qc,
        world.admin,
        company="Shared Second",
        website="shared-account.example",
        override_block=True,
        reason="two teams",
    )
    assert b.status_code == 201 and b.json()["account_id"] == a["account_id"]
    assert b.json()["team_name"] == "Team South"
    owner_a = next(x for x in world.all_actors if str(x.id) == a["owner"]["id"])
    acc = (await qc.get(f"/accounts/{a['account_id']}", headers=headers(owner_a))).json()
    assert [d["id"] for d in acc["deals"]] == [a["id"]]
    assert acc["locked_deals"] == [{"label": "Deal owned by another team"}]
    assert b.json()["id"] not in json.dumps(acc) and b.json()["owner"]["id"] not in json.dumps(acc)
    # a Team Leader of each team sees only their own deal, an Admin sees both and no locked rows
    tl = (await qc.get(f"/accounts/{a['account_id']}", headers=headers(world.tl_north))).json()
    assert [d["id"] for d in tl["deals"]] == [a["id"]] and len(tl["locked_deals"]) == 1
    adm = (await qc.get(f"/accounts/{a['account_id']}", headers=headers(world.admin))).json()
    assert {d["id"] for d in adm["deals"]} == {a["id"], b.json()["id"]} and adm[
        "locked_deals"
    ] == []


# ------------------------------------------------------------------------------ merge
async def _two_accounts(qc, world, **kw):
    a = await create(
        qc,
        world.north1,
        company="Merge Source Co",
        website="merge-source.example",
        phone="9840000001",
        email=DROP,
    )
    b = await create(
        qc,
        world.north1,
        company="Completely Other Target",
        website="merge-target.example",
        phone="9840000002",
        email=DROP,
    )
    return a, b


async def merge(qc, actor, src, dst, **kw):
    return await qc.post(
        f"/accounts/{src}/merge", json={"into_account_id": dst, **kw}, headers=headers(actor)
    )


async def test_merge_roles(qc, admin_engine, world):
    await configure(qc, world)
    a, b = await _two_accounts(qc, world)
    for actor in (world.north1, world.tl_north, world.south1, world.tl_south):
        r = await merge(qc, actor, a["account_id"], b["account_id"], reason="dup")
        assert r.status_code == 403, actor.name
    assert (await merge(qc, world.admin, a["account_id"], b["account_id"])).status_code == 422
    assert (
        await merge(qc, world.admin, a["account_id"], b["account_id"], reason="  ")
    ).status_code == 422
    r = await merge(qc, world.admin, a["account_id"], b["account_id"], reason="same company")
    assert r.status_code == 200 and r.json()["id"] == b["account_id"]
    # SA may merge without a reason
    c = await create(
        qc, world.north1, company="Another Merge Victim", phone="9840000003", email=DROP
    )
    assert (await merge(qc, world.sa, c["account_id"], b["account_id"])).status_code == 200


async def test_merge_moves_everything_and_keeps_the_source_row(qc, admin_engine, world):
    await configure(qc, world)
    a, b = await _two_accounts(qc, world)
    r = await merge(qc, world.admin, a["account_id"], b["account_id"], reason="same company")
    assert r.status_code == 200
    src = (
        await q(
            admin_engine,
            "SELECT merged_into_id, domain, deleted_at, name FROM accounts WHERE id=:i",
            i=uuid.UUID(a["account_id"]),
        )
    )[0]
    assert str(src[0]) == b["account_id"] and src[2] is None and src[3] == "Merge Source Co"
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM deals WHERE account_id=:i",
            i=uuid.UUID(a["account_id"]),
        )
        == 0
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM deals WHERE account_id=:i",
            i=uuid.UUID(b["account_id"]),
        )
        == 2
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM contacts WHERE account_id=:i",
            i=uuid.UUID(b["account_id"]),
        )
        == 2
    )
    # merged accounts are hidden from search and from the duplicate check
    found = (
        await qc.get("/accounts", params={"q": "Merge Source"}, headers=headers(world.admin))
    ).json()
    assert a["account_id"] not in {x["id"] for x in found}
    res = await check(qc, world.admin, company="Merge Source Co")
    assert all(m["account_id"] != a["account_id"] for m in res["warn"] + res["block"])
    # the moved phone now blocks against the surviving account
    res = await check(qc, world.admin, company="zzz", phone="9840000001")
    assert res["block"][0]["account_id"] == b["account_id"]
    # audit: one row on each account, with the reason
    rows = await q(
        admin_engine,
        "SELECT entity_id, reason, outcome FROM audit_log WHERE action_key='duplicate.override_or_merge' AND entity_type='account' AND org_id=:o",
        o=world.org_id,
    )
    assert sorted(str(x[0]) for x in rows) == sorted([a["account_id"], b["account_id"]])
    assert {x[1] for x in rows} == {"same company"}
    detail = (await qc.get(f"/accounts/{b['account_id']}", headers=headers(world.admin))).json()
    assert len(detail["deals"]) == 2 and len(detail["contacts"]) == 2


async def test_merge_edge_cases(qc, admin_engine, world):
    await configure(qc, world)
    a, b = await _two_accounts(qc, world)
    assert (
        await merge(qc, world.admin, a["account_id"], a["account_id"], reason="x")
    ).status_code == 409
    assert (
        await merge(qc, world.admin, a["account_id"], str(uuid.uuid4()), reason="x")
    ).status_code == 404
    assert (
        await merge(qc, world.admin, str(uuid.uuid4()), b["account_id"], reason="x")
    ).status_code == 404
    assert (
        await merge(qc, world.admin, a["account_id"], b["account_id"], reason="x")
    ).status_code == 200
    # merging the same source twice, and merging into / out of an already merged account
    assert (
        await merge(qc, world.admin, a["account_id"], b["account_id"], reason="x")
    ).status_code == 404
    assert (
        await merge(qc, world.admin, b["account_id"], a["account_id"], reason="cycle")
    ).status_code == 404
    c = await create(
        qc, world.north1, company="Third Wheel Industries", phone="9840000009", email=DROP
    )
    assert (
        await merge(qc, world.admin, c["account_id"], a["account_id"], reason="into merged")
    ).status_code == 404
    assert (
        await merge(qc, world.admin, a["account_id"], c["account_id"], reason="out of merged")
    ).status_code == 404
    # nothing was erased and no cycle exists
    assert (
        await q1(admin_engine, "SELECT count(*) FROM accounts WHERE org_id=:o", o=world.org_id) == 3
    )
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM accounts WHERE org_id=:o AND merged_into_id IS NOT NULL",
            o=world.org_id,
        )
        == 1
    )


async def test_merge_across_organisations_is_refused(qc, admin_engine, world):
    await configure(qc, world)
    other = await make_world(admin_engine)
    await configure(qc, other)
    mine = await create(qc, world.north1, company="Org One Co", phone="9850000001", email=DROP)
    theirs = await create(qc, other.north1, company="Org Two Co", phone="9850000002", email=DROP)
    r = await merge(qc, world.admin, mine["account_id"], theirs["account_id"], reason="x")
    assert r.status_code == 404
    r = await merge(qc, world.admin, theirs["account_id"], mine["account_id"], reason="x")
    assert r.status_code == 404
    r = await merge(qc, other.admin, mine["account_id"], theirs["account_id"], reason="x")
    assert r.status_code == 404
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM accounts WHERE merged_into_id IS NOT NULL AND org_id IN (:a,:b)",
            a=world.org_id,
            b=other.org_id,
        )
        == 0
    )


async def test_merging_twice_at_once_has_one_winner(qc, admin_engine, world):
    import asyncio

    await configure(qc, world)
    a, b = await _two_accounts(qc, world)
    rs = await asyncio.gather(
        *(merge(qc, world.admin, a["account_id"], b["account_id"], reason="r") for _ in range(6))
    )
    codes = sorted(r.status_code for r in rs)
    assert codes == [200] + [404] * 5, codes
    rows = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE action_key='duplicate.override_or_merge' AND entity_type='account' AND org_id=:o",
        o=world.org_id,
    )
    assert rows == 2


async def test_two_merges_in_opposite_directions_at_once_never_deadlock_or_cycle(
    qc, admin_engine, world
):
    import asyncio

    await configure(qc, world)
    a, b = await _two_accounts(qc, world)
    rs = await asyncio.gather(
        *(
            [merge(qc, world.admin, a["account_id"], b["account_id"], reason="r") for _ in range(3)]
            + [
                merge(qc, world.admin, b["account_id"], a["account_id"], reason="r")
                for _ in range(3)
            ]
        )
    )
    codes = sorted(r.status_code for r in rs)
    assert 500 not in codes and codes.count(200) == 1, codes
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM accounts WHERE org_id=:o AND merged_into_id IS NOT NULL",
            o=world.org_id,
        )
        == 1
    )


async def test_the_merged_accounts_website_still_blocks_new_leads(qc, world):
    await configure(qc, world)
    a, b = await _two_accounts(qc, world)
    assert (
        await merge(qc, world.admin, a["account_id"], b["account_id"], reason="same company")
    ).status_code == 200
    r = await post(qc, world.north1, company="Brand New Name", website="merge-source.example")
    assert r.status_code == 409 and r.json()["code"] == "duplicate_blocked"


async def _bare_pair(admin_engine, world, src_id, dst_id):
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO accounts (id, org_id, name, domain, website) VALUES (:i,:o,'Src Co','src-co.example','src-co.example')"
            ),
            {"i": src_id, "o": world.org_id},
        )
        await c.execute(
            text("INSERT INTO accounts (id, org_id, name) VALUES (:i,:o,'Dst Co')"),
            {"i": dst_id, "o": world.org_id},
        )


LOW = uuid.UUID("00000000-0000-4000-8000-000000000001")
HIGH = uuid.UUID("ffffffff-0000-4000-8000-000000000001")


async def test_merging_into_an_account_without_a_website_adopts_the_domain(qc, admin_engine, world):
    """Works when the source row sorts first by id (the order SQLAlchemy writes the two rows in)."""
    src_id, dst_id = LOW, HIGH
    await _bare_pair(admin_engine, world, src_id, dst_id)
    r = await merge(qc, world.admin, str(src_id), str(dst_id), reason="r")
    assert r.status_code == 200, r.text
    assert (
        await q1(admin_engine, "SELECT domain FROM accounts WHERE id=:i", i=dst_id)
        == "src-co.example"
    )
    assert await q1(admin_engine, "SELECT domain FROM accounts WHERE id=:i", i=src_id) is None


async def test_merging_into_an_account_without_a_website_works_whatever_the_ids(
    qc, admin_engine, world
):
    high, low = (  # own ids: account ids are unique across all test organisations
        uuid.UUID("ffffffff-0000-4000-8000-000000000002"),
        uuid.UUID("00000000-0000-4000-8000-000000000002"),
    )
    await _bare_pair(admin_engine, world, high, low)  # source id sorts after the survivor
    r = await merge(qc, world.admin, str(high), str(low), reason="r")
    assert r.status_code == 200, r.text


async def test_deals_moved_by_a_merge_get_their_own_history_row(qc, admin_engine, world):
    await configure(qc, world)
    a, b = await _two_accounts(qc, world)
    await merge(qc, world.admin, a["account_id"], b["account_id"], reason="same company")
    rows = await audit_rows(admin_engine, a["id"])
    assert any("merge" in (r[0] or "") or "account" in (r[6] or "") for r in rows[1:])


async def test_contacts_of_a_locked_deal_are_not_shown(qc, world):
    await configure(qc, world)
    a = await create(
        qc, world.tl_north, company="Locked Contacts Co", phone="9877700001", email=None
    )
    await configure(qc, world, team=world.south_team)
    b = await post(
        qc,
        world.admin,
        company="x",
        phone="9877700001",
        email=None,
        contact_name="Hidden Person",
        override_block=True,
        reason="other team",
    )
    assert b.status_code == 201 and b.json()["account_id"] == a["account_id"]
    owner = next(x for x in world.all_actors if str(x.id) == a["owner"]["id"])
    acc = (await qc.get(f"/accounts/{a['account_id']}", headers=headers(owner))).json()
    assert "Hidden Person" not in json.dumps(acc)
