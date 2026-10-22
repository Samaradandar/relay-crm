# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 6 PR1 (P6-01/04/05/06/08): schema only, no service/router writes yet.

Won credit is captured at the moment a deal is Won (won_by_user_id/won_team_id/won_value_paise
on deals) so later reassignment never moves it (PRD "decisions already made"). Backfilled below
from each already-Won deal's current owner_id/team_id/value_paise -- the best information
available for deals Won before this phase; Samar should spot-check reassigned-then-Won deals
from before this migration, since their current owner may not be who actually won them.

rep_month_stats is the read model the leaderboard/dashboards/reports read from (never raw
deals per request). targets and incentives are append-audited via the platform audit trail, not
here. user_activity_days backs the "daily active use" measure (PRD section 12).

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

ORG = "uuid NOT NULL REFERENCES orgs(id)"

STATEMENTS = [
    # ------------------------------------------------------------ deals: Won-credit snapshot
    """ALTER TABLE deals
    ADD COLUMN won_by_user_id uuid REFERENCES users(id),
    ADD COLUMN won_team_id uuid REFERENCES teams(id),
    ADD COLUMN won_value_paise bigint CHECK (won_value_paise >= 0)""",
    """UPDATE deals SET won_by_user_id = owner_id, won_team_id = team_id,
    won_value_paise = value_paise WHERE won_at IS NOT NULL AND won_by_user_id IS NULL""",
    # ------------------------------------------------------------------------ rep_month_stats
    f"""CREATE TABLE rep_month_stats (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    user_id uuid NOT NULL REFERENCES users(id),
    month date NOT NULL,
    team_id uuid REFERENCES teams(id),
    active_deals integer NOT NULL DEFAULT 0,
    won_deals integer NOT NULL DEFAULT 0,
    revenue_current_paise bigint NOT NULL DEFAULT 0,
    revenue_projected_paise bigint NOT NULL DEFAULT 0,
    best_to_date_paise bigint NOT NULL DEFAULT 0,
    target_paise bigint,
    rank integer,
    is_closed boolean NOT NULL DEFAULT false,
    computed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, month))""",
    "CREATE INDEX rep_month_stats_month_idx ON rep_month_stats (org_id, month)",
    # ------------------------------------------------------------------------------- targets
    f"""CREATE TABLE targets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    user_id uuid NOT NULL REFERENCES users(id),
    month date NOT NULL,
    amount_paise bigint NOT NULL CHECK (amount_paise >= 0),
    set_by uuid NOT NULL REFERENCES users(id),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, month))""",
    # ----------------------------------------------------------------------------- incentives
    f"""CREATE TABLE incentives (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    deal_id uuid NOT NULL REFERENCES deals(id),
    user_id uuid NOT NULL REFERENCES users(id),
    rate_bp integer CHECK (rate_bp >= 0 AND rate_bp <= 10000),
    won_value_paise bigint NOT NULL DEFAULT 0,
    received_paise bigint NOT NULL DEFAULT 0,
    pending_amount_paise bigint NOT NULL DEFAULT 0,
    earned_amount_paise bigint NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'no_rate' CHECK (status IN ('no_rate', 'pending', 'earned')),
    set_by uuid REFERENCES users(id),
    earned_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (deal_id))""",
    "CREATE INDEX incentives_user_idx ON incentives (org_id, user_id, status)",
    # ------------------------------------------------------------------------ user_activity_days
    f"""CREATE TABLE user_activity_days (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    user_id uuid NOT NULL REFERENCES users(id),
    day date NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, day))""",
    # ------------------------------------------------------------------------------- grants
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON rep_month_stats, targets, incentives,
        user_activity_days TO relay_app;
    REVOKE DELETE ON targets FROM relay_app;
    END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS user_activity_days"))
    op.execute(sa.text("DROP TABLE IF EXISTS incentives"))
    op.execute(sa.text("DROP TABLE IF EXISTS targets"))
    op.execute(sa.text("DROP TABLE IF EXISTS rep_month_stats"))
    op.execute(
        sa.text(
            "ALTER TABLE deals "
            "DROP COLUMN IF EXISTS won_by_user_id, "
            "DROP COLUMN IF EXISTS won_team_id, "
            "DROP COLUMN IF EXISTS won_value_paise"
        )
    )
