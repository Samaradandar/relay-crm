# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 1: identity, login, tokens and /me."""

import uuid

import pytest
from sqlalchemy import text

from app.main import create_app
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_table
from app.platform.permissions.roles import ActionClass, Role
from app.platform.reasons.policy import reason_rule
from tests.deep.phase1.kit import ROLES, actor_by_role, audit_rows, bearer, h, token_for

PUBLIC = {
    ("GET", "/health"),
    ("GET", "/health/ready"),  # readiness for the uptime check: public like /health
    ("POST", "/auth/dev/login"),
    ("GET", "/auth/dev/users"),
    # Phase 7 P7-01: the public website-form endpoint -- always 202, no auth, by design.
    ("POST", "/capture/web/{source_key}"),
    # Phase 8 ST-03: Google's push webhook -- authenticated by channel id/token headers, not
    # a bearer token (Google doesn't have one); invalid/missing headers get 404, see router.py.
    ("POST", "/integrations/google/calendar/push"),
}


# ------------------------------------------------------------------ dev login
async def test_dev_login_unknown_inactive_deleted_padded_mixed_case(client, world, admin_engine):
    r = await client.post("/auth/dev/login", json={"email": "ghost@example.com"})
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"

    # mixed case works and gives a token that works
    r = await client.post("/auth/dev/login", json={"email": world.north1.email.upper()})
    assert r.status_code == 200
    me = await client.get("/me", headers=bearer(r.json()["token"]))
    assert me.status_code == 200 and me.json()["id"] == str(world.north1.id)

    # padded email: the user typed a trailing space (copy and paste). Either accepted or a clean 4xx,
    # never a 500.
    r = await client.post("/auth/dev/login", json={"email": f"  {world.north1.email}  "})
    assert r.status_code in (200, 401, 422), r.text

    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE users SET is_active=false WHERE id=:i"), {"i": world.north2.id}
        )
        await c.execute(
            text("UPDATE users SET deleted_at=now() WHERE id=:i"), {"i": world.south2.id}
        )
    r = await client.post("/auth/dev/login", json={"email": world.north2.email})
    assert r.status_code == 401 and "deactivated" in r.json()["message"]
    r = await client.post("/auth/dev/login", json={"email": world.south2.email})
    assert r.status_code == 401 and "No CRM account" in r.json()["message"]


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"email": None},
        {"email": 5},
        {"email": ["a@b.c"]},
        {"email": {"a": 1}},
        "text",
        [1],
    ],
)
async def test_dev_login_bad_bodies_are_422_never_500(client, world, body):
    r = await client.post("/auth/dev/login", json=body)
    assert r.status_code == 422, r.text
    j = r.json()
    assert set(j) == {"code", "message", "details"} and j["code"] == "validation_error"


async def test_dev_login_wrong_content_type_and_garbage(client, world):
    r = await client.post(
        "/auth/dev/login", content=b"email=x", headers={"content-type": "text/plain"}
    )
    assert r.status_code == 422
    r = await client.post(
        "/auth/dev/login", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    r = await client.post("/auth/dev/login", json={"email": "a" * 100_000})
    assert r.status_code in (401, 422)
    r = await client.post("/auth/dev/login", json={"email": "x@y.z' OR '1'='1"})
    assert r.status_code == 401


async def test_dev_login_writes_one_audit_row_and_last_login(client, world):
    before = await audit_rows(world.org_id, action_key="auth.login")
    r = await client.post("/auth/dev/login", json={"email": world.tl_north.email})
    assert r.status_code == 200
    after = await audit_rows(world.org_id, action_key="auth.login")
    assert len(after) == len(before) + 1
    row = after[-1]
    assert row["outcome"] == "ok" and str(row["actor_id"]) == str(world.tl_north.id)
    me = await client.get("/me", headers=bearer(r.json()["token"]))
    assert me.json()["last_login_at"] is not None


async def test_failed_login_is_not_audited_anywhere(client, world):
    """A failed login for an unknown email has no org to attach to; for an inactive user there is
    an org. PRD 4: denied attempts are logged. Documented as a NOTE in the report."""
    n = len(await audit_rows(world.org_id))
    await client.post("/auth/dev/login", json={"email": "ghost@example.com"})
    assert len(await audit_rows(world.org_id)) == n


# ------------------------------------------------------------------ tokens
async def test_token_missing_malformed_wrong_scheme(client, world):
    assert (await client.get("/me")).status_code == 401
    for hdr in (
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic abc"},
        {"Authorization": "Token abc"},
        {"Authorization": "Bearer not.a.jwt"},
        {"Authorization": "Bearer " + "A" * 5000},
        {"Authorization": "Bearer éé"} if False else {"Authorization": "Bearer ..."},
    ):
        r = await client.get("/me", headers=hdr)
        assert r.status_code == 401, (hdr, r.status_code)
        assert set(r.json()) == {"code", "message", "details"}
    # lowercase scheme is accepted by design
    ok = await client.get("/me", headers={"Authorization": "bearer " + token_for(world.sa.email)})
    assert ok.status_code == 200


async def test_token_expired_wrong_key_none_alg_and_missing_claims(client, world):
    e = world.sa.email
    assert (await client.get("/me", headers=bearer(token_for(e, hours=-1)))).status_code == 401
    assert (
        await client.get("/me", headers=bearer(token_for(e, secret="x" * 40)))
    ).status_code == 401
    # unsigned ("alg none") token
    import base64
    import json

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    none_tok = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'email': e, 'sub': 'x'})}."
    assert (await client.get("/me", headers=bearer(none_tok))).status_code == 401
    # a different algorithm than HS256 (HS512 with the right key) must be refused
    import hashlib
    import hmac

    from app.core.config import get_settings

    h512 = (
        f"{b64({'alg': 'HS512', 'typ': 'JWT'})}.{b64({'email': e, 'sub': 'x', 'exp': 4102444800})}"
    )
    sig = hmac.new(get_settings().dev_jwt_secret.encode(), h512.encode(), hashlib.sha512).digest()
    forged512 = h512 + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    assert (await client.get("/me", headers=bearer(forged512))).status_code == 401


