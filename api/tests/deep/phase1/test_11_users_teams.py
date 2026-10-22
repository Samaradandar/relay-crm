# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 11: users and teams (rules from PRD 4.3 and the Phase 1 prompt)."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.core.security import get_auth_provider
from app.modules.users import service as users_service
from app.platform.permissions.roles import Role
from tests.conftest import insert_user
from tests.deep.phase1.kit import audit_rows, h, new_email
from tests.helpers import dispatch_all, get_user_row, notifications_for

NUL = chr(0)


async def create(client, actor, **kw):
    body = {"name": "Person", "email": new_email(), "role": "user", "reason": "qa"} | kw
    return await client.post("/users", json=body, headers=h(actor))


async def team_row(team_id):
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                text("SELECT id, name, leader_id FROM teams WHERE id=:i"), {"i": team_id}
            )
        ).one()


# ------------------------------------------------------------------ create
async def test_created_user_state_membership_and_who_created_them(client, world):
    r = await create(client, world.admin, team_id=str(world.north_team), name="  Spaced Name  ")
    assert r.status_code == 201
    u = r.json()
    assert u["name"] == "Spaced Name" and u["is_active"] is True and u["team_name"] == "Team North"
    async with get_sessionmaker()() as s:
        mem = (
            await s.execute(
                text("SELECT team_id, ended_at, moved_by FROM team_memberships WHERE user_id=:u"),
                {"u": u["id"]},
            )
        ).all()
        created_by = (
            await s.execute(text("SELECT created_by FROM users WHERE id=:u"), {"u": u["id"]})
        ).scalar_one()
    assert mem == [(world.north_team, None, world.admin.id)] and created_by == world.admin.id
    # a created user can sign in straight away
    assert (await client.post("/auth/dev/login", json={"email": u["email"]})).status_code == 200


async def test_admins_and_super_admins_are_never_on_a_team_even_if_a_team_is_sent(client, world):
    for role in ("admin", "super_admin"):
        r = await create(client, world.sa, role=role, team_id=str(world.north_team))
        assert r.status_code == 201 and r.json()["team_id"] is None
    async with get_sessionmaker()() as s:
        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM team_memberships tm JOIN users u ON u.id=tm.user_id WHERE u.org_id=:o AND u.role IN ('admin','super_admin')"
                ),
                {"o": world.org_id},
            )
        ).scalar_one()
    assert n == 0


async def test_a_user_without_a_team_can_be_created_but_cannot_sign_in_until_placed(client, world):
    r = await create(client, world.admin)  # role user, no team
    assert r.status_code == 201 and r.json()["team_id"] is None
    email = r.json()["email"]
    assert (await client.post("/auth/dev/login", json={"email": email})).status_code == 403
    r2 = await client.patch(
        f"/users/{r.json()['id']}",
        json={"team_id": str(world.north_team), "reason": "place"},
        headers=h(world.admin),
    )
    assert r2.status_code == 200
    assert (await client.post("/auth/dev/login", json={"email": email})).status_code == 200


async def test_team_leader_creation_rules(client, world):
    assert (
        await create(client, world.admin, role="team_leader")
    ).status_code == 409  # needs a team
    assert (
        await create(client, world.admin, role="team_leader", team_id=str(world.south_team))
    ).status_code == 409  # has a leader
    spare = (
        await client.post(
            "/teams", json={"name": "No leader yet", "reason": "r"}, headers=h(world.admin)
        )
    ).json()["id"]
    r = await create(client, world.admin, role="team_leader", team_id=spare)
    assert r.status_code == 201
    assert (await team_row(uuid.UUID(spare))).leader_id == uuid.UUID(r.json()["id"])
    assert (await create(client, world.admin, role="team_leader", team_id=spare)).status_code == 409


# ------------------------------------------------------------------ names and emails
@pytest.mark.parametrize("name", ["   ", "\t", " ", "　 "])
async def test_whitespace_only_names_are_rejected(client, world, name):
    assert (
        await create(client, world.admin, name=name, team_id=str(world.north_team))
    ).status_code == 422
    r = await client.patch(
        f"/users/{world.north2.id}", json={"name": name, "reason": "r"}, headers=h(world.admin)
    )
    assert r.status_code == 422
    assert (
        await client.post("/teams", json={"name": name, "reason": "r"}, headers=h(world.admin))
    ).status_code == 422


