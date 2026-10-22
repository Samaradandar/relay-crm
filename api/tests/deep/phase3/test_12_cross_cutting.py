# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 8 (cross-cutting): endpoint inventory, 401, forged / expired tokens,
IDOR on every id route (other team and other organisation), mass assignment, injection, paging."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.security import DevAuthProvider, get_auth_provider
from tests.conftest import insert_user
from tests.deep.phase3.invariants import check_invariants
from tests.deep.phase3.p3 import (
    H,
    add_doc,
    configure,
    make_file,
    mkdeal,
    one,
    rows,
    run,
    second_org,
)
from tests.deep.phase3.test_11_exports_profiles_timeline import FakeStorage


@pytest.fixture(autouse=True)
def _fake_storage():
    from app.platform.files import storage

    storage.set_storage(FakeStorage())
    yield
    storage.set_storage(None)


PHASE3_TAGS = {"deals", "pipeline", "activities", "catalog", "documents", "exports", "profiles"}
EXPECTED_OPS = """
GET /deals|POST /deals/bulk/lose|POST /deals/bulk/snooze|POST /deals/bulk/reassign|GET /deals/{deal_id}
PATCH /deals/{deal_id}|POST /deals/{deal_id}/undo|POST /deals/{deal_id}/lose|POST /deals/{deal_id}/reopen
POST /deals/{deal_id}/delete|POST /deals/{deal_id}/restore|GET /deleted/deals|POST /deals/{deal_id}/next-step
POST /next-steps/{step_id}/done|POST /next-steps/{step_id}/snooze|POST /next-steps/{step_id}/replied
POST /next-steps/{step_id}/nudge|GET /today|GET /board|GET /board/cards|GET /stages/metrics
GET /stages/{key}/metrics|GET /stages/{key}/deals|GET /accounts/{account_id}/timeline
GET /pipelines/{key}/versions|POST /pipelines/{key}/versions|GET /pipelines/{key}/versions/{version_no}
DELETE /pipelines/{key}/draft|POST /pipelines/{key}/versions/{version_no}/validate
GET /pipelines/{key}/versions/{version_no}/impact|POST /pipelines/{key}/versions/{version_no}/publish
POST /pipelines/{key}/rollback/{version_no}|GET /settings/custom-fields|PUT /settings/custom-fields
GET /settings/lost-reasons|PUT /settings/lost-reasons|GET /settings/fit-checklist|PUT /settings/fit-checklist
POST /deals/{deal_id}/move|POST /deals/{deal_id}/criteria/{key}/tick|POST /deals/{deal_id}/criteria/{key}/untick
POST /deals/{deal_id}/criteria/{key}/verify|POST /deals/{deal_id}/review-done|GET /deals/{deal_id}/activities
POST /deals/{deal_id}/activities|PATCH /activities/{activity_id}|DELETE /activities/{activity_id}
GET /activities/{activity_id}/versions|POST /activities/{activity_id}/meeting/held|POST /activities/{activity_id}/meeting/postpone|POST /activities/{activity_id}/meeting/cancel|GET /products|PUT /products|GET /deals/{deal_id}/lines
POST /deals/{deal_id}/lines|PATCH /deals/{deal_id}/lines/{line_id}|DELETE /deals/{deal_id}/lines/{line_id}
GET /deals/{deal_id}/documents|POST /deals/{deal_id}/documents|PATCH /documents/{doc_id}
DELETE /documents/{doc_id}|POST /exports|GET /exports/log|GET /exports/mine|GET /exports/{export_id}
GET /profiles/{user_id}|GET /monitoring|GET /teams-compare
""".replace("\n", "|").strip("|")


def phase3_ops() -> set[str]:
    from app.main import create_app

    out = set()
    for path, item in create_app().openapi()["paths"].items():
        for method, op in item.items():
            if PHASE3_TAGS & set(op.get("tags", [])) or path == "/accounts/{account_id}/timeline":
                out.add(f"{method.upper()} {path}")
    return out


