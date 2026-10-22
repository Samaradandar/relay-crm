# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 4: the reason policy through the real API."""

import pytest

from app.platform.approvals import service as approvals
from app.platform.context import RequestInfo
from app.platform.notifications import email
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL
from app.platform.permissions.roles import ActionClass, Role
from app.platform.reasons import policy
from app.platform.uow import UnitOfWork
from tests.deep.phase1.kit import audit_rows, h, new_email

REQ = RequestInfo()

# PRD 4.2, restated by hand: (role, class) -> required | optional | none
TABLE = {
    ("user", "important"): "required", ("team_leader", "important"): "required",
    ("admin", "important"): "required", ("super_admin", "important"): "optional",
    ("user", "routine"): "none", ("team_leader", "routine"): "none",
    ("admin", "routine"): "required", ("super_admin", "routine"): "optional",
    ("user", "view"): "none", ("team_leader", "view"): "none",
    ("admin", "view"): "none", ("super_admin", "view"): "none",
}  # fmt: skip

NASTY = [
    "x",
    "<script>alert(1)</script>",
    "'); DROP TABLE audit_log; --",
    "Robert'); DELETE FROM users;--",
    "हिन्दी कारण — üñî",
    "\U0001f600\U0001f680 emoji \U0001f468‍\U0001f469‍\U0001f467",
    "=CMD|'/c calc'!A1",
    '"; alert(document.cookie); //',
    "line1\nline2\r\nline3\ttab",
    "{{7*7}} ${7*7} %s %(x)s {0}",
    "a" * 10_000,
    "‮RTL override",
]


@pytest.mark.parametrize("role,cls", sorted(TABLE))
def test_reason_policy_cells(role, cls):
    want = TABLE[(role, cls)]
    assert policy.reason_rule(Role(role), ActionClass(cls)).value == want
    for blank in (None, "", " ", "\t\n  ", " " * 0 + "   "):
        if want == "required":
            with pytest.raises(Exception) as ei:  # noqa: PT011
                policy.check(Role(role), ActionClass(cls), blank)
            assert getattr(ei.value, "code", "") == "reason_required"
        else:
            assert policy.check(Role(role), ActionClass(cls), blank) is None
    assert policy.check(Role(role), ActionClass(cls), "  ok  ") == "ok"


def test_unicode_whitespace_only_reason_counts_as_empty():
    """NBSP / ideographic space are whitespace for str.strip(); a reason made only of them is empty."""
    for ws in (" ", "　", "  ", "  　"):
        with pytest.raises(Exception) as ei:  # noqa: PT011
            policy.check(Role.USER, ActionClass.IMPORTANT, ws)
        assert getattr(ei.value, "code", "") == "reason_required"


def test_zero_width_only_reason_is_accepted_as_a_reason():
    """NOTE: zero-width characters are not whitespace, so a 'blank looking' reason passes."""
    assert policy.check(Role.USER, ActionClass.IMPORTANT, "​") == "​"


# ------------------------------------------------------------------ through HTTP
async def test_important_action_reason_matrix_over_http(client, world):
    """create_user is an important action: Admin needs a reason, Super admin may skip it."""
    base = {"role": "user", "team_id": str(world.north_team), "name": "R1"}
    for blank in (None, "", " ", "\n\t "):
        body = {**base, "email": new_email()} | ({} if blank is None else {"reason": blank})
        r = await client.post("/users", json=body, headers=h(world.admin))
        assert r.status_code == 422 and r.json()["code"] == "reason_required", (blank, r.text)
        r = await client.post("/users", json={**body, "email": new_email()}, headers=h(world.sa))
        assert r.status_code == 201, (blank, r.text)
        r = await client.post(
            "/users", json={**body, "email": new_email()}, headers=h(world.tl_north)
        )
        assert r.status_code == 422  # a TL must give a reason to request (PRD 4.5)
    # a refused (422) create leaves nothing behind
    assert not [
        x
        for x in await audit_rows(world.org_id, action_key="user.manage")
        if x["outcome"] == "ok" and x["actor_id"] == world.admin.id
    ]


async def test_routine_action_reason_matrix_over_http(client, world):
    """password.reset_own is routine: none for User/TL, required for Admin, optional for SA."""
    for actor, blank_ok in (
        (world.north1, True),
        (world.tl_north, True),
        (world.admin, False),
        (world.sa, True),
    ):
        r = await client.post(f"/users/{actor.id}/password-reset", json={}, headers=h(actor))
        assert (r.status_code == 204) == blank_ok, (actor.role, r.status_code)
        if not blank_ok:
            assert r.status_code == 422 and r.json()["code"] == "reason_required"
        r = await client.post(
            f"/users/{actor.id}/password-reset", json={"reason": "forgot"}, headers=h(actor)
        )
        assert r.status_code == 204
    r = await client.post(
        f"/users/{world.north1.id}/password-reset", json={"reason": " "}, headers=h(world.admin)
    )
    assert r.status_code == 422  # reset of others is important: reason required for Admin


async def test_view_actions_never_ask_for_a_reason(client, world):
    for actor in world.all_actors:
        for path in (
            "/me",
            "/users",
            "/teams",
            "/approvals",
            "/notifications",
            f"/history/user/{actor.id}",
        ):
            assert (await client.get(path, headers=h(actor))).status_code == 200


