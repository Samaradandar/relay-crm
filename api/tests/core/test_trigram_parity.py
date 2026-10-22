# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The Python trigram similarity must equal PostgreSQL's pg_trgm similarity(), so results do not
change between a server with the extension (CI, production) and one without (embedded dev)."""

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.core.trigram import similarity

NAMES = [
    ("Zenith Foods Pvt", "Zenith Foods Pvt Ltd"),
    ("Acme Industries", "ACME industries"),
    ("Tata Consultancy Services", "TCS Limited"),
    ("Orchid Textiles", "Orchid Textile Mills"),
    ("Sharma & Sons", "Sharma and Sons"),
    ("", "Anything"),
]


async def test_python_similarity_matches_pg_trgm(database):
    async with get_sessionmaker()() as s:
        has = (
            await s.execute(text("SELECT 1 FROM pg_available_extensions WHERE name='pg_trgm'"))
        ).first()
        if not has:
            pytest.skip(
                "pg_trgm is not installed on this server (CI uses postgres:16, which has it)"
            )
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database.admin_url)  # superuser: CREATE EXTENSION needs it
    try:
        async with engine.begin() as c:
            await c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            for a, b in NAMES:
                got = (
                    await c.execute(
                        text("SELECT similarity(lower(:a), lower(:b))"), {"a": a, "b": b}
                    )
                ).scalar_one()
                assert similarity(a, b) == pytest.approx(float(got), abs=1e-6), (a, b)
    finally:
        await engine.dispose()
