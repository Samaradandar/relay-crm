# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_sessionmaker
from app.core.errors import OutOfScope, PermissionDenied, ReasonRequired
from app.platform.audit import service as audit_service
from app.platform.audit.models import AuditLog
from app.platform.outbox.models import OutboxEvent
from app.platform.permissions import loader
from app.platform.permissions.matrix import seed_table
from app.platform.permissions.roles import Scope
from app.platform.permissions.scope import in_scope, select_scoped
from app.platform.uow import UnitOfWork
from tests.fixtures.widget import Widget, create_widget_table


async def test_db_matrix_equals_code_seed(database):
    async with get_sessionmaker()() as s:
        table = await loader.get_table(s)
    assert table == seed_table()


@pytest.fixture
async def widgets(world, admin_engine):
    await create_widget_table(admin_engine)
    w = world
    owners = {
        "mine": w.north1,
        "mate": w.north2,
        "other": w.south1,
        "tl": w.tl_north,
        "sa": w.sa,
    }
    ids = {}
    async with admin_engine.begin() as c:
        for name, a in owners.items():
            wid = uuid.uuid4()
            ids[name] = wid
            await c.execute(
                text("INSERT INTO widgets (id, org_id, owner_id, name) VALUES (:i,:o,:u,:n)"),
                {"i": wid, "o": w.org_id, "u": a.id, "n": name},
            )
    return ids


async def names(actor, scope):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select_scoped(actor, Widget, scope))).scalars().all()
    return {r.name for r in rows}


async def test_scope_own_team_all(world, widgets):
    assert await names(world.north1, Scope.OWN) == {"mine"}
    assert await names(world.north1, Scope.TEAM) == {"mine", "mate", "tl"}  # not SA-owned
    assert await names(world.tl_north, Scope.TEAM) == {"mine", "mate", "tl"}
    assert await names(world.tl_south, Scope.TEAM) == {"other"}
    assert await names(world.tl_north, Scope.ALL) == {"mine", "mate", "other", "tl"}  # SA hidden
    assert await names(world.admin, Scope.ALL) == {"mine", "mate", "other", "tl", "sa"}
    assert await names(world.sa, Scope.ALL) == {"mine", "mate", "other", "tl", "sa"}
    assert await names(world.north1, Scope.NONE) == set()


async def test_scope_never_crosses_orgs(world, widgets, admin_engine):
    other_org = uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id,name) VALUES (:i,'x')"), {"i": other_org})
    from dataclasses import replace

    stranger = replace(world.admin, org_id=other_org)
    assert await names(stranger, Scope.ALL) == set()


async def test_in_scope_single_record(world, widgets):
    async with get_sessionmaker()() as s:
        assert await in_scope(s, world.north1, Scope.OWN, "widget", widgets["mine"])
        assert not await in_scope(s, world.north1, Scope.OWN, "widget", widgets["mate"])
        assert await in_scope(s, world.tl_north, Scope.TEAM, "widget", widgets["mate"])
        assert not await in_scope(s, world.tl_north, Scope.TEAM, "widget", widgets["other"])
        assert not await in_scope(s, world.tl_north, Scope.ALL, "widget", widgets["sa"])
        assert await in_scope(s, world.admin, Scope.ALL, "widget", widgets["sa"])
        assert not await in_scope(s, world.admin, Scope.ALL, "nope", widgets["sa"])
        assert not await in_scope(s, world.admin, Scope.ALL, "widget", "not-a-uuid")


# ------------------------------------------------------------------ audit
async def test_audit_chain_and_immutability(world, database, admin_engine):
    async with UnitOfWork(world.admin) as uow:
        for i in range(3):
            await uow.audit("deal.edit", "widget", f"w{i}", new={"i": i}, reason="because")
    async with get_sessionmaker()() as s:
        res = await audit_service.verify_chain(s, world.org_id)
        assert res.ok and res.checked == 3
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.org_id == world.org_id).order_by(AuditLog.seq)
                )
            )
            .scalars()
            .all()
        )
        assert rows[0].prev_hash == "GENESIS" and rows[1].prev_hash == rows[0].row_hash

    # the restricted app role cannot update or delete (privilege AND trigger)
    for stmt in ("UPDATE audit_log SET reason='x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        async with get_sessionmaker()() as s:
            with pytest.raises(DBAPIError):
                await s.execute(text(stmt))
                await s.commit()
    # even the owner is stopped by the trigger
    async with admin_engine.connect() as c:
        with pytest.raises(DBAPIError, match="append-only"):
            await c.execute(
                text("UPDATE audit_log SET reason='x' WHERE org_id=:o"), {"o": world.org_id}
            )

    # tamper bypassing the trigger (superuser) is detected by the chain check
    async with admin_engine.begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        await c.execute(
            text("UPDATE audit_log SET reason='tampered' WHERE org_id=:o AND entity_id='w1'"),
            {"o": world.org_id},
        )
        await c.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))
    async with get_sessionmaker()() as s:
        res = await audit_service.verify_chain(s, world.org_id)
    assert not res.ok and "hash" in (res.problem or "")


