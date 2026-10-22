# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Leaderboard & Incentive module (approved 6 Oct 2026), replacing P6-05/P6-06's incentive-setting
rule: a TL can only request/pledge; only Admin (reason) or Super admin (reason optional) approves
or sets directly. This PR alters the existing `incentives` table in place (per this prompt's
explicit instruction: "do not create a second table") and adds `incentive_requests`.

Status mapping for existing rows (old 3-value -> new 5-value): 'no_rate' stays 'no_rate';
'pending' (rate live on a Won deal, accruing) becomes 'approved'; 'earned' stays 'earned'. No
existing row was ever `pending_approval` or `approved_pending_closure` -- those states didn't
exist before this prompt.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

ORG = "uuid NOT NULL REFERENCES orgs(id)"

OLD_STATUS = "('no_rate', 'pending', 'earned')"
NEW_STATUS = "('no_rate', 'pending_approval', 'approved_pending_closure', 'approved', 'earned')"

STATEMENTS = [
    # ------------------------------------------------------------------- incentives: alter in place
    "ALTER TABLE incentives DROP CONSTRAINT IF EXISTS incentives_status_check",
    "ALTER TABLE incentives ALTER COLUMN status DROP DEFAULT",
    "UPDATE incentives SET status = 'approved' WHERE status = 'pending'",
    f"ALTER TABLE incentives ADD CONSTRAINT incentives_status_check CHECK (status IN {NEW_STATUS})",
    "ALTER TABLE incentives ALTER COLUMN status SET DEFAULT 'no_rate'",
    """ALTER TABLE incentives
    ADD COLUMN is_pledge boolean NOT NULL DEFAULT false,
    ADD COLUMN source text CHECK (source IN ('tl_request', 'admin_set', 'sa_set',
        'pledge_auto_applied')),
    ADD COLUMN reason text""",
    # value_paise of the open deal, for a pledge's "if won" ₹ preview (won_value_paise is NULL
    # until the deal is actually Won, so a pledge needs its own snapshot of the deal's value).
    "ALTER TABLE incentives ADD COLUMN pledge_value_paise bigint",
    "UPDATE incentives SET source = 'admin_set' WHERE source IS NULL AND status != 'no_rate'",
    # ------------------------------------------------------------------------- incentive_requests
    f"""CREATE TABLE incentive_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    deal_id uuid NOT NULL REFERENCES deals(id),
    requested_by uuid NOT NULL REFERENCES users(id),
    request_type text NOT NULL CHECK (request_type IN ('won_deal_rate', 'active_deal_pledge')),
    rate_bp integer NOT NULL CHECK (rate_bp >= 0 AND rate_bp <= 10000),
    reason text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN
        ('pending', 'approved', 'rejected', 'superseded')),
    decided_by uuid REFERENCES users(id),
    decision_reason text,
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now())""",
    # at most one pending request per (deal, type) -- a second one is refused, not queued.
    "CREATE UNIQUE INDEX incentive_requests_one_pending ON incentive_requests "
    "(deal_id, request_type) WHERE status = 'pending'",
    "CREATE INDEX incentive_requests_status_idx ON incentive_requests (org_id, status)",
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON incentive_requests TO relay_app;
    END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS incentive_requests"))
    op.execute(sa.text("ALTER TABLE incentives DROP CONSTRAINT IF EXISTS incentives_status_check"))
    op.execute(sa.text("UPDATE incentives SET status = 'pending' WHERE status = 'approved'"))
    op.execute(
        sa.text(
            "UPDATE incentives SET status = 'pending' "
            "WHERE status IN ('pending_approval', 'approved_pending_closure')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE incentives ADD CONSTRAINT incentives_status_check "
            f"CHECK (status IN {OLD_STATUS})"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE incentives "
            "DROP COLUMN IF EXISTS is_pledge, "
            "DROP COLUMN IF EXISTS source, "
            "DROP COLUMN IF EXISTS reason, "
            "DROP COLUMN IF EXISTS pledge_value_paise"
        )
    )
