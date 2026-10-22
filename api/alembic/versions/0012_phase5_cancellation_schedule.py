# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 5 PR4 (P5-03): `billing_schedule.cancelled` flag.

`billing_schedule` is append-only by DB grant (no DELETE for relay_app, discovered in P5-02), so
cancelling a subscription's future billing can't drop rows -- it flags them instead. Invoice
generation (whichever PR builds auto-billing) must skip rows where `cancelled` is true.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE billing_schedule ADD COLUMN cancelled boolean NOT NULL DEFAULT false")
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE billing_schedule DROP COLUMN IF EXISTS cancelled"))
