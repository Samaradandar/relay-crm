# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Test fixtures: a real PostgreSQL (never SQLite), one fresh org per test, one user per role.

By default an embedded Postgres 16 (pgserver) is started, so tests need no Docker. Set
TEST_ADMIN_URL (a superuser asyncpg URL, e.g. the compose postgres) to use another server.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from app.core import db as core_db
from app.core.config import get_settings
from app.modules.pipeline.seeds import seed_org_pipeline
from app.platform.context import Actor
from app.platform.permissions import loader
from app.platform.permissions.roles import Role

if sys.platform == "win32":  # psycopg (job queue) needs the selector event loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

API_DIR = Path(__file__).resolve().parent.parent


@dataclass
class DbInfo:
    admin_url: str  # superuser, asyncpg-style SQLAlchemy URL for the test database
    app_url: str  # restricted relay_app role


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[DbInfo]:
    server = None
    scratch = Path()
    external = os.environ.get("TEST_ADMIN_URL")
    if external:
        base = external.rsplit("/", 1)[0]
        raw_admin = base.replace("postgresql+asyncpg://", "postgresql://") + "/postgres"
    else:
        import pgserver

        scratch = Path(tempfile.mkdtemp())
        server = pgserver.get_server(scratch / "pg")
        raw_admin = server.get_uri()
    # raw_admin: postgresql://user[:pw]@host:port/postgres
    scheme_rest = raw_admin.split("://", 1)[1]
    creds_host, _ = scheme_rest.rsplit("/", 1)
    dbname = "relay_test_" + uuid.uuid4().hex[:8]
    conn = await asyncpg.connect(raw_admin)
    try:
        exists = await conn.fetchval("select 1 from pg_roles where rolname='relay_app'")
        if not exists:
            await conn.execute("CREATE ROLE relay_app LOGIN PASSWORD 'relay_app'")
        await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()
    host = creds_host.rsplit("@", 1)[1]
    admin_user_part = creds_host.rsplit("@", 1)[0]
    admin_url = f"postgresql+asyncpg://{admin_user_part}@{host}/{dbname}"
    app_url = f"postgresql+asyncpg://relay_app:relay_app@{host}/{dbname}"

    os.environ["DATABASE_URL"] = app_url
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    os.environ.setdefault("ENV", "test")
    get_settings.cache_clear()
    await core_db.dispose_engine()

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", admin_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    try:
        yield DbInfo(admin_url=admin_url, app_url=app_url)
    finally:
        await core_db.dispose_engine()
        if server is not None:
            server.cleanup()
            # cleanup() only stops the server; drop its ~50 MB data folder, which this session created
            shutil.rmtree(scratch, ignore_errors=True)


@pytest.fixture(scope="session")
async def admin_engine(database: DbInfo) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database.admin_url)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_permission_cache() -> None:
    loader.invalidate()


@dataclass
class World:
    org_id: uuid.UUID
    sa: Actor
    admin: Actor
    tl_north: Actor
    tl_south: Actor
    north1: Actor
    north2: Actor
    south1: Actor
    south2: Actor
    north_team: uuid.UUID
    south_team: uuid.UUID

    @property
    def all_actors(self) -> list[Actor]:
        return [
            self.sa,
            self.admin,
            self.tl_north,
            self.tl_south,
            self.north1,
            self.north2,
            self.south1,
            self.south2,
        ]


async def insert_user(
    engine: AsyncEngine, org_id: uuid.UUID, name: str, role: Role, team_id: uuid.UUID | None
) -> Actor:
    uid = uuid.uuid4()
    email = f"{name.lower().replace(' ', '.')}.{uid.hex[:6]}@example.com"
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO users (id, org_id, email, name, role, team_id) "
                "VALUES (:id, :o, :e, :n, :r, :t)"
            ),
            {"id": uid, "o": org_id, "e": email, "n": name, "r": role.value, "t": team_id},
        )
        if team_id:
            await c.execute(
                text("INSERT INTO team_memberships (org_id, user_id, team_id) VALUES (:o,:u,:t)"),
                {"o": org_id, "u": uid, "t": team_id},
            )
    return Actor(id=uid, org_id=org_id, role=role.value, team_id=team_id, name=name, email=email)


@pytest.fixture
async def world(admin_engine: AsyncEngine) -> World:
    org_id = uuid.uuid4()
    north, south = uuid.uuid4(), uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id, name) VALUES (:i, 'Test org')"), {"i": org_id})
        await seed_org_pipeline(c, org_id)
        for tid, tname in ((north, "Team North"), (south, "Team South")):
            await c.execute(
                text("INSERT INTO teams (id, org_id, name) VALUES (:i,:o,:n)"),
                {"i": tid, "o": org_id, "n": tname},
            )
    sa = await insert_user(admin_engine, org_id, "Samar", Role.SUPER_ADMIN, None)
    admin = await insert_user(admin_engine, org_id, "Anita", Role.ADMIN, None)
    tl_n = await insert_user(admin_engine, org_id, "Tara", Role.TEAM_LEADER, north)
    tl_s = await insert_user(admin_engine, org_id, "Tejas", Role.TEAM_LEADER, south)
    users = [
        await insert_user(admin_engine, org_id, n, Role.USER, t)
        for n, t in (("Priya", north), ("Rahul", north), ("Sneha", south), ("Vikram", south))
    ]
    async with admin_engine.begin() as c:
        for tid, leader in ((north, tl_n), (south, tl_s)):
            await c.execute(
                text("UPDATE teams SET leader_id = :l WHERE id = :t"), {"l": leader.id, "t": tid}
            )
    return World(org_id, sa, admin, tl_n, tl_s, *users, north, south)


@pytest.fixture
async def client(database: DbInfo) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
