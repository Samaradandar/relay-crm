# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Fixtures for the Phase 2 deep tests: a client that returns 500s instead of raising them."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def qc(database) -> AsyncIterator[AsyncClient]:
    """Like `client`, but a crash inside the app comes back as a 500 response so a test can
    assert 'nothing returned 500' instead of dying on the first one."""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
