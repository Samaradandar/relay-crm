# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Fixtures for the Phase 1 deep tests: a second organisation, fake storage, memory email."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.pipeline.seeds import seed_org_pipeline
from app.platform.files import storage
from app.platform.notifications import email
from app.platform.permissions.roles import Role
from tests.conftest import World, insert_user
from tests.deep.phase1.kit import FakeStorage


@pytest.fixture
async def world_b(admin_engine: AsyncEngine) -> World:
    """A second, complete organisation (same shape as `world`). Org A is `world`."""
    org_id = uuid.uuid4()
    north, south = uuid.uuid4(), uuid.uuid4()
    async with admin_engine.begin() as c:
        await c.execute(text("INSERT INTO orgs (id, name) VALUES (:i, 'Other org')"), {"i": org_id})
        await seed_org_pipeline(c, org_id)
        for tid, tname in ((north, "Team North"), (south, "Team South")):
            await c.execute(
                text("INSERT INTO teams (id, org_id, name) VALUES (:i,:o,:n)"),
                {"i": tid, "o": org_id, "n": tname},
            )
    sa = await insert_user(admin_engine, org_id, "Bsamar", Role.SUPER_ADMIN, None)
    admin = await insert_user(admin_engine, org_id, "Banita", Role.ADMIN, None)
    tl_n = await insert_user(admin_engine, org_id, "Btara", Role.TEAM_LEADER, north)
    tl_s = await insert_user(admin_engine, org_id, "Btejas", Role.TEAM_LEADER, south)
    users = [
        await insert_user(admin_engine, org_id, n, Role.USER, t)
        for n, t in (("Bpriya", north), ("Brahul", north), ("Bsneha", south), ("Bvikram", south))
    ]
    async with admin_engine.begin() as c:
        for tid, leader in ((north, tl_n), (south, tl_s)):
            await c.execute(
                text("UPDATE teams SET leader_id = :l WHERE id = :t"), {"l": leader.id, "t": tid}
            )
    return World(org_id, sa, admin, tl_n, tl_s, *users, north, south)


@pytest.fixture
def fake_storage():
    fs = FakeStorage()
    storage.set_storage(fs)
    yield fs
    storage.set_storage(None)


@pytest.fixture(autouse=True)
def memory_email():
    backend = email.MemoryBackend()
    email.set_backend(backend)
    yield backend
    email.set_backend(None)


@pytest.fixture
async def client(database):  # noqa: ARG001
    """Like the shared `client`, but unhandled server errors come back as HTTP 500 (as they do in
    production) instead of being re-raised into the test."""
    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as c:
        yield c
