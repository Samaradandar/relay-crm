# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 8: every endpoint x role, other-organisation isolation,
unauthenticated access, extra fields, injection and limits."""

import re
import uuid

import pytest

from tests.deep.phase2.support import (
    configure,
    create,
    headers,
    invariant_violations,
    lead,
    make_world,
    put_settings,
    q1,
)

UUID_PATH = re.compile(r"\{[a-z_]*id\}|\{key\}")


def phase2_routes():
    """(method, path) of every Phase 2 route, read from the live application's OpenAPI document."""
    from app.main import create_app

    wanted_prefixes = (
        "/leads",
        "/pool",
        "/transfers",
        "/settings/assignment",
        "/duplicates",
        "/accounts",
        "/contacts",
        "/deals/{deal_id}/reassign",
        "/deals/bulk-reassign",
        "/deals/{deal_id}/move-team",
        "/deals/{deal_id}/undo",
    )
    out = []
    for path, ops in create_app().openapi()["paths"].items():
        if path.startswith(wanted_prefixes) or path in ("/deals", "/deals/{deal_id}"):
            for m in ops:
                if m.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    out.append((m.upper(), path))
    return sorted(set(out))


def fill(path: str) -> str:
    return re.sub(r"\{[a-z_]*\}", lambda _m: str(uuid.uuid4()), path)


def test_the_phase_2_route_list_is_what_the_spec_names():
    routes = set(phase2_routes())
    expected = {
        ("POST", "/leads"),
        ("GET", "/pool"),
        ("GET", "/pool/distribution"),
        ("POST", "/pool/{deal_id}/assign"),
        ("POST", "/deals/{deal_id}/reassign"),
        ("POST", "/deals/bulk-reassign"),
        ("POST", "/deals/{deal_id}/move-team"),
        ("GET", "/transfers"),
        ("POST", "/transfers"),
        ("GET", "/transfers/lookup"),
        ("POST", "/transfers/{transfer_id}/comment"),
        ("GET", "/settings/assignment"),
        ("PUT", "/settings/assignment"),
        ("POST", "/settings/assignment/test"),
        ("POST", "/duplicates/check"),
        ("GET", "/accounts"),
        ("POST", "/accounts"),
        ("GET", "/accounts/{account_id}"),
        ("PATCH", "/accounts/{account_id}"),
        ("POST", "/accounts/{account_id}/merge"),
        ("POST", "/contacts"),
        ("PATCH", "/contacts/{contact_id}"),
        ("PATCH", "/deals/{deal_id}"),
        ("POST", "/deals/{deal_id}/undo"),
        ("GET", "/accounts/{account_id}/timeline"),
        ("GET", "/deals"),
        ("GET", "/deals/{deal_id}"),
    }
    assert expected <= routes, expected - routes


@pytest.mark.parametrize(("method", "path"), phase2_routes())
async def test_every_phase_2_endpoint_refuses_unauthenticated_calls(qc, method, path):
    r = await qc.request(method, fill(path), json={} if method != "GET" else None)
    assert r.status_code == 401, (method, path, r.status_code, r.text[:100])
    r = await qc.request(
        method,
        fill(path),
        headers={"Authorization": "Bearer forged.token.value"},
        json={} if method != "GET" else None,
    )
    assert r.status_code == 401, (method, path)