@pytest.mark.parametrize(
    "name,ok",
    [
        ("a", True),
        ("a" * 200, True),
        ("a" * 201, False),
        ("", False),
        ("ह" * 200, True),
        ("\U0001f600" * 200, True),
        ("\U0001f600" * 201, False),
    ],
)
async def test_user_name_length_boundaries(client, world, name, ok):
    r = await create(client, world.admin, name=name, team_id=str(world.north_team))
    assert r.status_code == (201 if ok else 422)


@pytest.mark.parametrize(
    "name,ok", [("t" * 120, True), ("t" * 121, False), ("", False), ("Team \U0001f680", True)]
)
async def test_team_name_length_boundaries(client, world, name, ok):
    r = await client.post(
        "/teams", json={"name": name + new_email("x")[:0], "reason": "r"}, headers=h(world.admin)
    )
    assert r.status_code == (201 if ok else 422)


@pytest.mark.parametrize(
    "email,ok",
    [("a@b.co", True), ("A.B@C.CO", True), ("  padded@x.com  ", True), ("a+tag@x.com", True), ("üñi@x.com", True),
     ("a@b", False), ("a b@c.de", False), ("@x.com", False), ("a@", False), ("", False), ("a@@x.com", False),
     ("a" * 250 + "@x.com", False), ("a" * 240 + "@x.com", True), ("x@y.z\n", True), ("a@b.c d", False)],
)  # fmt: skip
async def test_email_validation_boundaries(client, world, email, ok):
    r = await create(client, world.admin, email=email, team_id=str(world.north_team))
    assert r.status_code == (201 if ok else 422), (email[:30], r.status_code, r.text[:100])
    if ok:
        assert r.json()["email"] == email.strip().lower()


async def test_email_uniqueness_is_case_and_space_insensitive_among_live_users(client, world):
    base = new_email()
    assert (
        await create(client, world.admin, email=base, team_id=str(world.north_team))
    ).status_code == 201
    for variant in (base.upper(), f"  {base}  ", base.title()):
        assert (
            await create(client, world.admin, email=variant, team_id=str(world.north_team))
        ).status_code == 409
    # plus-addressing is a different address
    local, domain = base.split("@")
    assert (
        await create(
            client, world.admin, email=f"{local}+x@{domain}", team_id=str(world.north_team)
        )
    ).status_code == 201
    # the same address in ANOTHER organisation is fine (separate customers)
    # (covered in the isolation section with world_b)


async def test_a_deactivated_users_email_stays_reserved(client, world):
    """Open question for the owner: the PRD does not say whether a deactivated person can be re-added.
    Today the address stays taken, so re-hiring means reactivating the old account."""
    r = await create(client, world.admin, team_id=str(world.north_team))
    uid, email = r.json()["id"], r.json()["email"]
    await client.patch(
        f"/users/{uid}", json={"is_active": False, "reason": "left"}, headers=h(world.admin)
    )
    assert (
        await create(client, world.admin, email=email, team_id=str(world.north_team))
    ).status_code == 409
    assert (
        await client.patch(
            f"/users/{uid}", json={"is_active": True, "reason": "back"}, headers=h(world.admin)
        )
    ).status_code == 200


async def test_same_email_in_two_orgs_is_allowed_and_isolated(client, world, world_b):
    e = new_email()
    assert (
        await create(client, world.admin, email=e, team_id=str(world.north_team))
    ).status_code == 201
    assert (
        await create(client, world_b.admin, email=e, team_id=str(world_b.north_team))
    ).status_code == 201


async def test_concurrent_creation_of_the_same_email_makes_one_user(client, world):
    e = new_email()
    rs = await asyncio.gather(
        *[create(client, world.admin, email=e, team_id=str(world.north_team)) for _ in range(12)]
    )
    assert sorted(r.status_code for r in rs) == [201] + [409] * 11
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(text("SELECT count(*) FROM users WHERE lower(email)=:e"), {"e": e})
        ).scalar_one() == 1


