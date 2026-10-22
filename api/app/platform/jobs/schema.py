# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Applies the Procrastinate tables with the owner (migration) role and grants the app role.

Run after `alembic upgrade head`:  python -m app.platform.jobs.schema
"""

import asyncio

import procrastinate
import psycopg

from app.core.config import get_settings


def owner_conninfo() -> str:
    return get_settings().migration_database_url.replace("postgresql+asyncpg://", "postgresql://")


async def apply_schema() -> None:
    owner = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=owner_conninfo()))
    async with await psycopg.AsyncConnection.connect(owner_conninfo(), autocommit=True) as conn:
        cur = await conn.execute("SELECT to_regclass('public.procrastinate_jobs')")
        installed = (await cur.fetchone() or (None,))[0] is not None
    if not installed:
        async with owner.open_async():
            await owner.schema_manager.apply_schema_async()
    async with await psycopg.AsyncConnection.connect(owner_conninfo(), autocommit=True) as conn:
        await conn.execute(
            """DO $$ DECLARE t text; BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
                -- Only the job-queue tables are granted here; business tables keep the grants of
                -- the migrations (never ALL: no TRUNCATE / TRIGGER / REFERENCES; BUG-P1-11).
                FOR t IN SELECT tablename FROM pg_tables
                         WHERE schemaname = 'public' AND tablename LIKE 'procrastinate%' LOOP
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO relay_app', t);
                END LOOP;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relay_app;
                GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO relay_app;
                REVOKE TRUNCATE, TRIGGER, REFERENCES ON ALL TABLES IN SCHEMA public FROM relay_app;
                END IF; END $$"""
        )


if __name__ == "__main__":
    asyncio.run(apply_schema())