def test_endpoint_inventory_is_the_one_the_tests_cover():
    got = phase3_ops()
    want = {x.strip() for x in EXPECTED_OPS.split("|") if x.strip()}
    assert got == want, (sorted(got - want), sorted(want - got))
    assert len(got) == 68


ZERO = "00000000-0000-0000-0000-000000000000"


def fill(path: str, **ids: str) -> str:
    out = path
    for k, v in {
        "deal_id": ids.get("deal", ZERO), "step_id": ids.get("step", ZERO), "key": ids.get("key", "lead"),
        "version_no": ids.get("version", "1"), "activity_id": ids.get("activity", ZERO),
        "line_id": ids.get("line", ZERO), "doc_id": ids.get("doc", ZERO), "export_id": ids.get("export", ZERO),
        "user_id": ids.get("user", ZERO), "account_id": ids.get("account", ZERO),
    }.items():  # fmt: skip
        out = out.replace("{" + k + "}", v)
    return out


def op_list():
    return sorted(
        (x.split(" ", 1)[0], x.split(" ", 1)[1])
        for x in {y.strip() for y in EXPECTED_OPS.split("|") if y.strip()}
    )


# ============================================================================== 401 and bad tokens
async def test_every_endpoint_refuses_missing_and_bad_tokens(client, world, admin_engine):
    provider = get_auth_provider()
    assert isinstance(provider, DevAuthProvider)
    secret = provider._secret
    now = datetime.now(UTC)
    claims = {
        "sub": "x",
        "email": world.admin.email.lower(),
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    tokens = {
        "none": None,
        "empty bearer": "",
        "junk": "not.a.jwt",
        "wrong key": jwt.encode(claims, "another-secret-another-secret-123456", algorithm="HS256"),
        "expired": jwt.encode(
            {**claims, "exp": now - timedelta(seconds=5)}, secret, algorithm="HS256"
        ),
        "alg none": jwt.encode(claims, key="", algorithm="none"),
        "unknown user": jwt.encode(
            {**claims, "email": "ghost@example.com"}, secret, algorithm="HS256"
        ),
    }
    bad = []
    for method, path in op_list():
        url = fill(path)
        for label, tok in tokens.items():
            headers = {} if tok is None else {"Authorization": f"Bearer {tok}"}
            try:
                r = await client.request(
                    method,
                    url,
                    headers=headers,
                    json={} if method not in ("GET", "DELETE") else None,
                )
            except Exception as e:  # noqa: BLE001
                bad.append(f"{method} {path} [{label}] raised {type(e).__name__}")
                continue
            if r.status_code != 401:
                bad.append(f"{method} {path} [{label}] -> {r.status_code}")
    assert not bad, "\n".join(bad[:30])
    # a deactivated user's valid token stops working at once
    await run(admin_engine, "UPDATE users SET is_active=false WHERE id=:u", u=world.north2.id)
    for method, path in (("GET", "/board"), ("GET", "/today"), ("POST", "/exports")):
        r = await client.request(
            method, path, headers=H(world.north2), json={} if method == "POST" else None
        )
        assert r.status_code == 401, path


# ============================================================================== IDOR
class Fixture:
    pass


async def build_fixture(client, world, eng):
    f = Fixture()
    await configure(client, world, cap=500)
    f.deal, f.owner = await mkdeal(client, world, eng, owner=world.north1, stage="qualification")
    r = await client.post(
        f"/deals/{f.deal}/activities",
        json={
            "type": "call",
            "two_way": True,
            "discussed": "d",
            "next_step_text": "x",
            "next_step_due_date": "2999-01-01",
        },
        headers=H(f.owner),
    )
    f.activity = r.json()["id"]
    fid = await make_file(eng, world, f.deal, f.owner)
    f.doc = (await add_doc(client, f.owner, f.deal, "agreement", "sent", fid))["id"]
    prod = (
        await client.put(
            "/products",
            json={
                "products": [
                    {"name": "P", "unit": "fixed", "list_price_paise": 1000, "floor_price_paise": 0}
                ],
                "reason": "x",
            },
            headers=H(world.admin),
        )
    ).json()[0]
    f.line = (
        await client.post(
            f"/deals/{f.deal}/lines",
            json={"product_id": prod["id"], "quantity": 5},
            headers=H(f.owner),
        )
    ).json()["lines"][0]["id"]
    f.step = await one(
        eng, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=f.deal
    )
    f.account = await one(eng, "SELECT account_id FROM deals WHERE id=:d", d=f.deal)
    r = await client.post("/exports", json={"entity": "deals", "reason": "x"}, headers=H(f.owner))
    f.export = r.json()["id"]
    return f


def body_for(method, path):
    b = {
        "PATCH /deals/{deal_id}": {"use_case": "hijack"},
        "POST /deals/{deal_id}/undo": {"audit_id": ZERO},
        "POST /deals/{deal_id}/lose": {"lost_reason": "Price"},
        "POST /deals/{deal_id}/reopen": {"reason": "x"},
        "POST /deals/{deal_id}/delete": {"reason": "x"},
        "POST /deals/{deal_id}/restore": {"reason": "x"},
        "POST /deals/{deal_id}/next-step": {"text": "hijack", "due_date": "2999-01-01"},
        "POST /next-steps/{step_id}/done": {"next": {"text": "x", "due_date": "2999-01-01"}},
        "POST /deals/{deal_id}/move": {
            "to_stage": "meeting_scheduled",
            "reason": "x",
            "override": True,
        },
        "POST /deals/{deal_id}/criteria/{key}/tick": {},
        "POST /deals/{deal_id}/criteria/{key}/untick": {"reason": "x"},
        "POST /deals/{deal_id}/criteria/{key}/verify": {"decision": "reject", "reason": "x"},
        "POST /deals/{deal_id}/review-done": {},
        "POST /deals/{deal_id}/activities": {
            "type": "call",
            "two_way": True,
            "discussed": "hijack",
            "next_step_text": "x",
            "next_step_due_date": "2999-01-01",
        },
        "PATCH /activities/{activity_id}": {"discussed": "hijack", "reason": "x"},
        "POST /deals/{deal_id}/lines": {"product_id": ZERO, "quantity": 1},
        "PATCH /deals/{deal_id}/lines/{line_id}": {"quantity": 9, "reason": "x"},
        "POST /deals/{deal_id}/documents": {"kind": "proposal", "status": "draft"},
        "PATCH /documents/{doc_id}": {"notes": "hijack", "reason": "x"},
    }
    return b.get(f"{method} {path}", {} if method in ("POST", "PUT", "PATCH") else None)


SNAP_SQL = (
    "SELECT (SELECT md5(string_agg(d::text, '|' ORDER BY d.id)) FROM deals d WHERE d.org_id=:o),"
    "(SELECT md5(coalesce(string_agg(a::text, '|' ORDER BY a.id), '')) FROM activities a WHERE a.org_id=:o),"
    "(SELECT md5(coalesce(string_agg(x::text, '|' ORDER BY x.id), '')) FROM documents x WHERE x.org_id=:o),"
    "(SELECT md5(coalesce(string_agg(l::text, '|' ORDER BY l.id), '')) FROM deal_products l WHERE l.org_id=:o),"
    "(SELECT md5(coalesce(string_agg(c::text, '|' ORDER BY c.id), '')) FROM deal_criteria c WHERE c.org_id=:o),"
    "(SELECT md5(coalesce(string_agg(n::text, '|' ORDER BY n.id), '')) FROM next_steps n WHERE n.org_id=:o),"
    "(SELECT md5(coalesce(string_agg(e::text, '|' ORDER BY e.id), '')) FROM deal_stage_entries e WHERE e.org_id=:o)"
)


@pytest.mark.parametrize("who", ["south_rep", "south_tl", "north_other_rep", "other_org_admin"])
async def test_idor_every_id_route_refuses_outsiders_and_changes_nothing(
    client, world, admin_engine, who
):
    eng = admin_engine
    f = await build_fixture(client, world, eng)
    if who == "other_org_admin":
        from app.platform.permissions.roles import Role

        oid = await second_org(eng)
        actor = await insert_user(eng, oid, "Outsider", Role.ADMIN, None)
    else:
        actor = {
            "south_rep": world.south1,
            "south_tl": world.tl_south,
            "north_other_rep": world.north2,
        }[who]
    before = await rows(eng, SNAP_SQL, o=world.org_id)
    ids = dict(
        deal=str(f.deal),
        step=str(f.step),
        activity=f.activity,
        line=f.line,
        doc=f.doc,
        export=f.export,
        account=str(f.account),
        user=str(world.north1.id),
    )
    id_routes = [
        (m, p)
        for m, p in op_list()
        if any(
            k in p
            for k in (
                "{deal_id}",
                "{step_id}",
                "{activity_id}",
                "{doc_id}",
                "{export_id}",
                "{account_id}",
                "{user_id}",
            )
        )
    ]
    assert len(id_routes) >= 30
    leaks = []
    for method, path in id_routes:
        url = fill(path, **ids)
        r = await client.request(
            method,
            url,
            headers=H(actor),
            json=body_for(method, path),
            params={"reason": "x"} if method == "DELETE" else None,
        )
        if r.status_code < 400 or r.status_code >= 500:
            leaks.append(f"{method} {path} -> {r.status_code} {r.text[:80]}")
        elif who == "other_org_admin" and r.status_code not in (404, 403, 422):
            leaks.append(f"{method} {path} -> {r.status_code}")
        elif r.status_code in (409,) and who != "north_other_rep":
            leaks.append(f"{method} {path} -> {r.status_code} (state revealed to an outsider)")
    after = await rows(eng, SNAP_SQL, o=world.org_id)
    assert before == after, "an outsider's request changed data"
    assert not leaks, "\n".join(leaks)


async def test_second_org_never_sees_the_first_orgs_data_in_lists(client, world, admin_engine):
    eng = admin_engine
    f = await build_fixture(client, world, eng)
    from app.platform.permissions.roles import Role

    oid = await second_org(eng)
    out_admin = await insert_user(eng, oid, "Outsider", Role.SUPER_ADMIN, None)
    secret = str(f.deal)
    for path in (
        "/deals",
        "/board",
        "/today",
        "/stages/qualification/deals",
        "/stages/won/deals",
        "/stages/lost/deals",
        "/stages/metrics",
        "/deleted/deals",
        "/products",
        "/exports/log",
        "/exports/mine",
        "/monitoring",
        "/teams-compare",
        "/settings/lost-reasons",
        "/settings/fit-checklist",
        "/settings/custom-fields",
        "/pipelines/new_business/versions",
    ):
        r = await client.get(path, headers=H(out_admin))
        assert r.status_code == 200, (path, r.status_code, r.text[:100])
        txt = r.text
        assert secret not in txt and "Voice" not in txt and str(f.activity) not in txt, path
    # the other org's pipeline is its own: a publish there leaves ours alone
    v = (await client.get("/pipelines/new_business/versions", headers=H(out_admin))).json()
    mine = (await client.get("/pipelines/new_business/versions", headers=H(world.admin))).json()
    assert {x["id"] for x in v}.isdisjoint({x["id"] for x in mine})
    r = await client.post(
        "/pipelines/new_business/versions", json={"from_version_no": 1}, headers=H(out_admin)
    )
    assert r.status_code == 200
    assert (
        len((await client.get("/pipelines/new_business/versions", headers=H(world.admin))).json())
        == 1
    )
    # org-scoped settings lists are separate
    await client.put(
        "/settings/lost-reasons", json={"items": [{"label": "Only theirs"}]}, headers=H(out_admin)
    )
    assert (
        "Only theirs"
        not in (await client.get("/settings/lost-reasons", headers=H(world.admin))).text
    )
    # the outsider cannot use our user ids for owner / team filters or profile views
    assert (
        await client.get(f"/profiles/{world.north1.id}", headers=H(out_admin))
    ).status_code == 404
    r = await client.get("/board", params={"owner_id": str(world.north1.id)}, headers=H(out_admin))
    assert r.status_code == 200 and all(not c["cards"] for c in r.json()["columns"])


# ============================================================================ mass assignment
async def test_extra_fields_never_change_protected_data(client, world, admin_engine):
    eng = admin_engine
    f = await build_fixture(client, world, eng)
    evil = {
        "org_id": str(uuid.uuid4()),
        "owner_id": str(world.sa.id),
        "status": "won",
        "stage_key": "onboarding",
        "value_paise": 1,
        "role": "super_admin",
        "is_active": False,
        "deleted_at": "2020-01-01T00:00:00Z",
        "created_by": str(world.sa.id),
        "won_at": "2020-01-01T00:00:00Z",
        "id": ZERO,
    }
    before = await rows(eng, SNAP_SQL, o=world.org_id)
    for method, path in op_list():
        if method == "GET" or method == "DELETE":
            continue
        url = fill(
            path,
            deal=str(f.deal),
            step=str(f.step),
            activity=f.activity,
            line=f.line,
            doc=f.doc,
            export=f.export,
        )
        base = body_for(method, path) or {}
        r = await client.request(method, url, headers=H(world.sa), json={**base, **evil})
        assert r.status_code < 500, (method, path, r.status_code)
        if r.status_code < 300 and path in ("/deals/{deal_id}/move",):
            pytest.fail("junk accepted by move")
    row = (
        await rows(
            eng,
            "SELECT org_id, owner_id, status, stage_key, value_paise, deleted_at, won_at FROM deals WHERE id=:d",
            d=f.deal,
        )
    )[0]
    assert row["org_id"] == world.org_id and row["owner_id"] == world.north1.id
    assert row["status"] in ("open", "lost") and row["won_at"] is None and row["deleted_at"] is None
    assert (await one(eng, "SELECT role FROM users WHERE id=:u", u=world.sa.id)) == "super_admin"
    assert await one(eng, "SELECT is_active FROM users WHERE id=:u", u=world.sa.id)
    assert before is not None
    assert not await check_invariants(eng, world.org_id)


# ================================================================================ injection
INJECT = [
    "'; DROP TABLE deals; --",
    "\" OR \"1\"=\"1",
    "1; SELECT pg_sleep(0)",
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "{{7*7}} ${7*7} %s %(x)s {0}",
    "=1+1", "@SUM(1)", "+cmd", "-2+3",
    "../../etc/passwd",
    "नमस्ते 🙏 ‮",
    "   ",
    "a" * 5000,
    "\\", "%", "_", "\\%", "[a-z]", "(?i)", ".*",
]  # fmt: skip


async def test_injection_strings_in_every_free_text_field_are_stored_inertly(
    client, world, admin_engine
):
    eng = admin_engine
    f = await build_fixture(client, world, eng)
    bad = []
    deals_before = await one(eng, "SELECT count(*) FROM deals")
    for s in INJECT:
        calls = [
            (
                "PATCH",
                f"/deals/{f.deal}",
                {
                    "pain_point": s,
                    "use_case": s,
                    "budget_source": s,
                    "scope": s,
                    "agreed_by": s,
                    "billing_contact": s,
                    "contract_term": s,
                    "billing_frequency": s,
                    "live_note": s,
                    "plan": s,
                    "pricing_model": s,
                    "reason": s,
                    "expected_close_reason": s,
                },
                world.north1,
            ),
            (
                "POST",
                f"/deals/{f.deal}/activities",
                {
                    "type": "note",
                    "discussed": s or "x",
                    "requirements": s,
                    "objections": s,
                    "decisions": s,
                    "actions": s,
                    "attendees": [s],
                    "next_step_text": s[:500] or "x",
                    "next_step_due_date": "2999-01-01",
                    "reason": s,
                },
                world.north1,
            ),
            (
                "POST",
                f"/deals/{f.deal}/next-step",
                {"text": s[:500] or "x", "due_date": "2999-01-01", "reason": s},
                world.north1,
            ),
            (
                "POST",
                f"/deals/{f.deal}/documents",
                {"kind": "invoice", "status": "draft", "number": s, "notes": s, "reason": s},
                world.north1,
            ),
            (
                "PATCH",
                f"/documents/{f.doc}",
                {"notes": s, "number": s, "reason": s},
                world.tl_north,
            ),
            ("POST", f"/deals/{f.deal}/lose", {"lost_reason": s, "notes": s}, world.north1),
            ("POST", "/exports", {"entity": "deals", "reason": s}, world.admin),
            (
                "PUT",
                "/settings/lost-reasons",
                {"items": [{"label": s or "x"}], "reason": s},
                world.admin,
            ),
            (
                "PUT",
                "/settings/fit-checklist",
                {"items": [{"label": s or "x"}], "reason": s},
                world.admin,
            ),
            (
                "PUT",
                "/settings/custom-fields",
                {
                    "fields": [
                        {
                            "entity": "deal",
                            "key": "t",
                            "label": s or "x",
                            "type": "dropdown",
                            "options": [s or "x"],
                        }
                    ],
                    "reason": s,
                },
                world.admin,
            ),
            (
                "PUT",
                "/products",
                {
                    "products": [
                        {
                            "name": s or "x",
                            "description": s,
                            "unit": "fixed",
                            "list_price_paise": 1,
                            "floor_price_paise": 0,
                        }
                    ],
                    "reason": s,
                },
                world.admin,
            ),
            (
                "POST",
                f"/deals/{f.deal}/criteria/{'need_documented'}/untick",
                {"reason": s},
                world.north1,
            ),
        ]
        for method, url, body, actor in calls:
            try:
                r = await client.request(method, url, json=body, headers=H(actor))
            except Exception as e:  # noqa: BLE001
                bad.append(f"{method} {url} {s[:15]!r}: raised {type(e).__name__}")
                continue
            if r.status_code >= 500:
                bad.append(f"{method} {url} {s[:15]!r}: {r.status_code}")
    # query parameters of every read
    qs = [
        ("/board", "q"), ("/board", "source"), ("/board", "health"), ("/stages/lead/deals", "q"), ("/stages/lead/deals", "source"),
        ("/stages/lead/deals", "health"), ("/deals", "lead_source"), ("/deals", "status"), ("/deals", "stage_key"), ("/board/cards", "column"),
        ("/products", "include_inactive"),
    ]  # fmt: skip
    for path, param in qs:
        for s in INJECT:
            params = {param: s}
            if path == "/board/cards" and param != "column":
                params["column"] = "lead"
            r = await client.get(path, params=params, headers=H(world.admin))
            if r.status_code >= 500:
                bad.append(f"GET {path}?{param}={s[:15]!r}: {r.status_code}")
    assert not bad, "\n".join(bad[:20])
    assert await one(eng, "SELECT count(*) FROM deals") >= deals_before
    # round-trip: values come back exactly as stored, never interpreted
    det = (await client.get(f"/deals/{f.deal}", headers=H(world.admin))).json()
    assert det["pain_point"] == INJECT[-1] or det["pain_point"] in INJECT
    assert not await check_invariants(eng, world.org_id)


async def test_BUG_P3_17_nul_in_other_fields(client, world, admin_engine):
    f = await build_fixture(client, world, admin_engine)
    failures = []
    for method, url, body, actor in (
        ("PATCH", f"/deals/{f.deal}", {"pain_point": "a\x00b"}, world.north1),
        (
            "POST",
            f"/deals/{f.deal}/lose",
            {"lost_reason": "Price", "notes": "a\x00b"},
            world.north1,
        ),
        ("POST", "/exports", {"entity": "deals", "reason": "a\x00b"}, world.admin),
        (
            "PUT",
            "/settings/lost-reasons",
            {"items": [{"label": "a\x00b"}], "reason": "x"},
            world.admin,
        ),
    ):
        try:
            r = await client.request(method, url, json=body, headers=H(actor))
            if r.status_code >= 500:
                failures.append(url)
        except Exception:  # noqa: BLE001
            failures.append(url)
    assert not failures, failures


# ================================================================================== paging
async def test_paging_and_limit_parameters_on_lists(client, world, admin_engine):
    for _i in range(3):
        await mkdeal(client, world, admin_engine)
    r = await client.get("/deals", params={"limit": 1}, headers=H(world.admin))
    assert len(r.json()) == 1
    assert (
        len((await client.get("/deals", params={"limit": 10**9}, headers=H(world.admin))).json())
        >= 3
    )
    assert (
        await client.get("/deals", params={"limit": "x"}, headers=H(world.admin))
    ).status_code == 422
    assert (await client.get("/deals", params={"limit": 0}, headers=H(world.admin))).json() == []
    assert (
        await client.get("/deals", params={"status": "nonsense"}, headers=H(world.admin))
    ).json() == []
    assert (
        await client.get("/deals", params={"owner_id": "nope"}, headers=H(world.admin))
    ).status_code == 422


async def test_BUG_P3_20_negative_limit_on_deals(client, world, admin_engine):
    await mkdeal(client, world, admin_engine)
    r = await client.get("/deals", params={"limit": -1}, headers=H(world.admin))
    assert r.status_code < 500


async def test_large_bodies_do_not_crash(client, world, admin_engine):
    f = await build_fixture(client, world, admin_engine)
    big = "x" * 3_000_000
    for method, url, body in (
        ("PATCH", f"/deals/{f.deal}", {"pain_point": big}),
        (
            "POST",
            f"/deals/{f.deal}/activities",
            {
                "type": "note",
                "discussed": big,
                "next_step_text": "x",
                "next_step_due_date": "2999-01-01",
            },
        ),
        (
            "POST",
            f"/deals/{f.deal}/documents",
            {"kind": "invoice", "status": "draft", "notes": big},
        ),
        ("POST", f"/deals/{f.deal}/lose", {"lost_reason": "Price", "notes": big}),
    ):
        r = await client.request(method, url, json=body, headers=H(world.sa))
        assert r.status_code < 500, (url, r.status_code)
    # many ids in a bulk request, a deeply nested JSON, and a wrong content type
    r = await client.post(
        "/deals/bulk/lose",
        json={"deal_ids": [ZERO] * 500, "lost_reason": "Price"},
        headers=H(world.sa),
    )
    assert r.status_code == 200 and len(r.json()["failed"]) == 500
    r = await client.post(
        f"/deals/{f.deal}/move",
        content="to_stage=lead",
        headers={**H(world.sa), "content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 422
    r = await client.post(
        f"/deals/{f.deal}/move",
        content="<xml/>",
        headers={**H(world.sa), "content-type": "text/xml"},
    )
    assert r.status_code == 422
    r = await client.post(
        f"/deals/{f.deal}/move",
        content=b'{"to_stage": ' * 300,
        headers={**H(world.sa), "content-type": "application/json"},
    )
    assert r.status_code in (400, 422)
    r = await client.post(
        f"/deals/{f.deal}/move",
        content=b"\xff\xfe\x00",
        headers={**H(world.sa), "content-type": "application/json"},
    )
    assert r.status_code in (400, 422)


async def test_concurrent_mixed_reads_never_error(client, world, admin_engine):
    f = await build_fixture(client, world, admin_engine)
    paths = [
        "/board",
        "/today",
        f"/deals/{f.deal}",
        "/stages/qualification/deals",
        "/stages/metrics",
        f"/deals/{f.deal}/lines",
        f"/deals/{f.deal}/activities",
        "/monitoring",
        "/teams-compare",
        "/exports/log",
    ]
    res = await asyncio.gather(*(client.get(p, headers=H(world.admin)) for p in paths * 6))
    assert all(r.status_code == 200 for r in res), [
        (r.request.url.path, r.status_code) for r in res if r.status_code != 200
    ]


async def test_BUG_P3_29_token_without_email_claim(client, world):
    provider = get_auth_provider()
    now = datetime.now(UTC)
    tok = jwt.encode(
        {"sub": "x", "iat": now, "exp": now + timedelta(hours=1)},
        provider._secret,
        algorithm="HS256",
    )
    r = await client.get("/board", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