# ------------------------------------------------------------------ role changes
async def test_role_change_paths_keep_team_leader_and_membership_consistent(client, world):
    A = h(world.admin)
    # TL -> user: the team has no leader afterwards
    assert (
        await client.patch(
            f"/users/{world.tl_north.id}", json={"role": "user", "reason": "step down"}, headers=A
        )
    ).status_code == 200
    assert (await team_row(world.north_team)).leader_id is None
    # user -> TL
    assert (
        await client.patch(
            f"/users/{world.north1.id}",
            json={"role": "team_leader", "reason": "promote"},
            headers=A,
        )
    ).status_code == 200
    assert (await team_row(world.north_team)).leader_id == world.north1.id
    # TL -> admin: leaves the team entirely (membership closed, leader cleared)
    assert (
        await client.patch(
            f"/users/{world.north1.id}", json={"role": "admin", "reason": "up"}, headers=h(world.sa)
        )
    ).status_code == 200
    u = await get_user_row(world.north1.id)
    assert (
        u.team_id is None
        and u.role == "admin"
        and (await team_row(world.north_team)).leader_id is None
    )
    async with get_sessionmaker()() as s:
        open_rows = (
            await s.execute(
                text("SELECT count(*) FROM team_memberships WHERE user_id=:u AND ended_at IS NULL"),
                {"u": world.north1.id},
            )
        ).scalar_one()
    assert open_rows == 0
    # same role is a no-op
    n = len(await audit_rows(world.org_id))
    assert (
        await client.patch(
            f"/users/{world.north2.id}", json={"role": "user", "reason": "same"}, headers=A
        )
    ).status_code == 200
    assert len(await audit_rows(world.org_id)) == n


async def test_deactivating_a_team_leader_frees_the_team_and_reactivation_does_not_restore_it(
    client, world
):
    A = h(world.admin)
    await client.patch(
        f"/users/{world.tl_north.id}", json={"is_active": False, "reason": "left"}, headers=A
    )
    assert (await team_row(world.north_team)).leader_id is None
    teams = (await client.get("/teams", headers=A)).json()
    north = next(t for t in teams if t["id"] == str(world.north_team))
    assert (
        north["leader_id"] is None and north["member_count"] == 2
    )  # the inactive leader is not counted
    await client.patch(
        f"/users/{world.tl_north.id}", json={"is_active": True, "reason": "back"}, headers=A
    )
    assert (
        await team_row(world.north_team)
    ).leader_id is None  # an Admin appoints the leader again
    assert (
        await client.patch(
            f"/users/{world.tl_north.id}", json={"role": "team_leader", "reason": "x"}, headers=A
        )
    ).status_code in (200,)


async def test_demoting_a_privileged_user_to_user_does_not_leave_a_locked_out_account(
    client, world
):
    r = await client.patch(
        f"/users/{world.admin.id}", json={"role": "user", "reason": "demote"}, headers=h(world.sa)
    )
    assert r.status_code in (200, 409, 422)
    if r.status_code == 200:
        assert (await client.get("/me", headers=h(world.admin))).status_code == 200
        assert (await get_user_row(world.admin.id)).team_id is not None


async def test_a_multi_field_patch_is_all_or_nothing(client, world):
    A = h(world.admin)
    before = await get_user_row(world.north2.id)
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"name": "Changed", "role": "super_admin", "reason": "x"},
        headers=A,
    )
    assert r.status_code == 403
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"name": "Changed", "is_active": False, "team_id": str(uuid.uuid4()), "reason": "x"},
        headers=A,
    )
    assert r.status_code == 404
    after = await get_user_row(world.north2.id)
    assert (after.name, after.is_active, after.role, after.team_id) == (
        before.name,
        before.is_active,
        before.role,
        before.team_id,
    )
    # and when it succeeds every part applies, with one audit row per changed aspect
    n = len(await audit_rows(world.org_id))
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"name": "Both", "team_id": str(world.south_team), "reason": "x"},
        headers=A,
    )
    assert (
        r.status_code == 200
        and r.json()["name"] == "Both"
        and r.json()["team_id"] == str(world.south_team)
    )
    assert len(await audit_rows(world.org_id)) == n + 2