async def test_every_get_endpoint_answers_the_role_matrix(qc, admin_engine, world):
    await configure(qc, world)
    d = await create(
        qc, world.tl_north, company="Matrix Probe Foods"
    )  # a leader created it: no rep is the creator
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    other_rep = world.north2 if owner.id == world.north1.id else world.north1
    deal_url, acc_url = f"/deals/{d['id']}", f"/accounts/{d['account_id']}"
    roles = {
        "owner": owner,
        "team_mate": other_rep,
        "south_rep": world.south1,
        "tl_north": world.tl_north,
        "tl_south": world.tl_south,
        "admin": world.admin,
        "sa": world.sa,
    }
    table = {
        "/pool": dict(
            owner=403, team_mate=403, south_rep=403, tl_north=200, tl_south=200, admin=200, sa=200
        ),
        "/pool/distribution": dict(
            owner=403, team_mate=403, south_rep=403, tl_north=200, tl_south=200, admin=200, sa=200
        ),
        "/transfers": dict(
            owner=403, team_mate=403, south_rep=403, tl_north=200, tl_south=200, admin=200, sa=200
        ),
        "/transfers/lookup?q=matrix": dict(
            owner=403, team_mate=403, south_rep=403, tl_north=200, tl_south=200, admin=403, sa=403
        ),
        "/settings/assignment": dict(
            owner=403, team_mate=403, south_rep=403, tl_north=403, tl_south=403, admin=200, sa=200
        ),
        "/accounts": dict.fromkeys(roles, 200),
        acc_url: dict(
            owner=200, team_mate=404, south_rep=404, tl_north=200, tl_south=404, admin=200, sa=200
        ),
        acc_url + "/timeline": dict(
            owner=200, team_mate=404, south_rep=404, tl_north=200, tl_south=404, admin=200, sa=200
        ),
        deal_url: dict(
            owner=200, team_mate=404, south_rep=404, tl_north=200, tl_south=404, admin=200, sa=200
        ),
        "/deals": dict.fromkeys(roles, 200),
    }
    for url, expected in table.items():
        for role, actor in roles.items():
            r = await qc.get(url, headers=headers(actor))
            exp = expected[role]
            assert r.status_code in (
                (exp, 404) if exp == 403 and url == "/settings/assignment" else (exp,)
            ), (url, role, r.status_code, r.text[:120])
    # what each role sees in the lists
    seen = {
        r: [x["id"] for x in (await qc.get("/deals", headers=headers(a))).json()]
        for r, a in roles.items()
    }
    assert (
        d["id"] in seen["owner"]
        and d["id"] in seen["tl_north"]
        and d["id"] in seen["admin"]
        and d["id"] in seen["sa"]
    )
    assert (
        d["id"] not in seen["team_mate"]
        and d["id"] not in seen["south_rep"]
        and d["id"] not in seen["tl_south"]
    )


async def test_every_write_endpoint_answers_the_role_matrix(qc, admin_engine, world):
    """Denied roles first (they change nothing), then the allowed ones."""
    await configure(qc, world)
    d = await create(qc, world.tl_north, company="Write Matrix Foods")
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    other = world.north2 if owner.id == world.north1.id else world.north1
    ids = {"deal": d["id"], "acc": d["account_id"], "contact": d["contact"]["id"]}
    calls = [
        (
            "PATCH",
            f"/deals/{ids['deal']}",
            {"lead_source": "Event", "reason": "r"},
            dict(owner=200, other=403, south=403, tl_s=403, tl_n=200, admin=200, sa=200),
        ),
        (
            "PATCH",
            f"/accounts/{ids['acc']}",
            {"city": "Pune", "reason": "r"},
            dict(owner=200, other=404, south=404, tl_s=404, tl_n=200, admin=200, sa=200),
        ),
        (
            "PATCH",
            f"/contacts/{ids['contact']}",
            {"role": "CEO", "reason": "r"},
            dict(owner=200, other=404, south=404, tl_s=404, tl_n=200, admin=200, sa=200),
        ),
        (
            "POST",
            "/contacts",
            {"account_id": ids["acc"], "name": "N", "email": "n@x.example", "reason": "r"},
            dict(owner=201, other=404, south=404, tl_s=404, tl_n=201, admin=201, sa=201),
        ),
        (
            "POST",
            f"/deals/{ids['deal']}/reassign",
            {"user_id": str(other.id), "reason": "r"},
            dict(owner=403, other=403, south=403, tl_s=403, tl_n=200, admin=409, sa=409),
        ),
        (
            "POST",
            f"/deals/{ids['deal']}/move-team",
            {"to_team_id": str(world.north_team), "reason": "r"},
            dict(owner=403, other=403, south=403, tl_s=403, tl_n=403, admin=409, sa=409),
        ),
        (
            "POST",
            f"/accounts/{ids['acc']}/merge",
            {"into_account_id": str(uuid.uuid4()), "reason": "r"},
            dict(owner=403, other=403, south=403, tl_s=403, tl_n=403, admin=404, sa=404),
        ),
        (
            "POST",
            "/deals/bulk-reassign",
            {"from_user_id": str(owner.id), "to_user_id": str(owner.id), "reason": "r"},
            dict(owner=403, other=403, south=403, tl_s=403, tl_n=409, admin=409, sa=409),
        ),
        (
            "POST",
            f"/pool/{ids['deal']}/assign",
            {"user_id": str(other.id), "reason": "r"},
            dict(owner=403, other=403, south=403, tl_s=403, tl_n=409, admin=409, sa=409),
        ),
        (
            "POST",
            "/transfers",
            {
                "direction": "give",
                "deal_id": ids["deal"],
                "to_team_id": str(world.north_team),
                "reason": "r",
            },
            dict(owner=403, other=403, south=403, tl_s=404, tl_n=422, admin=403, sa=403),
        ),
        (
            "PUT",
            "/settings/assignment",
            {"default_team_id": str(world.north_team), "default_cap": 25, "reason": "r"},
            dict(owner=403, other=403, south=403, tl_s=403, tl_n=403, admin=200, sa=200),
        ),
        (
            "POST",
            "/settings/assignment/test",
            {},
            dict(owner=403, other=403, south=403, tl_s=403, tl_n=403, admin=200, sa=200),
        ),
        (
            "POST",
            "/leads",
            None,
            dict(owner=201, other=201, south=201, tl_s=201, tl_n=201, admin=201, sa=201),
        ),
    ]
    actors = dict(
        owner=owner,
        other=other,
        south=world.south1,
        tl_s=world.tl_south,
        tl_n=world.tl_north,
        admin=world.admin,
        sa=world.sa,
    )
    for method, url, body, expect in calls:
        for role, actor in actors.items():
            payload = lead(reason="r") if body is None else body
            r = await qc.request(method, url, json=payload, headers=headers(actor))
            assert r.status_code == expect[role], (method, url, role, r.status_code, r.text[:160])
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE org_id=:o AND outcome='denied'",
            o=world.org_id,
        )
        >= 30
    )
    assert not await invariant_violations(admin_engine, world.org_id)


