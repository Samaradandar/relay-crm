# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 6: rep_month_stats.lost_deals -- the frontend contract's RepSummary.lostMonth (section
12a) must be served from rep_month_stats like every other money/count field there, never from a
raw per-request deals query.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE rep_month_stats ADD COLUMN lost_deals integer NOT NULL DEFAULT 0")
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE rep_month_stats DROP COLUMN IF EXISTS lost_deals"))
