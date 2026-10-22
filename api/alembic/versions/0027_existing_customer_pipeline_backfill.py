# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Give every org the Existing customer pipeline, version 1 (published).

0003 seeded only New business for orgs that already existed, and no later migration ran the
Existing customer seed, so a database created by migrations had the `existing_customer` pipeline
row with no version: its stage pages and account requests failed with "The existing customer
pipeline has no published version". The seed is idempotent (it skips an org that already has a
version), so this is safe on databases where it did run.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa

from alembic import op
from app.modules.pipeline.seeds.existing_customer import seed_statements

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for (org_id,) in conn.execute(sa.text("SELECT id FROM orgs")).all():
        for sql, params in seed_statements(org_id):
            conn.execute(sa.text(sql), params)


def downgrade() -> None:
    # Data backfill only; the version may already carry deals, so it is left in place.
    pass
