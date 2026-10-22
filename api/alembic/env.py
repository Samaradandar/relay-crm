# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Alembic environment. Uses the migration (owner) URL, never the restricted app role."""

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings

config = context.config


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().migration_database_url


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(_run_async())
