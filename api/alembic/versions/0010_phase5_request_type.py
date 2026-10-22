# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 5 PR2 (P5-01): `deals.request_type`, used only when pipeline = 'existing_customer'
(upsell/top_up, renewal, plan_change, other_product, increase, decrease). Nullable: every
other pipeline leaves it null.

A request-creation deal (POST /accounts/{id}/requests) still needs a `lead_source` (NOT NULL,
checked since 0002); it never came from a lead source, so the check constraint gains one more
value, 'Existing customer', for this one case.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SOURCES = (
    "('Website','Referral','LinkedIn','Event','Cold outbound','Inbound call','Existing customer')"
)

STATEMENTS = [
    """ALTER TABLE deals ADD COLUMN request_type text
    CHECK (request_type IN ('upsell_top_up','renewal','plan_change','other_product',
        'increase','decrease'))""",
    "ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_lead_source_check",
    f"ALTER TABLE deals ADD CONSTRAINT deals_lead_source_check CHECK (lead_source IN {SOURCES})",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_lead_source_check"))
    op.execute(
        sa.text(
            "ALTER TABLE deals ADD CONSTRAINT deals_lead_source_check CHECK (lead_source IN "
            "('Website','Referral','LinkedIn','Event','Cold outbound','Inbound call'))"
        )
    )
    op.execute(sa.text("ALTER TABLE deals DROP COLUMN IF EXISTS request_type"))