async def test_token_role_claim_tampering_is_ignored(client, world):
    """The role comes from the database on every request, never from the token."""
    tok = token_for(world.north1.email, extra={"role": "super_admin", "org_id": str(uuid.uuid4())})
    me = await client.get("/me", headers=bearer(tok))
    assert me.status_code == 200 and me.json()["role"] == "user"
    assert me.json()["org_id"] == str(world.org_id)
    r = await client.get("/admin/failed-jobs", headers=bearer(tok))
    assert r.status_code == 403
    # an edited payload with the old signature is rejected
    import base64
    import json

    head, payload, sig = tok.split(".")
    pad = "=" * (-len(payload) % 4)
    body = json.loads(base64.urlsafe_b64decode(payload + pad))
    body["email"] = world.sa.email
    forged = (
        head
        + "."
        + base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()
        + "."
        + sig
    )
    assert (await client.get("/me", headers=bearer(forged))).status_code == 401


async def test_deactivated_users_token_stops_on_next_request(client, world, admin_engine):
    t = h(world.north1)
    assert (await client.get("/me", headers=t)).status_code == 200
    r = await client.patch(
        f"/users/{world.north1.id}",
        json={"is_active": False, "reason": "left"},
        headers=h(world.admin),
    )
    assert r.status_code == 200
    r = await client.get("/me", headers=t)
    assert r.status_code == 401 and "deactivated" in r.json()["message"]
    for path in ("/users", "/notifications", "/approvals", "/events"):
        assert (await client.get(path, headers=t)).status_code == 401
    # and works again on reactivation (same token)
    r = await client.patch(
        f"/users/{world.north1.id}",
        json={"is_active": True, "reason": "back"},
        headers=h(world.admin),
    )
    assert r.status_code == 200
    assert (await client.get("/me", headers=t)).status_code == 200


async def test_deleted_user_token_stops(client, world, admin_engine):
    t = h(world.south2)
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE users SET deleted_at=now() WHERE id=:i"), {"i": world.south2.id}
        )
    assert (await client.get("/me", headers=t)).status_code == 401


async def test_role_change_applies_at_once(client, world):
    t = h(world.north2)
    assert (await client.get("/admin/failed-jobs", headers=t)).status_code == 403
    me = (await client.get("/me", headers=t)).json()
    assert me["role"] == "user"
    # an Admin promotes the user to TL of... north already has a leader, so demote the leader first
    r = await client.patch(
        f"/users/{world.tl_north.id}",
        json={"role": "user", "reason": "swap"},
        headers=h(world.admin),
    )
    assert r.status_code == 200, r.text
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"role": "team_leader", "reason": "promote"},
        headers=h(world.admin),
    )
    assert r.status_code == 200, r.text
    me = (await client.get("/me", headers=t)).json()
    assert me["role"] == "team_leader"  # same token, new role at once
    perms = {p["key"]: p for p in me["permissions"]}
    assert perms["pool.view"]["scope"] == "team"
    # and the approvals inbox answers differently now
    assert (await client.get("/approvals?tab=pending", headers=t)).status_code == 200


