# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 2: the permission matrix, restated by hand from the PRD / Phase 1
prompt (not parsed from matrix.py), checked through guard.evaluate, the database copy, /me and HTTP."""

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.platform.permissions import loader
from app.platform.permissions.guard import Effect, evaluate, require
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_table
from app.platform.permissions.roles import ActionClass, Role, Scope
from app.platform.permissions.sync import sync
from tests.deep.phase1.kit import ROLES, actor_by_role, audit_rows, h, new_email

# Cell tokens: "-" denied | own/team/all | own_d (own, own_deals_only) | team_nod (team, not own deals)
# | apprTL / apprA (needs approval, routed to the TL / the Admin pool) | reqA (request only, Admin pool)
U, TL, AD, SA = Role.USER, Role.TEAM_LEADER, Role.ADMIN, Role.SUPER_ADMIN
R_I, R_R, R_V = "important", "routine", "view"

HAND = {  # key: (class, user, team leader, admin, super admin)  -- PRD v3.6 4.4 / Phase 1 prompt 6
    "deal.view": (R_V, "own", "team", "all", "all"),
    "board.view": (R_V, "own", "team", "all", "all"),
    "pool.view": (R_V, "-", "team", "all", "all"),
    "today.view": (R_V, "own", "team", "all", "all"),
    "customer.view": (R_V, "own", "team", "all", "all"),
    "audit.view": (R_V, "own", "team", "all", "all"),
    "deleted.view": (R_V, "-", "team", "all", "all"),
    "sa_owned.view": (R_V, "-", "-", "all", "all"),
    "lead.create": (R_R, "own", "team", "all", "all"),
    "pool.assign": (R_I, "-", "team", "all", "all"),
    "lead.reassign": (R_I, "-", "team", "all", "all"),
    "deal.edit": (R_R, "own", "team", "all", "all"),
    "deal.value.up_to_5": (R_R, "own", "team", "all", "all"),
    "deal.value.5_to_10": (R_I, "own", "team", "all", "all"),
    "deal.value.above_10": (R_I, "apprTL", "apprA", "all", "all"),
    "deal.source.change_after_lead": (R_I, "-", "team", "all", "all"),
    "deal.close_date.change": (R_I, "own", "team", "all", "all"),
    "deal.pricing.change_after_prospecting": (R_I, "own", "team", "all", "all"),
    "deal.delete": (R_I, "-", "team", "all", "all"),
    "deal.restore": (R_I, "-", "team", "all", "all"),
    "record.created_fields.edit": (R_I, "-", "-", "-", "-"),
    "record.erase": (R_I, "-", "-", "-", "-"),
    "activity.log": (R_R, "own", "team", "all", "all"),
    "activity.edit_own": (R_I, "own", "own", "all", "all"),
    "activity.edit_others": (R_I, "-", "-", "-", "-"),
    "activity.delete": (R_I, "-", "team", "all", "all"),
    "criterion.tick": (R_R, "own", "own_d", "own_d", "own_d"),
    "criterion.untick": (R_I, "own", "own_d", "own_d", "own_d"),
    "deal.move_forward": (R_R, "own", "own_d", "own_d", "own_d"),
    "criterion.verify": (R_R, "-", "team", "all", "all"),
    "criterion.reject": (R_I, "-", "team", "all", "all"),
    "deal.move_backward": (R_I, "own", "team", "all", "all"),
    "deal.lose": (R_I, "own", "team", "all", "all"),
    "deal.reopen": (R_I, "-", "team", "all", "all"),
    "deal.override_or_skip": (R_I, "-", "team_nod", "all", "all"),
    "deal.bulk_lose": (R_I, "-", "team", "all", "all"),
    "deal.bulk_stage_move": (R_I, "-", "-", "-", "-"),
    "next_step.set": (R_R, "own", "team", "all", "all"),
    "deal.snooze_nudge_replied": (R_R, "own", "team", "all", "all"),
    "deal.bulk_snooze": (R_I, "-", "team", "all", "all"),
    "deal.owner.change": (R_I, "-", "team", "all", "all"),
    "deal.team_transfer.request": (R_I, "-", "reqA", "-", "-"),
    "deal.team_transfer.decide_or_move": (R_I, "-", "-", "all", "all"),
    "deal.bulk_reassign": (R_I, "-", "team", "all", "all"),
    "proposal.send_list_price": (R_R, "own", "team", "all", "all"),
    "discount.up_to_10": (R_I, "apprTL", "team", "all", "all"),
    "discount.above_10_or_below_floor": (R_I, "apprA", "apprA", "all", "all"),
    "catalog.template.manage": (R_I, "-", "-", "all", "all"),
    "agreement.add": (R_R, "own", "team", "all", "all"),
    "agreement.edit": (R_I, "-", "team", "all", "all"),
    "contract_dates.set_and_subscription.confirm": (R_I, "-", "team", "all", "all"),
    "payment.record": (R_I, "-", "team", "all", "all"),
    "invoice.add": (R_R, "own", "team", "all", "all"),
    "invoice.edit": (R_I, "-", "apprA", "all", "all"),
    "agreement.terminate": (R_I, "-", "-", "all", "all"),
    "renewal_cancellation_churn.record": (R_I, "own", "team", "all", "all"),
    "churn.reverse": (R_I, "-", "-", "all", "all"),
    "churn_reasons.manage": (R_I, "-", "-", "all", "all"),
    "profile.view_own": (R_V, "own", "own", "all", "all"),
    "profile.view_others": (R_V, "-", "team", "all", "all"),
    "leaderboard.view": (R_V, "all", "all", "all", "all"),
    "target.set": (R_I, "-", "team", "all", "all"),
    "incentive_rate.set": (R_I, "-", "-", "all", "all"),
    "incentive.view": (R_V, "own", "team", "all", "all"),
    "incentive.request": (R_I, "-", "reqA", "-", "-"),
    "teams.compare": (R_V, "-", "-", "all", "all"),
    "reports.view": (R_V, "own", "team", "all", "all"),
    "forecast.view": (R_V, "own", "team", "all", "all"),
    "performance.recompute": (R_I, "-", "-", "all", "all"),
    "capture.settings.manage": (R_I, "-", "-", "all", "all"),
    "capture.csv.upload": (R_I, "-", "team", "all", "all"),
    "capture.review.view": (R_V, "-", "team", "all", "all"),
    "capture.review.decide": (R_I, "-", "team", "all", "all"),
    "capture.events.view": (R_V, "-", "-", "all", "all"),
    "google.connect": (R_V, "own", "own", "own", "own"),
    "google.admin.view": (R_V, "-", "-", "all", "all"),
    "google.admin.retry": (R_I, "-", "-", "all", "all"),
    "gmail.log": (R_I, "own", "own", "own", "own"),
    "export.create": (R_I, "own", "team", "all", "all"),
    "export_log.view": (R_V, "-", "team", "all", "all"),
    "approval.request": (R_I, "own", "own", "-", "-"),
    "approval.decide": (R_I, "-", "team", "all", "all"),
    "pipeline.config": (R_I, "-", "-", "all", "all"),
    "assignment.config": (R_I, "-", "-", "all", "all"),
    "custom_fields_and_reason_lists.manage": (R_I, "-", "-", "all", "all"),
    "duplicate.override_or_merge": (R_I, "-", "-", "all", "all"),
    "duplicate.create_anyway": (R_I, "own", "team", "all", "all"),
    "user.manage": (R_I, "-", "reqA", "all", "all"),
    "team.manage": (R_I, "-", "-", "all", "all"),
    "team.colour.change": (R_I, "-", "-", "all", "all"),
    "admin.manage": (R_I, "-", "-", "-", "all"),
    "password.reset_own": (R_R, "own", "own", "own", "own"),
    "password.reset_others": (R_I, "-", "-", "all", "all"),
    "jobs.view_failed": (R_V, "-", "-", "all", "all"),
    "jobs.retry": (R_I, "-", "-", "all", "all"),
}


def decode(cell: str, role: Role) -> dict:
    """Expected Rule fields for a hand-written cell."""
    d = {"scope": Scope.NONE, "mode": "allow", "own_d": False, "not_own": False, "routed": None}
    if cell == "-":
        return d
    if cell in ("own", "team", "all"):
        return {**d, "scope": Scope(cell)}
    if cell == "own_d":
        return {**d, "scope": Scope.OWN, "own_d": True}
    if cell == "team_nod":
        return {**d, "scope": Scope.TEAM, "not_own": True}
    scope = Scope.OWN if role == U else Scope.TEAM
    if cell == "apprTL":
        return {**d, "scope": scope, "mode": "needs_approval", "routed": "team_leader"}
    if cell == "apprA":
        return {**d, "scope": scope, "mode": "needs_approval", "routed": "admin_pool"}
    if cell == "reqA":
        return {**d, "scope": scope, "mode": "request_only", "routed": "admin_pool"}
    raise AssertionError(cell)


def test_hand_table_covers_exactly_the_registered_keys():
    assert set(HAND) == set(KEYS)
    assert len(HAND) >= 30


@pytest.mark.parametrize("key", sorted(HAND))
async def test_every_role_key_pair_through_guard_and_database(key, database):
    """Hand table == python seed == database rows, for every role, through guard.evaluate."""
    cls = HAND[key][0]
    assert KEYS[key].action_class == ActionClass(cls)
    async with get_sessionmaker()() as s:
        table = await loader.get_table(s)
    py = seed_table()
    for i, role in enumerate((U, TL, AD, SA)):
        want = decode(HAND[key][1 + i], role)
        for src_name, src in (("db", table), ("py", py)):
            rule = src[(role, key)]
            assert rule.scope == want["scope"], (key, role, src_name)
            assert rule.mode.value == want["mode"], (key, role, src_name)
            assert rule.own_deals_only == want["own_d"], (key, role, src_name)
            assert rule.not_own_deals == want["not_own"], (key, role, src_name)
            assert rule.routed_to == want["routed"], (key, role, src_name)
        # the decision
        for own in (None, True, False):
            d = evaluate(table, role, key, is_own_record=own)
            if want["scope"] == Scope.NONE:
                assert d.effect == Effect.DENY
            elif want["own_d"] and own is not True:
                assert d.effect == Effect.DENY  # fails closed when unknown or not own
            elif want["not_own"] and own is not False:
                assert d.effect == Effect.DENY
            else:
                assert (
                    d.effect
                    == {
                        "allow": Effect.ALLOW,
                        "needs_approval": Effect.NEEDS_APPROVAL,
                        "request_only": Effect.REQUEST_ONLY,
                    }[want["mode"]]
                )
                assert d.scope == want["scope"]


def test_unknown_keys_and_unknown_roles_raise():
    table = seed_table()
    for bad in ("", "nope", "deal.view ", "DEAL.VIEW", "deal.view\n", "deal"):
        with pytest.raises(ValueError):
            evaluate(table, U, bad)
        with pytest.raises(ValueError):
            require(table, U, bad)
    with pytest.raises((ValueError, KeyError)):
        evaluate(table, "ghost", "deal.view")  # type: ignore[arg-type]


async def test_database_copy_is_complete_and_has_no_extras(database, admin_engine):
    async with admin_engine.connect() as c:
        n_keys = (await c.execute(text("SELECT count(*) FROM permission_keys"))).scalar_one()
        n_rows = (await c.execute(text("SELECT count(*) FROM role_permissions"))).scalar_one()
        classes = {
            r[0]: r[1]
            for r in (await c.execute(text("SELECT key, action_class FROM permission_keys"))).all()
        }
        per_key = (
            await c.execute(
                text("SELECT key, count(DISTINCT role) FROM role_permissions GROUP BY key")
            )
        ).all()
        orphan = (
            await c.execute(
                text(
                    "SELECT count(*) FROM role_permissions rp "
                    "LEFT JOIN permission_keys k ON k.key = rp.key WHERE k.key IS NULL"
                )
            )
        ).scalar_one()
    assert n_keys == len(KEYS) and n_rows == 4 * len(KEYS) and orphan == 0
    assert all(cnt == 4 for _, cnt in per_key) and len(per_key) == len(KEYS)
    assert classes == {k: v[0] for k, v in HAND.items()}
    # CHECK constraints reject nonsense roles and scopes
    async with admin_engine.connect() as c:
        for stmt in (
            "INSERT INTO role_permissions (role,key,scope,mode) VALUES ('root','deal.view','all','allow')",
            "INSERT INTO role_permissions (role,key,scope,mode) VALUES ('user','x.y','all','allow')",
        ):
            with pytest.raises(Exception):  # noqa: B017, PT011
                await c.execute(text(stmt))
            await c.rollback()


async def test_sync_is_idempotent_and_restores_deleted_and_altered_rows(database, admin_engine):
    async def snapshot():
        async with admin_engine.connect() as c:
            a = (await c.execute(text("SELECT * FROM role_permissions ORDER BY role, key"))).all()
            b = (await c.execute(text("SELECT * FROM permission_keys ORDER BY key"))).all()
        return a, b

    before = await snapshot()
    assert await sync() == (len(KEYS), 4 * len(KEYS))
    assert await sync() == (len(KEYS), 4 * len(KEYS))
    assert await snapshot() == before
    try:
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE role_permissions SET scope='all' WHERE role='user' AND key='deal.delete'"
                )
            )
            await c.execute(
                text("DELETE FROM role_permissions WHERE role='team_leader' AND key='pool.view'")
            )
        await sync()
        assert await snapshot() == before
    finally:
        await sync()
        loader.invalidate()


async def test_altered_row_changes_behaviour_only_after_cache_invalidation(
    client, world, admin_engine
):
    """DOCUMENTED BEHAVIOUR: the matrix is cached in-process for 30 s (loader._TTL_SECONDS); a
    direct database edit is not seen until the cache expires or `loader.invalidate()` runs."""

    async def can_retry(actor) -> int:
        return (await client.get("/admin/failed-jobs", headers=h(actor))).status_code

    assert await can_retry(world.north1) == 403  # warms the cache
    try:
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE role_permissions SET scope='all' WHERE role='user' AND key='jobs.view_failed'"
                )
            )
        assert await can_retry(world.north1) == 403  # still the cached copy
        loader.invalidate()
        assert await can_retry(world.north1) == 200  # new behaviour after invalidation
    finally:
        await sync()  # also invalidates
    assert await can_retry(world.north1) == 403


async def test_a_deleted_matrix_row_fails_closed_for_writes_and_does_not_open_anything(
    client, world, admin_engine
):
    """If a row disappears (bad manual edit) nothing becomes allowed: the request errors out."""
    try:
        async with admin_engine.begin() as c:
            await c.execute(
                text("DELETE FROM role_permissions WHERE role='admin' AND key='jobs.view_failed'")
            )
        loader.invalidate()
        r = await client.get("/admin/failed-jobs", headers=h(world.admin))
        assert r.status_code >= 400  # never 200
        assert (await client.get("/admin/failed-jobs", headers=h(world.sa))).status_code == 200
    finally:
        await sync()


@pytest.mark.parametrize("role", ROLES)
async def test_me_equals_hand_table(client, world, role):
    me = (await client.get("/me", headers=h(actor_by_role(world, role)))).json()
    idx = {"user": 1, "team_leader": 2, "admin": 3, "super_admin": 4}[role]
    for p in me["permissions"]:
        want = decode(HAND[p["key"]][idx], Role(role))
        assert p["scope"] == want["scope"].value, p["key"]
        assert p["mode"] == want["mode"], p["key"]
        assert p["own_deals_only"] == want["own_d"] and p["not_own_deals"] == want["not_own"]


# ============================================================ HTTP matrix: role x action x target
TARGETS = (
    "self",
    "same_team_user",
    "other_team_user",
    "own_tl",
    "other_tl",
    "admin",
    "sa",
    "other_org",
)


def _target(world, world_b, kind, actor):
    return {
        "self": actor,
        "same_team_user": world.north2,
        "other_team_user": world.south1,
        "own_tl": world.tl_north,
        "other_tl": world.tl_south,
        "admin": world.admin,
        "sa": world.sa,
        "other_org": world_b.north1,
    }[kind]


def expected_status(role: str, action: str, kind: str, actor_is_target: bool) -> int | None:
    """Hand-derived from the PRD (4.3, 4.4 L and 4.5). None = not asserted here (known gap/bug)."""
    if kind == "other_org":
        return 404
    if action == "reset":
        if actor_is_target:
            return 204  # everyone may reset their own password (Phase 1 prompt section 10)
        return 204 if role in ("admin", "super_admin") else 403
    priv_target = kind in ("admin", "sa") or (actor_is_target and role in ("admin", "super_admin"))
    if role == "user":
        return 403
    if role == "team_leader":
        if action == "rename":
            return 403  # a request-only role never renames directly (BUG-P1-03 fixed)
        if action == "deactivate":
            return 202 if kind == "same_team_user" else 403  # a request (BUG-P1-18 fixed)
        if action == "move":
            return 403  # team.manage is Admin-only
        if action == "role":
            return 403  # a TL may no longer ask for team_leader; only an Admin appoints one
    if role == "admin":
        if action == "move":
            return 409 if kind in ("own_tl", "other_tl", "admin", "sa", "self") else 200
        if priv_target:
            return 403
        if action == "role" and kind in ("same_team_user", "other_team_user"):
            return 409  # both teams already have a Team Leader
        return 200
    if role == "super_admin":
        if action == "role" and kind in ("same_team_user", "other_team_user"):
            return 409
        if action == "move":
            return 409 if kind in ("own_tl", "other_tl", "admin", "sa", "self") else 200
        return 200
    return None


def body_for(action: str, world, kind: str) -> dict:
    return {
        "rename": {"name": "Renamed " + kind},
        "deactivate": {"is_active": False},
        "role": {
            "role": "team_leader" if kind in ("same_team_user", "other_team_user") else "user",
            # demoting an Admin / Super admin needs a team in the same change (BUG-P1-05)
            **({"team_id": str(world.north_team)} if kind in ("admin", "sa") else {}),
        },
        "move": {
            "team_id": str(
                world.north_team if kind in ("other_team_user", "other_tl") else world.south_team
            )
        },
    }.get(action, {})


@pytest.mark.parametrize("action", ["rename", "deactivate", "role", "move", "reset"])
@pytest.mark.parametrize("role", ROLES)
async def test_http_matrix_for_user_endpoints(client, world, world_b, role, action):
    actor = actor_by_role(world, role)
    failures = []
    for kind in TARGETS:
        target = _target(world, world_b, kind, actor)
        is_self = target.id == actor.id
        # skip combinations that would lock the actor out of the rest of the loop
        if is_self and action in ("deactivate", "role", "move"):
            continue
        if kind == "sa" and role == "super_admin" and action in ("deactivate", "role"):
            continue  # that is the last Super admin; covered in section 11
        body = body_for(action, world, kind)
        want = expected_status(role, action, kind, is_self)
        if want is None:
            continue
        before_org = await audit_rows(target.org_id)
        before_mine = await audit_rows(world.org_id)
        if action == "reset":
            r = await client.post(
                f"/users/{target.id}/password-reset", json={"reason": "qa reset"}, headers=h(actor)
            )
        else:
            r = await client.patch(
                f"/users/{target.id}", json={**body, "reason": "qa matrix"}, headers=h(actor)
            )
        if r.status_code != want:
            failures.append((kind, want, r.status_code, r.text[:120]))
            continue
        after_mine = await audit_rows(world.org_id)
        after_org = await audit_rows(target.org_id)
        if want == 404:  # other organisation: no trace in either org, nothing changed
            assert len(after_org) == len(before_org) and len(after_mine) == len(before_mine)
        if want == 403:
            denied = [x for x in after_mine[len(before_mine) :] if x["outcome"] == "denied"]
            assert len(after_mine) == len(before_mine) + 1 and len(denied) == 1, (kind, action)
            assert str(denied[0]["actor_id"]) == str(actor.id)
        if want in (200, 204):
            new = [x for x in after_mine[len(before_mine) :]]
            assert len(new) >= 1 and all(x["outcome"] == "ok" for x in new), (kind, action)
        if want == 202:
            new = after_mine[len(before_mine) :]
            assert [x["outcome"] for x in new] == ["approval_requested"], (kind, action)
    assert not failures, failures


async def test_denied_write_leaves_no_change(client, world):
    before = (await client.get("/users", headers=h(world.admin))).json()
    for actor in (world.north1, world.tl_north):
        for target in (world.south1, world.tl_south, world.admin, world.sa):
            await client.patch(
                f"/users/{target.id}",
                json={
                    "is_active": False,
                    "role": "admin",
                    "team_id": str(world.north_team),
                    "name": "Hacked",
                    "reason": "x",
                },
                headers=h(actor),
            )
    after = (await client.get("/users", headers=h(world.admin))).json()
    assert before == after


@pytest.mark.parametrize("role", ROLES)
async def test_http_matrix_for_create_user_team_and_failed_jobs(client, world, world_b, role):
    actor = actor_by_role(world, role)
    expect_create = {
        ("user", "user"): 403, ("user", "team_leader"): 403, ("user", "admin"): 403, ("user", "super_admin"): 403,
        ("team_leader", "user"): 202, ("team_leader", "team_leader"): 403, ("team_leader", "admin"): 403,
        ("team_leader", "super_admin"): 403,
        ("admin", "user"): 201, ("admin", "team_leader"): 201, ("admin", "admin"): 403, ("admin", "super_admin"): 403,
        ("super_admin", "user"): 201, ("super_admin", "team_leader"): 201, ("super_admin", "admin"): 201,
        ("super_admin", "super_admin"): 201,
    }  # fmt: skip
    # a spare team without a leader so team_leader creation can succeed
    spare = (
        await client.post(
            "/teams", json={"name": f"Spare {role}", "reason": "qa"}, headers=h(world.sa)
        )
    ).json()["id"]
    for new_role in ROLES:
        team = str(world.north_team) if role == "team_leader" else spare
        body = {
            "name": "Newbie",
            "email": new_email(),
            "role": new_role,
            "team_id": team,
            "reason": "qa",
        }
        if new_role in ("admin", "super_admin"):
            body.pop("team_id")
        r = await client.post("/users", json=body, headers=h(actor))
        want = expect_create[(role, new_role)]
        assert r.status_code == want, (role, new_role, r.status_code, r.text)
    # other-org / other-team targets
    r = await client.post(
        "/users",
        json={
            "name": "X",
            "email": new_email(),
            "role": "user",
            "team_id": str(world_b.north_team),
            "reason": "qa",
        },
        headers=h(actor),
    )
    assert r.status_code == (403 if role in ("user", "team_leader") else 404), (role, r.text)
    # teams
    for method, path, body in (
        ("POST", "/teams", {"name": f"T {role} {new_email()}", "reason": "qa"}),
    ):
        r = await client.request(method, path, json=body, headers=h(actor))
        assert r.status_code == (201 if role in ("admin", "super_admin") else 403), (role, r.text)
    for team_id, want_priv in (
        (world.north_team, 200),
        (world.south_team, 200),
        (world_b.north_team, 404),
    ):
        r = await client.patch(
            f"/teams/{team_id}",
            json={"name": f"Rn {new_email()}", "reason": "qa"},
            headers=h(actor),
        )
        want = want_priv if role in ("admin", "super_admin") else 403
        if team_id == world_b.north_team and role not in ("admin", "super_admin"):
            want = 403
        assert r.status_code == want, (role, str(team_id), r.status_code)
    # failed jobs
    want = 200 if role in ("admin", "super_admin") else 403
    assert (await client.get("/admin/failed-jobs", headers=h(actor))).status_code == want
    r = await client.post("/admin/failed-jobs/1/retry", json={"reason": "qa"}, headers=h(actor))
    # admins: the job does not exist (404, BUG-P1-08 fixed); others: 403
    assert r.status_code == (404 if role in ("admin", "super_admin") else 403)
    if role in ("user", "team_leader"):
        assert r.status_code == 403
    # reads of the user directory: team scoped for non-privileged roles
    users = (await client.get("/users", headers=h(actor))).json()
    emails = {u["email"] for u in users}
    assert (
        world.sa.email in emails
        if role in ("admin", "super_admin")
        else world.sa.email not in emails
    )
    assert not ({a.email for a in world_b.all_actors} & emails)
    teams = (await client.get("/teams", headers=h(actor))).json()
    assert len(teams) >= (3 if role in ("admin", "super_admin") else 1)
    if role == "user":
        assert [t["id"] for t in teams] == [str(actor.team_id)]
    if role == "team_leader":  # sees every team's name (BUG-P2-40) but only the own team's details
        assert str(actor.team_id) in [t["id"] for t in teams]