async def test_bad_patch_bodies(client, world):
    A = h(world.admin)
    for body in (
        {"role": "god"},
        {"is_active": "maybe"},
        {"team_id": "x"},
        {"name": 5},
        {"open_deals": "burn"},
        {"name": ""},
        {"name": "x" * 201},
        [],
    ):
        r = await client.patch(
            f"/users/{world.north2.id}", json=body if body != [] else [], headers=A
        )
        assert r.status_code == 422, (body, r.status_code)
    r = await client.patch(f"/users/{world.north2.id}", json={"reason": "nothing to do"}, headers=A)
    assert r.status_code == 200  # an empty change is accepted and changes nothing
    assert (
        await client.patch("/users/not-a-uuid", json={"name": "x"}, headers=A)
    ).status_code == 422


async def test_patch_with_nothing_to_change_skips_the_permission_check(client, world):
    """NOTE (BUG-P1-19, S4): a plain User sending an unchanged value gets 200 for a team mate
    and 404 for anyone else; no 403 and no denied audit row, so it also reveals who is on the team."""
    n = len(await audit_rows(world.org_id))
    r = await client.patch(
        f"/users/{world.north2.id}", json={"role": "user"}, headers=h(world.north1)
    )
    assert r.status_code == 200
    assert len(await audit_rows(world.org_id)) == n


# ------------------------------------------------------------------ last Super admin
async def test_last_super_admin_rules_with_three_and_with_inactive_ones(
    client, world, admin_engine
):
    sa2 = await insert_user(admin_engine, world.org_id, "Sa Two", Role.SUPER_ADMIN, None)
    sa3 = await insert_user(admin_engine, world.org_id, "Sa Three", Role.SUPER_ADMIN, None)
    S = h(world.sa)
    assert (
        await client.patch(f"/users/{sa2.id}", json={"is_active": False, "reason": "x"}, headers=S)
    ).status_code == 200
    assert (
        await client.patch(f"/users/{sa3.id}", json={"role": "admin", "reason": "x"}, headers=S)
    ).status_code == 200
    # world.sa is now the only active one
    for body in ({"is_active": False}, {"role": "admin"}, {"role": "user"}):
        r = await client.patch(f"/users/{world.sa.id}", json={**body, "reason": "x"}, headers=S)
        assert r.status_code == 409, (body, r.text)
    # an Admin cannot touch it either, and reactivating the other one frees the rule
    assert (
        await client.patch(
            f"/users/{world.sa.id}",
            json={"is_active": False, "reason": "x"},
            headers=h(world.admin),
        )
    ).status_code == 403
    assert (
        await client.patch(f"/users/{sa2.id}", json={"is_active": True, "reason": "x"}, headers=S)
    ).status_code == 200
    assert (
        await client.patch(
            f"/users/{world.sa.id}", json={"role": "admin", "reason": "x"}, headers=S
        )
    ).status_code == 200
    assert (
        await client.patch(
            f"/users/{sa2.id}", json={"is_active": False, "reason": "x"}, headers=h(sa2)
        )
    ).status_code == 409


async def test_ten_concurrent_attempts_can_never_remove_the_last_super_admin(
    client, world, admin_engine
):
    sas = [world.sa] + [
        await insert_user(admin_engine, world.org_id, f"SA{i}", Role.SUPER_ADMIN, None)
        for i in range(2)
    ]
    calls = []
    for i in range(10):
        actor, target = sas[i % 3], sas[(i + 1) % 3]
        body = {"is_active": False} if i % 2 == 0 else {"role": "admin"}
        calls.append(
            client.patch(f"/users/{target.id}", json={**body, "reason": "race"}, headers=h(actor))
        )
    rs = await asyncio.gather(*calls)
    assert all(r.status_code in (200, 401, 409) for r in rs), [
        (r.status_code, r.text) for r in rs if r.status_code not in (200, 401, 409)
    ]
    async with get_sessionmaker()() as s:
        active = (
            await s.execute(
                text(
                    "SELECT count(*) FROM users WHERE org_id=:o AND role='super_admin' AND is_active"
                ),
                {"o": world.org_id},
            )
        ).scalar_one()
    assert active >= 1