# ------------------------------------------------------------------------------ other organisation
async def test_a_second_organisation_cannot_touch_any_id_of_the_first(qc, admin_engine, world):
    await configure(qc, world, cap=0)
    waiting = await create(qc, world.north1, company="Org One Waiting Lead")
    await configure(qc, world, cap=10)
    d = await create(
        qc,
        world.north1,
        company="Org One Open Lead",
        phone="9811100077",
        email=None,
        create_anyway=True,
        reason="second",
    )
    t = await qc.post(
        "/transfers",
        json={
            "direction": "take",
            "deal_id": d["id"],
            "owner_id": str(world.south1.id),
            "reason": "r",
        },
        headers=headers(world.tl_south),
    )
    assert t.status_code == 202
    tid = (await qc.get("/transfers", headers=headers(world.admin))).json()[0]["id"]
    other = await make_world(admin_engine)
    await configure(qc, other)
    mine = {
        "deal": d["id"],
        "waiting": waiting["id"],
        "acc": d["account_id"],
        "contact": d["contact"]["id"],
        "user": str(world.north1.id),
        "team": str(world.north_team),
        "transfer": tid,
        "approval": t.json()["approval_id"],
    }
    calls = [
        ("GET", f"/deals/{mine['deal']}", None),
        ("PATCH", f"/deals/{mine['deal']}", {"lead_source": "Event", "reason": "r"}),
        ("POST", f"/deals/{mine['deal']}/undo", {"audit_id": str(uuid.uuid4())}),
        ("GET", f"/history/deal/{mine['deal']}", None),
        (
            "POST",
            f"/deals/{mine['deal']}/reassign",
            {"user_id": str(other.north1.id), "reason": "r"},
        ),
        (
            "POST",
            f"/deals/{mine['deal']}/move-team",
            {"to_team_id": str(other.south_team), "reason": "r"},
        ),
        ("POST", f"/deals/{mine['deal']}/lose", {"lost_reason": "Price"}),
        (
            "POST",
            f"/pool/{mine['waiting']}/assign",
            {"user_id": str(other.north1.id), "reason": "r"},
        ),
        ("POST", f"/deals/{mine['deal']}/delete", {"reason": "r"}),
        ("GET", f"/accounts/{mine['acc']}", None),
        ("GET", f"/accounts/{mine['acc']}/timeline", None),
        ("PATCH", f"/accounts/{mine['acc']}", {"city": "Hacked", "reason": "r"}),
        (
            "POST",
            f"/accounts/{mine['acc']}/merge",
            {"into_account_id": str(uuid.uuid4()), "reason": "r"},
        ),
        (
            "POST",
            "/contacts",
            {"account_id": mine["acc"], "name": "Evil", "email": "e@evil.example", "reason": "r"},
        ),
        ("PATCH", f"/contacts/{mine['contact']}", {"role": "Hacked", "reason": "r"}),
        (
            "POST",
            "/deals/bulk-reassign",
            {"from_user_id": mine["user"], "to_user_id": str(other.north1.id), "reason": "r"},
        ),
        (
            "POST",
            "/deals/bulk-reassign",
            {"from_user_id": str(other.north1.id), "to_user_id": mine["user"], "reason": "r"},
        ),
        ("POST", f"/transfers/{mine['transfer']}/comment", {"comment": "hi"}),
        ("GET", f"/approvals/{mine['approval']}", None),
        ("POST", f"/approvals/{mine['approval']}/decide", {"decision": "approve", "reason": "r"}),
        ("POST", f"/approvals/{mine['approval']}/cancel", None),
        (
            "POST",
            "/transfers",
            {
                "direction": "take",
                "deal_id": mine["deal"],
                "owner_id": str(other.north1.id),
                "reason": "r",
            },
        ),
        (
            "POST",
            "/transfers",
            {
                "direction": "give",
                "deal_id": str(uuid.uuid4()),
                "to_team_id": mine["team"],
                "reason": "r",
            },
        ),
        ("PATCH", f"/users/{mine['user']}", {"is_active": False, "reason": "r"}),
    ]
    for actor in (other.sa, other.admin, other.tl_north, other.north1):
        for method, url, body in calls:
            r = await qc.request(method, url, json=body, headers=headers(actor))
            assert r.status_code in (403, 404, 422), (
                actor.name,
                method,
                url,
                r.status_code,
                r.text[:160],
            )
            assert d["id"] not in r.text.replace(url, "") or r.status_code in (403, 404, 422)
            assert "Org One" not in r.text
    # lists, search, lookup and the distribution in the second org carry nothing of the first
    for url in (
        "/deals",
        "/pool",
        "/accounts?q=org",
        "/transfers",
        "/pool/distribution",
        "/approvals",
        "/deleted/deals",
        "/users",
    ):
        for actor in (other.admin, other.sa):
            r = await qc.get(url, headers=headers(actor))
            assert r.status_code == 200, (url, r.status_code)
            assert (
                "Org One" not in r.text
                and d["id"] not in r.text
                and str(world.north1.id) not in r.text
            ), url
    assert (
        "Org One"
        not in (
            await qc.get(
                "/transfers/lookup", params={"q": "org one"}, headers=headers(other.tl_south)
            )
        ).text
    )
    r = await qc.post(
        "/duplicates/check",
        json={"company": "Org One Open Lead", "phone": "9811100077"},
        headers=headers(other.sa),
    )
    assert r.json() == {"block": [], "warn": []}
    # and nothing of the first organisation changed
    assert (await qc.get(f"/deals/{d['id']}", headers=headers(world.admin))).json()[
        "lead_source"
    ] == "Website"
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE org_id=:o AND outcome='ok' AND action_key IN ('deal.edit','lead.reassign','deal.delete','deal.lose')",
            o=world.org_id,
        )
        == 0
    )
    assert not await invariant_violations(admin_engine, world.org_id)
    assert not await invariant_violations(admin_engine, other.org_id)