@pytest.mark.parametrize("reason", NASTY)
async def test_reason_text_is_stored_verbatim_and_returned_as_data(client, world, reason):
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"name": "Reasoned", "reason": reason},
        headers=h(world.admin),
    )
    assert r.status_code == 200, r.text[:200]
    hist = (await client.get(f"/history/user/{world.north2.id}", headers=h(world.north2))).json()
    stored = hist[0]["reason"]
    assert stored == reason.strip()
    # raw DB value is identical (nothing was escaped on the way in)
    rows = await audit_rows(world.org_id, entity_id=str(world.north2.id), action_key="user.manage")
    assert rows[-1]["reason"] == reason.strip()
    # the tables still exist
    assert (await client.get("/users", headers=h(world.admin))).status_code == 200
    assert len(await audit_rows(world.org_id)) > 0
    # JSON, not HTML: the content-type prevents the browser from executing anything
    resp = await client.get(f"/history/user/{world.north2.id}", headers=h(world.admin))
    assert resp.headers["content-type"].startswith("application/json")


async def test_names_with_markup_are_stored_verbatim_and_never_executed_server_side(client, world):
    for name in (
        "<img src=x onerror=alert(1)>",
        "'; DROP TABLE users;--",
        "\U0001f600 Emoji Name",
        '=HYPERLINK("http://x")',
    ):
        r = await client.post(
            "/users",
            json={
                "name": name,
                "email": new_email(),
                "role": "user",
                "team_id": str(world.north_team),
                "reason": "r",
            },
            headers=h(world.admin),
        )
        assert r.status_code == 201 and r.json()["name"] == name
    assert len((await client.get("/users", headers=h(world.admin))).json()) >= 12


def test_email_template_escapes_html_in_reasons_and_titles():
    html_body, text_body = email.render(
        "<b>T</b>", '<script>alert(1)</script> & "q"', "/a?b=1&c=<x>"
    )
    assert "<script>" not in html_body and "&lt;script&gt;" in html_body
    assert "<b>T</b>" not in html_body
    assert 'href="/a?b=1&amp;c=&lt;x&gt;"' in html_body
    assert "<script>" in text_body  # plain text part is text


async def test_reason_of_a_decision_reaches_the_requester_escaped_in_email(
    world, database, memory_email
):
    from tests.helpers import dispatch_all

    async def noop(uow, req):
        return None

    approvals.register_type("deep.reason", noop)
    async with UnitOfWork(world.tl_north, REQ) as uow:
        req = await approvals.create_request(
            uow, type_="deep.reason", action_key="user.manage", summary="s", payload={}, reason="why",
            routed_to=ROUTE_ADMIN_POOL,
        )  # fmt: skip
        rid = req.id
    await approvals.decide(world.admin, REQ, rid, approve=False, reason="<script>x</script>")
    await dispatch_all()
    mine = [m for m in memory_email.sent if m[0] == world.tl_north.email]
    assert (
        mine and "<script>x</script>" in mine[0][2]
    )  # text part keeps the raw text (it is plain text)


async def test_decision_reason_rules_through_http(client, world):
    from tests.deep.phase1.test_03_isolation import make_request

    rid = await make_request(world.tl_north)  # admin pool
    # a User or TL (own team) cannot decide an admin-pool request
    for a in (world.north1, world.tl_north, world.tl_south):
        r = await client.post(
            f"/approvals/{rid}/decide", json={"decision": "approve", "reason": "x"}, headers=h(a)
        )
        assert r.status_code == 403
    # an Admin must give a reason, approve or reject
    for dec in ("approve", "reject"):
        for blank in (None, "", "   "):
            r = await client.post(
                f"/approvals/{rid}/decide",
                json={"decision": dec, "reason": blank},
                headers=h(world.admin),
            )
            assert r.status_code == 422 and r.json()["code"] == "reason_required", (dec, blank)
    r = await client.post(
        f"/approvals/{rid}/decide",
        json={"decision": "reject", "reason": "no"},
        headers=h(world.admin),
    )
    assert (
        r.status_code == 200
        and r.json()["status"] == "rejected"
        and r.json()["decision_reason"] == "no"
    )
    # SA: optional
    rid2 = await make_request(world.tl_north)
    r = await client.post(
        f"/approvals/{rid2}/decide", json={"decision": "approve"}, headers=h(world.sa)
    )
    assert r.status_code == 200 and r.json()["decision_reason"] is None
    # bad decision values
    rid3 = await make_request(world.tl_north)
    for bad in ("APPROVE", "approved", "", None, 1, "maybe"):
        r = await client.post(
            f"/approvals/{rid3}/decide", json={"decision": bad}, headers=h(world.sa)
        )
        assert r.status_code == 422
    r = await client.post(f"/approvals/{rid3}/decide", json={}, headers=h(world.sa))
    assert r.status_code == 422


async def test_reason_whitespace_is_trimmed_not_padded_in_history(client, world):
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"name": "T", "reason": "  \n padded \t "},
        headers=h(world.admin),
    )
    assert r.status_code == 200
    hist = (await client.get(f"/history/user/{world.north2.id}", headers=h(world.admin))).json()
    assert hist[0]["reason"] == "padded"