async def test_two_super_admins_demoting_themselves_at_once_leave_one(client, world, admin_engine):
    sa2 = await insert_user(admin_engine, world.org_id, "SA Two", Role.SUPER_ADMIN, None)
    rs = await asyncio.gather(
        client.patch(
            f"/users/{world.sa.id}", json={"role": "admin", "reason": "me"}, headers=h(world.sa)
        ),
        client.patch(f"/users/{sa2.id}", json={"role": "admin", "reason": "me"}, headers=h(sa2)),
    )
    assert sorted(r.status_code for r in rs) == [200, 409]


# ------------------------------------------------------------------ moves, hook, notification
async def test_team_move_fires_the_hook_once_notifies_and_keeps_one_open_membership(
    client, world, monkeypatch
):
    calls = []

    async def hook(uow, user, old, new):
        calls.append((user.id, old, new))

    monkeypatch.setattr(users_service, "TEAM_MOVE_HOOKS", [hook])
    A = h(world.admin)
    r = await client.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "reason": "re-org"},
        headers=A,
    )
    assert r.status_code == 200
    assert calls == [(world.north1.id, world.north_team, world.south_team)]
    # moving to the same team is a no-op (hook not fired, no audit row)
    n = len(await audit_rows(world.org_id))
    await client.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "reason": "again"},
        headers=A,
    )
    assert len(calls) == 1 and len(await audit_rows(world.org_id)) == n
    await dispatch_all()
    got = [x for x in await notifications_for(world.north1.id) if x.type == "user.team_changed"]
    assert len(got) == 1 and "Team South" in got[0].body
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT team_id, ended_at IS NULL FROM team_memberships WHERE user_id=:u ORDER BY started_at"
                ),
                {"u": world.north1.id},
            )
        ).all()
    assert rows == [(world.north_team, False), (world.south_team, True)]


async def test_a_failing_move_hook_rolls_the_whole_move_back(client, world, monkeypatch):
    async def bad(uow, user, old, new):
        raise RuntimeError("deals module down")

    monkeypatch.setattr(users_service, "TEAM_MOVE_HOOKS", [bad])
    n = len(await audit_rows(world.org_id))
    r = await client.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "reason": "x"},
        headers=h(world.admin),
    )
    assert r.status_code == 500
    assert (await get_user_row(world.north1.id)).team_id == world.north_team
    assert len(await audit_rows(world.org_id)) == n
    assert "deals module down" not in r.text


async def test_leaders_cannot_be_moved_and_admins_have_no_team(client, world):
    A = h(world.admin)
    assert (
        await client.patch(
            f"/users/{world.tl_north.id}",
            json={"team_id": str(world.south_team), "reason": "x"},
            headers=A,
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/users/{world.sa.id}",
            json={"team_id": str(world.south_team), "reason": "x"},
            headers=A,
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/users/{world.north1.id}", json={"team_id": None, "reason": "x"}, headers=A
        )
    ).status_code == 409


# ------------------------------------------------------------------ teams
async def test_team_names_are_unique_case_insensitively_and_rename_works(client, world):
    A = h(world.admin)
    assert (
        await client.post("/teams", json={"name": "TEAM north", "reason": "r"}, headers=A)
    ).status_code == 409
    assert (
        await client.post("/teams", json={"name": "  team north ", "reason": "r"}, headers=A)
    ).status_code == 409
    r = await client.post("/teams", json={"name": "East", "reason": "r"}, headers=A)
    assert r.status_code == 201 and r.json()["member_count"] == 0 and r.json()["leader_id"] is None
    tid = r.json()["id"]
    assert (
        await client.patch(f"/teams/{tid}", json={"name": "team south", "reason": "r"}, headers=A)
    ).status_code == 409
    assert (
        await client.patch(f"/teams/{tid}", json={"name": "EAST", "reason": "case only"}, headers=A)
    ).status_code == 200
    assert (
        await client.patch(f"/teams/{tid}", json={"name": "Far East", "reason": "r"}, headers=A)
    ).json()["name"] == "Far East"
    assert (
        await client.patch(f"/teams/{uuid.uuid4()}", json={"name": "x", "reason": "r"}, headers=A)
    ).status_code == 404
    names = {t["name"] for t in (await client.get("/teams", headers=A)).json()}
    assert {"Team North", "Team South", "Far East"} <= names


