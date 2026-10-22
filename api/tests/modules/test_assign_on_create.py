# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""New lead form: a Team Leader / Admin / Super admin picks the owner (`owner_id` on POST /leads).

The pick reuses the manual Pool-assign checks inside the create transaction: one lead.create audit
row plus one pool.assign row, method 'manual', the new owner notified, no engine run."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.modules.leads import service as leads_service
from app.platform.permissions.roles import Role
from tests.conftest import insert_user
from tests.helpers import dispatch_all, headers, notifications_for
from tests.modules.test_leads_api import audit_keys, configure, create, lead


async def post(client, actor, owner=None, **kw):
    body = lead(**kw)
    if owner is not None:
        body["owner_id"] = str(owner)
    return await client.post("/leads", json=body, headers=headers(actor))


async def counts(admin_engine, org_id) -> dict[str, int]:
    out = {}
    async with admin_engine.connect() as c:
        for t in ("deals", "accounts", "contacts", "lead_pool_entries", "next_steps"):
            r = await c.execute(text(f"SELECT count(*) FROM {t} WHERE org_id = :o"), {"o": org_id})
            out[t] = r.scalar_one()
    return out


async def audit_count(admin_engine, org_id) -> int:
    async with admin_engine.connect() as c:
        r = await c.execute(
            text("SELECT count(*) FROM audit_log WHERE org_id = :o AND outcome = 'ok'"),
            {"o": org_id},
        )
        return r.scalar_one()


async def set_inactive(admin_engine, uid):
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE users SET is_active = false WHERE id = :u"), {"u": uid})


# ------------------------------------------------------------------------------ happy paths
async def test_tl_assigns_to_a_rep_in_own_team(client, world):
    await configure(client, world)
    r = await post(client, world.tl_north, world.north2.id, reason="best fit")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "open" and d["owner"]["id"] == str(world.north2.id)
    assert d["assignment_method"] == "manual" and d["assigned_by"] == "Tara"
    assert d["team_name"] == "Team North" and d["generated_by"] == "Tara"
    assert d["next_step"]["responsible"]["id"] == str(world.north2.id)
    # exactly one create row and one manual-assignment row; the engine did not also run
    assert await audit_keys(d["id"]) == [("lead.create", "ok"), ("pool.assign", "ok")]
    await dispatch_all()
    notes = [n for n in await notifications_for(world.north2.id) if n.type == "lead.assigned"]
    assert len(notes) == 1
    assert not [n for n in await notifications_for(world.north1.id) if n.type == "lead.assigned"]


async def test_tl_assigns_to_self(client, world):
    r = await post(client, world.tl_north, world.tl_north.id, reason="mine")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["owner"]["id"] == str(world.tl_north.id) and d["assignment_method"] == "manual"
    assert d["team_name"] == "Team North"
    got = await client.get(f"/deals/{d['id']}", headers=headers(world.tl_north))
    assert got.status_code == 200


async def test_admin_assigns_anywhere_and_to_self(client, world):
    r = await post(client, world.admin, world.south1.id, reason="south needs it")
    assert r.status_code == 201, r.text
    assert r.json()["owner"]["id"] == str(world.south1.id)
    assert r.json()["team_name"] == "Team South"
    me = await post(client, world.admin, world.admin.id, reason="mine")
    assert me.status_code == 201, me.text
    assert me.json()["owner"]["id"] == str(world.admin.id)


async def test_super_admin_reason_is_optional(client, world):
    r = await post(client, world.sa, world.north1.id)
    assert r.status_code == 201, r.text
    assert r.json()["owner"]["id"] == str(world.north1.id)
    assert await audit_keys(r.json()["id"]) == [("lead.create", "ok"), ("pool.assign", "ok")]


