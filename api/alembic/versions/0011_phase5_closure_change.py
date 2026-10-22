# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 5 PR3 (P5-02): `deals.closure_change`, the Closure-stage record of what is changing on
the subscription (change_type, new plan values, effective date). Written by the owner while
ticking Closure's "New plan and effective date recorded" criterion; read by the Updated-stage
`deal.won` outbox handler to raise a pending SubscriptionChange (billing/changes.py).

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

CHANGE_TYPES = (
    "('top_up','plan_change','model_switch','other_product','renewal','increase','decrease')"
)


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE deals ADD COLUMN closure_change jsonb"))
    op.execute(
        sa.text(
            "ALTER TABLE deals ADD CONSTRAINT deals_closure_change_type_check CHECK "
            f"(closure_change IS NULL OR closure_change->>'change_type' IN {CHANGE_TYPES})"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_closure_change_type_check")
    )
    op.execute(sa.text("ALTER TABLE deals DROP COLUMN IF EXISTS closure_change"))
