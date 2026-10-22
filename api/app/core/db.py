# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Database engine, session, and the one-transaction helper every service write uses."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url,
            pool_pre_ping=True,
            pool_size=s.db_pool_size,
            max_overflow=s.db_pool_overflow,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


@asynccontextmanager
async def transaction() -> AsyncGenerator[AsyncSession]:
    """One session, one transaction: commit on success, roll back everything on any error.

    The change, its audit_log row and its outbox_events row are written inside one of these.
    """
    async with get_sessionmaker()() as session, session.begin():
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for read-only use. Writes must go through a service + transaction()."""
    async with get_sessionmaker()() as session:
        yield session
