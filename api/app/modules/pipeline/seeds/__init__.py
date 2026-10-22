# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Seeds the starting pipeline configuration for an organisation."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.pipeline.seeds.existing_customer import seed_statements as ec_seed_statements
from app.modules.pipeline.seeds.new_business import seed_statements


async def seed_org_pipeline(conn: AsyncConnection, org_id: uuid.UUID) -> None:
    """Give an org New business version 1, Existing customer version 1, the lost reasons and
    the fit checklist. Safe to call again: anything already there is left alone."""
    for sql, params in seed_statements(org_id):
        await conn.execute(text(sql), params)
    for sql, params in ec_seed_statements(org_id):
        await conn.execute(text(sql), params)