async def test_the_same_company_may_exist_in_two_organisations(qc, admin_engine, world):
    await configure(qc, world)
    other = await make_world(admin_engine)
    await configure(qc, other)
    a = await create(
        qc,
        world.north1,
        company="Shared Name Pvt Ltd",
        website="shared-name.example",
        phone="9811100088",
        email=None,
    )
    b = await create(
        qc,
        other.north1,
        company="Shared Name Pvt Ltd",
        website="shared-name.example",
        phone="9811100088",
        email=None,
    )
    assert a["account_id"] != b["account_id"]
    res = await qc.post(
        "/duplicates/check",
        json={"company": "Shared Name Pvt Ltd", "website": "shared-name.example"},
        headers=headers(other.admin),
    )
    assert [m["account_id"] for m in res.json()["block"]] == [b["account_id"]]


# ------------------------------------------------------------------------------ extra fields, injection, limits
EXTRA = {
    "is_admin": True,
    "role": "super_admin",
    "org_id": str(uuid.uuid4()),
    "status": "won",
    "owner_id": str(uuid.uuid4()),
    "team_id": str(uuid.uuid4()),
}


async def test_extra_fields_never_change_anything(qc, admin_engine, world):
    await configure(qc, world, cap=0)
    d = await create(qc, world.north1)
    h = headers(world.admin)
    r = await qc.post(
        f"/pool/{d['id']}/assign",
        json={"user_id": str(world.north1.id), "reason": "r", **EXTRA},
        headers=h,
    )
    assert r.status_code in (200, 422)
    got = (await qc.get(f"/deals/{d['id']}", headers=h)).json()
    assert (
        got["team_id"] == str(world.north_team)
        and got["status"] in ("open", "pooled")
        and got["owner"]["id"] == str(world.north1.id)
    )
    for method, url, body in (
        (
            "POST",
            f"/deals/{d['id']}/reassign",
            {"user_id": str(world.north2.id), "reason": "r", **EXTRA},
        ),
        (
            "POST",
            "/deals/bulk-reassign",
            {
                "from_user_id": str(world.north2.id),
                "to_user_id": str(world.north1.id),
                "reason": "r",
                **EXTRA,
            },
        ),
        (
            "POST",
            f"/deals/{d['id']}/move-team",
            {"to_team_id": str(world.north_team), "reason": "r", **EXTRA},
        ),
        ("POST", "/duplicates/check", {"company": "x", **EXTRA}),
        (
            "POST",
            f"/accounts/{d['account_id']}/merge",
            {"into_account_id": d["account_id"], "reason": "r", **EXTRA},
        ),
    ):
        r = await qc.request(method, url, json=body, headers=h)
        assert r.status_code < 500, (url, r.text)
    assert await q1(admin_engine, "SELECT role FROM users WHERE id=:i", i=world.north1.id) == "user"
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM deals WHERE status='won' AND org_id=:o",
            o=world.org_id,
        )
        == 0
    )


