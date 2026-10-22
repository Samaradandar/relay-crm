# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 5 PR6 (P5-06): widen deals.request_type to add 'return' -- a churned customer coming
back raises a new existing_customer deal with this request_type ("other product / return" in
the PRD), reusing create_request()'s shape instead of a new endpoint.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

OLD = "('upsell_top_up','renewal','plan_change','other_product','increase','decrease')"
NEW = "('upsell_top_up','renewal','plan_change','other_product','increase','decrease','return')"


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_request_type_check"))
    op.execute(
        sa.text(
            f"ALTER TABLE deals ADD CONSTRAINT deals_request_type_check "
            f"CHECK (request_type IN {NEW})"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_request_type_check"))
    op.execute(
        sa.text(
            f"ALTER TABLE deals ADD CONSTRAINT deals_request_type_check "
            f"CHECK (request_type IN {OLD})"
        )
    )