async def test_team_list_member_counts_and_leaders_are_right(client, world):
    teams = {t["name"]: t for t in (await client.get("/teams", headers=h(world.sa))).json()}
    assert teams["Team North"]["member_count"] == 3 and teams["Team North"]["leader_name"] == "Tara"
    assert teams["Team South"]["leader_id"] == str(world.tl_south.id)


async def test_one_team_per_leader_is_enforced_by_the_database(world, admin_engine):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE teams SET leader_id=:l WHERE id=:t"),
                {"l": world.tl_north.id, "t": world.south_team},
            )
    with pytest.raises(IntegrityError):  # and one open membership per user
        async with admin_engine.begin() as c:
            await c.execute(
                text("INSERT INTO team_memberships (org_id,user_id,team_id) VALUES (:o,:u,:t)"),
                {"o": world.org_id, "u": world.north1.id, "t": world.south_team},
            )


# ------------------------------------------------------------------ passwords
async def test_password_reset_rules_and_provider_calls(client, world, world_b):
    provider = get_auth_provider()
    provider.sent_resets.clear()
    # own: every role, no reason needed for User and TL
    for a in (world.north1, world.tl_north, world.sa):
        assert (
            await client.post(f"/users/{a.id}/password-reset", json={}, headers=h(a))
        ).status_code == 204
    # others: a rep or TL cannot; an Admin must give a reason; a Super admin needs none
    assert (
        await client.post(
            f"/users/{world.north2.id}/password-reset",
            json={"reason": "x"},
            headers=h(world.tl_north),
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/users/{world.north2.id}/password-reset", json={}, headers=h(world.admin)
        )
    ).status_code == 422
    assert (
        await client.post(
            f"/users/{world.north2.id}/password-reset",
            json={"reason": "locked out"},
            headers=h(world.admin),
        )
    ).status_code == 204
    assert (
        await client.post(f"/users/{world.north2.id}/password-reset", json={}, headers=h(world.sa))
    ).status_code == 204
    assert (
        await client.post(
            f"/users/{world_b.north1.id}/password-reset", json={"reason": "x"}, headers=h(world.sa)
        )
    ).status_code == 404
    assert provider.sent_resets == [
        ("self", world.north1.email), ("self", world.tl_north.email), ("self", world.sa.email),
        ("admin", world.north2.email), ("admin", world.north2.email),
    ]  # fmt: skip
    # nothing was sent for refused or cross-org attempts, and no password ever appears in a response/audit row
    rows = await audit_rows(world.org_id, action_key="password.reset_others")
    assert [r["outcome"] for r in rows].count("ok") == 2
    assert not any("password" in str(r["new_value"]).lower() for r in rows)


async def test_admin_can_reset_a_super_admins_password_note(client, world):
    """NOTE: an Admin may reset a Super admin's password (matrix: all). The reset mail goes to the
    Super admin's own address, so it is not a takeover, but it is a way to lock a Super admin out."""
    assert (
        await client.post(
            f"/users/{world.sa.id}/password-reset", json={"reason": "x"}, headers=h(world.admin)
        )
    ).status_code == 204


async def test_nul_in_names_reason_and_team_names_does_not_500(client, world):
    A = h(world.admin)
    r = await create(client, world.admin, name="a" + NUL, team_id=str(world.north_team))
    r2 = await create(client, world.admin, reason="r" + NUL, team_id=str(world.north_team))
    r3 = await client.post("/teams", json={"name": "t" + NUL, "reason": "r"}, headers=A)
    r4 = await client.patch(
        f"/users/{world.north2.id}", json={"name": "n" + NUL, "reason": "r"}, headers=A
    )
    codes = [r.status_code, r2.status_code, r3.status_code, r4.status_code]
    assert all(c in (201, 422) for c in codes), codes  # BUG-P1-01 while this fails
    _ = A