async def test_user_in_no_team_cannot_log_in_after_losing_team(client, world, admin_engine):
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE users SET team_id=NULL WHERE id=:i"), {"i": world.north2.id})
    r = await client.get("/me", headers=h(world.north2))
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"
    r = await client.post("/auth/dev/login", json={"email": world.north2.email})
    assert r.status_code == 403


# ------------------------------------------------------------------ every route needs a token
def _routes():
    app = create_app()
    schema = app.openapi()
    out = []
    for path, item in schema["paths"].items():
        for method in item:
            if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.append((method.upper(), path))
    return out


async def test_every_route_but_health_and_login_answers_401_without_a_token(client, world):
    routes = _routes()
    assert len(routes) > 40
    bad = []
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        url = path
        for seg in ("{",):
            while seg in url:
                a = url.index("{")
                b = url.index("}", a)
                url = url[:a] + str(uuid.uuid4()) + url[b + 1 :]
        r = await client.request(method, url, json={} if method != "GET" else None)
        if r.status_code != 401:
            bad.append((method, path, r.status_code))
        else:
            assert set(r.json()) == {"code", "message", "details"}
    assert not bad, bad


async def test_every_route_rejects_a_garbage_and_an_expired_token(client, world):
    expired = bearer(token_for(world.sa.email, hours=-2))
    garbage = bearer("garbage")
    bad = []
    for method, path in _routes():
        if (method, path) in PUBLIC:
            continue
        url = path
        while "{" in url:
            a = url.index("{")
            b = url.index("}", a)
            url = url[:a] + str(uuid.uuid4()) + url[b + 1 :]
        for hdr in (expired, garbage):
            r = await client.request(method, url, headers=hdr, json={} if method != "GET" else None)
            if r.status_code != 401:
                bad.append((method, path, r.status_code))
    assert not bad, bad


async def test_public_routes_are_exactly_the_documented_ones(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    # the public list of dev users names every account (dev provider only, never in production)
    r = await client.get("/auth/dev/users")
    assert r.status_code == 200


# ------------------------------------------------------------------ /me
@pytest.mark.parametrize("role", ROLES)
async def test_me_permissions_equal_the_matrix_for_the_role(client, world, role):
    actor = actor_by_role(world, role)
    me = (await client.get("/me", headers=h(actor))).json()
    table = seed_table()
    assert me["role"] == role and me["email"] == actor.email and me["org_id"] == str(world.org_id)
    got = {p["key"]: p for p in me["permissions"]}
    assert set(got) == set(KEYS)
    assert len(me["permissions"]) == len(KEYS)  # no duplicates
    for key, kp in KEYS.items():
        rule = table[(Role(role), key)]
        p = got[key]
        assert (p["scope"], p["mode"]) == (rule.scope.value, rule.mode.value), key
        assert (p["own_deals_only"], p["not_own_deals"]) == (
            rule.own_deals_only,
            rule.not_own_deals,
        )
        assert p["class"] == kp.action_class.value
        assert p["reason"] == reason_rule(Role(role), kp.action_class, key).value, key


async def test_me_team_id_is_none_for_privileged_and_set_for_team_roles(client, world):
    for actor in world.all_actors:
        me = (await client.get("/me", headers=h(actor))).json()
        if actor.role in ("admin", "super_admin"):
            assert me["team_id"] is None
        else:
            assert me["team_id"] == str(actor.team_id)


def test_reason_column_of_me_matches_prd_table_by_hand():
    """PRD 4.2 restated by hand (not from policy.py)."""
    expect = {
        ("user", "important"): "required",
        ("team_leader", "important"): "required",
        ("admin", "important"): "required",
        ("super_admin", "important"): "optional",
        ("user", "routine"): "none",
        ("team_leader", "routine"): "none",
        ("admin", "routine"): "required",
        ("super_admin", "routine"): "optional",
        ("user", "view"): "none",
        ("team_leader", "view"): "none",
        ("admin", "view"): "none",
        ("super_admin", "view"): "none",
    }
    for (role, cls), want in expect.items():
        assert reason_rule(Role(role), ActionClass(cls)).value == want


async def test_nul_byte_in_login_email_is_a_clean_4xx(client, world):
    r = await client.post("/auth/dev/login", json={"email": "\x00@x.com"})
    assert r.status_code in (401, 422), r.status_code


async def test_me_permission_field_matches_the_published_schema(client, world):
    schema = create_app().openapi()["components"]["schemas"]["PermissionOut"]
    assert "class" in schema["properties"] and "class" in schema["required"]
    me = (await client.get("/me", headers=h(world.sa))).json()
    assert all("class" in p for p in me["permissions"])
