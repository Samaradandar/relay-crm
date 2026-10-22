# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 7: widen deals.lead_source to add 'Inbound email' -- email-to-lead captures (P7-03) need
a lead source of their own, same pattern as 0010's 'Existing customer' addition. Confirmed the
label with Samar; entered manually today since there is no live inbox yet (see capture/service.py).

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

OLD = "('Website','Referral','LinkedIn','Event','Cold outbound','Inbound call','Existing customer')"
NEW = (
    "('Website','Referral','LinkedIn','Event','Cold outbound','Inbound call','Existing customer',"
    "'Inbound email')"
)


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_lead_source_check"))
    op.execute(
        sa.text(
            f"ALTER TABLE deals ADD CONSTRAINT deals_lead_source_check CHECK (lead_source IN {NEW})"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_lead_source_check"))
    op.execute(
        sa.text(
            f"ALTER TABLE deals ADD CONSTRAINT deals_lead_source_check CHECK (lead_source IN {OLD})"
        )
    )