async def test_audit_serialises_per_org(world, database):
    import asyncio

    async def one(i):
        async with UnitOfWork(world.admin) as uow:
            await uow.audit("deal.edit", "widget", f"c{i}", new={"i": i}, reason="r")

    await asyncio.gather(*(one(i) for i in range(15)))
    async with get_sessionmaker()() as s:
        res = await audit_service.verify_chain(s, world.org_id)
    assert res.ok and res.checked == 15


# ------------------------------------------------------------------ unit of work
async def test_uow_rolls_back_change_audit_and_outbox_together(world, database):
    wid = uuid.uuid4()
    with pytest.raises(RuntimeError):
        async with UnitOfWork(world.admin) as uow:
            await uow.audit("deal.edit", "widget", str(wid), new={"a": 1}, reason="r")
            await uow.emit("widget.created", {"id": str(wid)})
            raise RuntimeError("boom after outbox insert")
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(select(AuditLog).where(AuditLog.entity_id == str(wid)))
        ).first() is None
        assert (
            await s.execute(select(OutboxEvent).where(OutboxEvent.org_id == world.org_id))
        ).first() is None


async def test_uow_commit_writes_audit_and_outbox(world, database):
    async with UnitOfWork(world.admin) as uow:
        await uow.audit("deal.edit", "widget", "w-ok", new={"a": 1}, reason="r")
        await uow.emit("widget.created", {"id": "w-ok"})
    async with get_sessionmaker()() as s:
        assert (await s.execute(select(AuditLog).where(AuditLog.entity_id == "w-ok"))).first()
        assert (
            await s.execute(select(OutboxEvent).where(OutboxEvent.org_id == world.org_id))
        ).first()


async def test_denied_attempt_is_audited_and_rolled_back(world, database):
    with pytest.raises(PermissionDenied):
        async with UnitOfWork(world.north1) as uow:
            await uow.audit("deal.edit", "widget", "should-not-exist", reason="r")
            await uow.authorize("pool.assign", entity=("widget", "w9"))
    async with get_sessionmaker()() as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.org_id == world.org_id)))
            .scalars()
            .all()
        )
    assert [r.entity_id for r in rows] == ["w9"]
    assert rows[0].outcome == "denied" and rows[0].action_key == "pool.assign"


async def test_out_of_scope_and_reason_checks(world, widgets, database):
    with pytest.raises(OutOfScope):
        async with UnitOfWork(world.tl_north) as uow:
            await uow.authorize("deal.edit", entity=("widget", widgets["other"]))
    async with UnitOfWork(world.tl_north) as uow:
        await uow.authorize("deal.edit", entity=("widget", widgets["mate"]))
    # important action: reason required for TL, Admin; optional for SA
    async with UnitOfWork(world.tl_north) as uow:
        with pytest.raises(ReasonRequired):
            uow.reason("deal.lose", "  ")
    async with UnitOfWork(world.sa) as uow:
        assert uow.reason("deal.lose", None) is None
    async with UnitOfWork(world.admin) as uow:
        with pytest.raises(ReasonRequired):
            uow.reason("deal.edit", None)  # routine, but Admin always needs a reason


async def test_own_deals_only_uses_owner(world, database):
    async with UnitOfWork(world.tl_north) as uow:
        await uow.authorize("criterion.tick", owner_id=world.tl_north.id, check_scope=False)
        with pytest.raises(PermissionDenied):
            await uow.authorize("criterion.tick", owner_id=world.north1.id, check_scope=False)
