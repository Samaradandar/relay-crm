# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Proposal rounds, and a starter proposal template.

A proposal goes through rounds with the customer: a walkthrough meeting is held, the customer
answers, and if they want changes (or raise objections) the round is closed with what they asked
for and a new meeting is held to revise the proposal. The loop ends when the customer accepts.
`proposal_rounds` keeps every answer, so the whole story stays on the deal.

Also gives every org a starter proposal template (so "Create draft" works on day one); an Admin
edits or replaces it under Settings > Proposal template, and each save is a new version.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

STARTER_BODY = (
    "<h1>Proposal for {{company}}</h1>"
    "<p>Dear {{recipient_name}},</p>"
    "<p>Thank you for your time. As we discussed, here is our proposal for {{company}}.</p>"
    "<h2>What we will do</h2><p>{{scope}}</p>"
    "<h2>Plan</h2><p>{{pricing_model}} &ndash; {{plan}}, about "
    "{{expected_minutes_per_month}} minutes a month.</p>"
    "<h2>Pricing</h2>{{items_html}}"
    "<p><strong>Total: {{total_inr}}</strong></p>"
    "<p>Contract term: {{contract_term}}. Billing: {{billing_frequency}}.</p>"
    "<p>We look forward to working with you.</p>"
)
STARTER_TERMS = (
    "<h3>Terms</h3><p>Prices are in Indian rupees and exclude GST. This proposal is valid for "
    "30 days from the date it was sent. Payment terms follow the billing frequency above.</p>"
)

STATEMENTS = [
    """CREATE TABLE proposal_rounds (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        deal_id uuid NOT NULL REFERENCES deals(id),
        round_no integer NOT NULL CHECK (round_no >= 1),
        meeting_id uuid REFERENCES activities(id),
        feedback text NOT NULL CHECK (feedback IN ('accepted', 'changes_requested', 'objections')),
        changes text,
        recorded_by uuid REFERENCES users(id),
        recorded_at timestamptz NOT NULL DEFAULT now(),
        closed_at timestamptz,
        UNIQUE (deal_id, round_no))""",
    "CREATE INDEX proposal_rounds_deal_idx ON proposal_rounds (deal_id, round_no)",
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON proposal_rounds TO relay_app;
    END IF; END $$""",
]


def upgrade() -> None:
    conn = op.get_bind()
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))
    for (org_id,) in conn.execute(sa.text("SELECT id FROM orgs")).all():
        has = conn.execute(
            sa.text("SELECT 1 FROM proposal_templates WHERE org_id = :o LIMIT 1"), {"o": org_id}
        ).first()
        if not has:
            conn.execute(
                sa.text(
                    "INSERT INTO proposal_templates "
                    "(org_id, name, body, terms_html, version, active) "
                    "VALUES (:o, 'Standard proposal', :b, :t, 1, true)"
                ),
                {"o": org_id, "b": STARTER_BODY, "t": STARTER_TERMS},
            )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS proposal_rounds"))