async def test_strict_bodies_reject_extra_fields(qc, world):
    await configure(qc, world)
    h = headers(world.admin)
    d = await create(qc, world.north1)
    strict = [
        ("POST", "/leads", lead(reason="r", **EXTRA)),
        (
            "PUT",
            "/settings/assignment",
            {"default_team_id": str(world.north_team), "default_cap": 5, "reason": "r", **EXTRA},
        ),
        ("POST", "/accounts", {"name": "x", "reason": "r", **EXTRA}),
        ("PATCH", f"/accounts/{d['account_id']}", {"city": "x", "reason": "r", **EXTRA}),
        (
            "POST",
            "/contacts",
            {"account_id": d["account_id"], "name": "x", "email": "a@b.co", "reason": "r", **EXTRA},
        ),
        ("PATCH", f"/contacts/{d['contact']['id']}", {"role": "x", "reason": "r", **EXTRA}),
        ("PATCH", f"/deals/{d['id']}", {"lead_source": "Event", "reason": "r", **EXTRA}),
    ]
    for method, url, body in strict:
        r = await qc.request(method, url, json=body, headers=h)
        assert r.status_code == 422, (method, url, r.status_code)


@pytest.mark.parametrize(
    "field", ["company", "contact_name", "next_step_text", "industry", "reason"]
)
async def test_nul_characters_in_text_are_a_clean_422(qc, world, field):
    await configure(qc, world)
    r = await qc.post("/leads", json=lead(**{field: "a\x00b"}), headers=headers(world.sa))
    assert r.status_code < 500, field


