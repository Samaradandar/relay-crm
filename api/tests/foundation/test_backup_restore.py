# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The restore drill, run for real against a scratch database: dump, restore, verify."""

import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine

from app.platform.audit import service as audit_service
from app.platform.uow import UnitOfWork
from scripts import backup_restore


async def test_backup_then_restore_into_a_fresh_database(world, database, tmp_path):
    async with UnitOfWork(world.admin) as uow:
        await uow.audit("deal.edit", "widget", "b1", new={"a": 1}, reason="before backup")
        await uow.emit("thing.happened", {"n": 1})

    raw_admin = database.admin_url.replace("postgresql+asyncpg://", "postgresql://")
    manifest = backup_restore.backup(raw_admin, tmp_path / "bk", bucket=None)
    assert manifest["dump_bytes"] > 1000 and (tmp_path / "bk" / "manifest.json").exists()

    base, _ = raw_admin.rsplit("/", 1)
    conn = await asyncpg.connect(base + "/postgres")
    await conn.execute('CREATE DATABASE "relay_restored"')
    await conn.close()
    result = backup_restore.restore(tmp_path / "bk", base + "/relay_restored", bucket=None)
    assert result["database"] == "restored"

    engine = create_async_engine(database.admin_url.rsplit("/", 1)[0] + "/relay_restored")
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker

        async with async_sessionmaker(engine)() as s:
            users = (
                await s.execute(
                    text("select count(*) from users where org_id=:o"), {"o": world.org_id}
                )
            ).scalar_one()
            assert users == len(world.all_actors)
            matrix = (await s.execute(text("select count(*) from role_permissions"))).scalar_one()
            assert matrix > 300
            outbox = (
                await s.execute(
                    text("select count(*) from outbox_events where type='thing.happened'")
                )
            ).scalar_one()
            assert outbox == 1
            # the audit hash chain survives the round trip intact
            res = await audit_service.verify_chain(s, world.org_id)  # type: ignore[arg-type]
            assert res.ok and res.checked >= 1
    finally:
        await engine.dispose()