async def test_null_owner_is_automatic_as_before(client, world):
    await configure(client, world)
    r = await client.post(
        "/leads", json={**lead(), "owner_id": None, "reason": "x"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["assignment_method"] == "round_robin" and d["assigned_by"] == "System"
    assert ("lead.assigned", "ok") in await audit_keys(d["id"])


async def test_cap_warning_assigns_anyway(client, world):
    await configure(client, world, cap=1)
    first = await post(client, world.tl_north, world.north1.id, reason="r")
    assert first.status_code == 201 and first.json()["warning"] is None
    r = await post(client, world.tl_north, world.north1.id, reason="override cap")
    assert r.status_code == 201, r.text
    assert r.json()["owner"]["id"] == str(world.north1.id)
    assert "cap" in r.json()["warning"]
    again = await post(client, world.tl_north, world.north2.id, reason="r")
    assert again.json()["warning"] is None


# ------------------------------------------------------------------------------ permissions
async def test_sales_rep_cannot_choose_an_owner(client, world, admin_engine):
    before = await counts(admin_engine, world.org_id)
    r = await post(client, world.north1, world.north2.id)
    assert r.status_code == 403, r.text
    assert await counts(admin_engine, world.org_id) == before
    plain = await post(client, world.north1)  # no owner_id still works
    assert plain.status_code == 201


async def test_tl_reason_required_for_a_manual_pick_but_not_for_automatic(client, world):
    r = await post(client, world.tl_north, world.north1.id)
    assert r.status_code == 422, r.text
    ok = await post(client, world.tl_north)
    assert ok.status_code == 201


async def test_admin_reason_required(client, world):
    r = await post(client, world.admin, world.north1.id)
    assert r.status_code == 422, r.text


async def test_tl_cannot_assign_outside_own_team(client, world, admin_engine):
    before = await counts(admin_engine, world.org_id)
    for target in (world.south1.id, world.tl_south.id):
        r = await post(client, world.tl_north, target, reason="wrong team")
        assert r.status_code == 403, r.text
    assert await counts(admin_engine, world.org_id) == before


# ------------------------------------------------------------------------------ bad targets
async def test_inactive_user_is_rejected(client, world, admin_engine):
    await set_inactive(admin_engine, world.north2.id)
    before = await counts(admin_engine, world.org_id)
    for actor in (world.tl_north, world.admin):
        r = await post(client, actor, world.north2.id, reason="x")
        assert r.status_code == 422, r.text
    assert await counts(admin_engine, world.org_id) == before


async def test_unknown_and_other_org_user_are_rejected(client, world, admin_engine):
    other_org = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id, name) VALUES (:i, 'Other')"), {"i": other_org})
    stranger = await insert_user(admin_engine, other_org, "Stranger", Role.USER, None)
    before = await counts(admin_engine, world.org_id)
    for actor in (world.tl_north, world.admin, world.sa):
        for target in (stranger.id, uuid.uuid4()):
            r = await post(client, actor, target, reason="x")
            assert r.status_code == 422, r.text
    assert await counts(admin_engine, world.org_id) == before
    assert (await counts(admin_engine, other_org))["deals"] == 0


async def test_deleted_user_is_rejected(client, world, admin_engine):
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE users SET deleted_at = now(), is_active = false WHERE id = :u"),
            {"u": world.north2.id},
        )
    r = await post(client, world.admin, world.north2.id, reason="x")
    assert r.status_code == 422, r.text


async def test_bad_owner_id_value_is_a_validation_error(client, world):
    r = await client.post(
        "/leads", json={**lead(), "owner_id": "not-a-uuid"}, headers=headers(world.tl_north)
    )
    assert r.status_code == 422


# ------------------------------------------------------------------------------ duplicates
async def test_duplicate_checks_still_apply_with_an_owner(client, world):
    first = await post(client, world.admin, world.north1.id, company="Zenith Foods", reason="r")
    assert first.status_code == 201
    dup = await post(client, world.admin, world.north2.id, company="Zenith Foods", reason="r")
    assert dup.status_code in (409, 422), dup.text
    assert dup.json()["code"].startswith("duplicate")


# ------------------------------------------------------------------------------ atomicity
async def test_failure_after_assignment_leaves_nothing(world, database, admin_engine, monkeypatch):
    from app.main import create_app

    real = leads_service.apply_owner

    async def boom(*a, **kw):
        await real(*a, **kw)
        raise RuntimeError("simulated failure after the assignment")

    monkeypatch.setattr(leads_service, "apply_owner", boom)
    transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        before = await counts(admin_engine, world.org_id)
        audits = await audit_count(admin_engine, world.org_id)
        r = await post(c, world.tl_north, world.north1.id, reason="x")
        assert r.status_code == 500
    assert await counts(admin_engine, world.org_id) == before
    assert await audit_count(admin_engine, world.org_id) == audits
    async with admin_engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM outbox_events WHERE org_id = :o"), {"o": world.org_id}
            )
        ).scalar_one()
    assert n == 0


# ------------------------------------------------------------------------------ round robin
async def test_manual_pick_does_not_move_the_round_robin_pointer(client, world, admin_engine):
    await configure(client, world)

    async def pointer():
        async with admin_engine.connect() as c:
            return (
                await c.execute(
                    text("SELECT last_assigned_user_id FROM rr_pointers WHERE org_id = :o"),
                    {"o": world.org_id},
                )
            ).scalar_one_or_none()

    a = await create(client, world.north1)  # automatic
    first = a["owner"]["id"]
    ptr = await pointer()
    assert str(ptr) == first
    for target in (world.north1.id, world.north2.id, world.tl_north.id):
        assert (await post(client, world.tl_north, target, reason="manual")).status_code == 201
    assert await pointer() == ptr
    other = str(world.north2.id) if first == str(world.north1.id) else str(world.north1.id)
    b = await create(client, world.north1)
    assert b["owner"]["id"] == other and b["assignment_method"] == "round_robin"


@pytest.mark.parametrize("role", ["tl_north", "admin", "sa"])
async def test_creator_and_assigner_are_the_actor(client, world, role):
    actor = getattr(world, role)
    r = await post(client, actor, world.north1.id, reason="r")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["generated_by"] == actor.name and d["assigned_by"] == actor.name