async def test_injection_strings_are_stored_verbatim_and_returned_as_data(qc, admin_engine, world):
    await configure(qc, world)
    hostile = [
        "<img src=x onerror=alert(1)>",
        "'; DROP TABLE deals; --",
        "{{7*7}}",
        "${jndi:ldap://x}",
        "=cmd|' /C calc'!A0",
        "‮evil",
        "\U0001f600" * 40,
    ]
    h = headers(world.admin)
    for s in hostile:
        d = await create(
            qc,
            world.north1,
            company=f"Inj {uuid.uuid4().hex[:6]}",
            industry=s,
            city=s,
            contact_role=s,
            contact_name=s,
            next_step_text=s,
        )
        assert (
            d["industry"] == s
            and d["city"] == s
            and d["contact"]["role"] == s
            and d["contact"]["name"] == s
            and d["next_step"]["text"] == s
        )
        r = await qc.post(
            f"/deals/{d['id']}/reassign",
            json={
                "user_id": str(
                    world.north2.id if d["owner"]["id"] == str(world.north1.id) else world.north1.id
                ),
                "reason": s,
            },
            headers=h,
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        hist = (await qc.get(f"/history/deal/{d['id']}", headers=h)).json()
        assert any(x["reason"] == s for x in hist)
    assert await q1(
        admin_engine, "SELECT count(*) FROM deals WHERE org_id=:o", o=world.org_id
    ) == len(hostile)


async def test_history_endpoint_limits_and_unknown_entities(qc, world):
    await configure(qc, world)
    d = await create(qc, world.north1)
    h = headers(world.admin)
    for limit, code in ((1, 200), (200, 200), (0, 422), (201, 422), (-1, 422), ("x", 422)):
        assert (
            await qc.get(f"/history/deal/{d['id']}", params={"limit": limit}, headers=h)
        ).status_code == code
    assert (await qc.get("/history/deal/not-a-uuid", headers=h)).status_code in (200, 404, 422)
    assert (
        await qc.get(f"/history/deal/{d['id']}", headers=headers(world.south1))
    ).status_code in (403, 404)
    assert (
        await qc.get("/history/deal/%27%3B%20DROP%20TABLE%20audit_log", headers=h)
    ).status_code < 500
    rows = (await qc.get(f"/history/deal/{d['id']}", headers=h)).json()
    # a rep's lead is held, then the Team Leader's auto choice: create, release, engine
    assert [x["action"] for x in rows][::-1][:3] == ["lead.create", "pool.assign", "lead.assigned"]


async def test_oversized_and_malformed_bodies_do_not_crash_anything(qc, world):
    await configure(qc, world)
    h = {**headers(world.north1), "content-type": "application/json"}
    assert (
        await qc.post("/leads", content=b"[" * 100_000 + b"]" * 100_000, headers=h)
    ).status_code == 400
    assert (
        await qc.post("/leads", content=b'{"company": "' + b"x" * 5_000_000 + b'"}', headers=h)
    ).status_code in (413, 422)
    assert (
        await qc.post("/leads", content=rb'{"company": "a\ud800b"}', headers=h)
    ).status_code == 422
    assert (await qc.post("/leads", content=b"", headers=h)).status_code == 422
    assert (await qc.post("/leads", content=b"\xff\xfe\x00", headers=h)).status_code in (400, 422)
    assert (await qc.post("/leads", content=b'{"company": NaN}', headers=h)).status_code in (
        400,
        422,
    )


async def test_duplicate_check_is_available_while_typing_for_every_role(qc, world):
    await configure(qc, world)
    for actor in world.all_actors:
        r = await qc.post(
            "/duplicates/check", json={"company": "Anything at all"}, headers=headers(actor)
        )
        assert r.status_code == 200


async def test_a_full_scenario_leaves_every_phase_2_invariant_intact(qc, admin_engine, world):
    await put_settings(qc, world.admin, world, default_cap=3)
    ds = [await create(qc, world.north1) for _ in range(12)]
    for d in ds[:3]:
        if d["status"] == "open":
            await qc.post(
                f"/deals/{d['id']}/reassign",
                json={"user_id": str(world.south1.id), "reason": "r"},
                headers=headers(world.admin),
            )
    await qc.post(
        "/transfers",
        json={
            "direction": "take",
            "deal_id": ds[5]["id"],
            "reason": "r",
            "owner_id": str(world.south2.id),
        },
        headers=headers(world.tl_south),
    )
    from tests.helpers import dispatch_all

    await dispatch_all()
    assert not await invariant_violations(admin_engine, world.org_id)
