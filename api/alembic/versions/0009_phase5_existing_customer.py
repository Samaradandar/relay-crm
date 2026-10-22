# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 5 schema (first PR): renewal/cancellation fields on subscriptions, and the tables that
later PRs in this phase write to (subscription change requests, renewals, churn events and the
org's churn reason list, plus per-user notification preferences).

This PR only adds schema. No router/service writes to these tables yet except the churn_reasons
seed below, which mirrors how lost_reasons/fit_items are backfilled for existing orgs.

Conventions: money is integer paise (bigint), matching 0008. Audit history for subscriptions
stays append-only via subscription_versions (0008); the new columns here follow the same
relay_app grants pattern.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

ORG = "uuid NOT NULL REFERENCES orgs(id)"

CHURN_REASONS = [
    "Price",
    "Competitor",
    "Not using the product",
    "Results not as expected",
    "Budget cuts",
    "Business closed",
    "Support issues",
    "Other",
]

STATEMENTS = (
    [
        # ------------------------------------------------------ subscriptions: renewal/cancel
        """ALTER TABLE subscriptions
        ADD COLUMN renewal_status text NOT NULL DEFAULT 'upcoming'
            CHECK (renewal_status IN ('upcoming','renewed','not_renewing')),
        ADD COLUMN cancellation_status text NOT NULL DEFAULT 'none'
            CHECK (cancellation_status IN ('none','requested','cancelled')),
        ADD COLUMN cancellation_requested_at timestamptz,
        ADD COLUMN ended_at timestamptz,
        ADD COLUMN end_reason text""",
        # ------------------------------------------------------------------ subscription_changes
        f"""CREATE TABLE subscription_changes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        subscription_id uuid NOT NULL REFERENCES subscriptions(id),
        source_deal_id uuid REFERENCES deals(id),
        change_type text NOT NULL,
        payload jsonb NOT NULL DEFAULT '{{}}',
        effective_date date,
        status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','rejected')),
        confirmed_by uuid REFERENCES users(id),
        confirmed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now())""",
        "CREATE INDEX subscription_changes_sub_idx "
        "ON subscription_changes (subscription_id, status)",
        # ------------------------------------------------------------------ renewals
        f"""CREATE TABLE renewals (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        subscription_id uuid NOT NULL REFERENCES subscriptions(id),
        outcome text NOT NULL CHECK (outcome IN ('renewed','not_renewing')),
        deal_id uuid REFERENCES deals(id),
        reason text,
        recorded_by uuid NOT NULL REFERENCES users(id),
        recorded_at timestamptz NOT NULL DEFAULT now())""",
        "CREATE INDEX renewals_sub_idx ON renewals (subscription_id, recorded_at)",
        # ------------------------------------------------------------------ churn_events
        f"""CREATE TABLE churn_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        churn_date date NOT NULL,
        reason text NOT NULL,
        notes text,
        monthly_value_lost_paise bigint NOT NULL CHECK (monthly_value_lost_paise >= 0),
        subscription_start date NOT NULL,
        time_as_customer_days integer NOT NULL CHECK (time_as_customer_days >= 0),
        last_renewal_date date,
        recorded_by uuid NOT NULL REFERENCES users(id),
        recorded_at timestamptz NOT NULL DEFAULT now(),
        returned_deal_id uuid REFERENCES deals(id),
        reversed_at timestamptz,
        reversed_by uuid REFERENCES users(id),
        CHECK ((reversed_at IS NULL) = (reversed_by IS NULL)))""",
        "CREATE INDEX churn_events_customer_idx ON churn_events (customer_id, churn_date)",
        # -------------------------------------------------------------- churn_reasons (Settings)
        f"""CREATE TABLE churn_reasons (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        label text NOT NULL,
        active boolean NOT NULL DEFAULT true,
        sort_order integer NOT NULL DEFAULT 0,
        UNIQUE (org_id, label))""",
        # -------------------------------------------------------------- notification_preferences
        f"""CREATE TABLE notification_preferences (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        user_id uuid NOT NULL REFERENCES users(id),
        type text NOT NULL,
        email_enabled boolean NOT NULL DEFAULT true,
        UNIQUE (user_id, type))""",
        # -------------------------------------------------------------- backfill for existing orgs
        # mirrors lost_reasons/fit_items (0003): the default list, seeded once per existing org.
    ]
    + [
        "INSERT INTO churn_reasons (org_id, label, sort_order) "
        f"SELECT id, '{label}', {pos} FROM orgs "
        "ON CONFLICT (org_id, label) DO NOTHING"
        for pos, label in enumerate(CHURN_REASONS, start=1)
    ]
    + [
        # ------------------------------------------------------------------ grants
        # Only the new tables: `GRANT ... ON ALL TABLES` would re-grant UPDATE/DELETE that 0008
        # already revoked from relay_app on the append-only money tables.
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON subscription_changes, renewals, churn_events,
            churn_reasons, notification_preferences TO relay_app;
        REVOKE DELETE ON subscription_changes, renewals, churn_events FROM relay_app;
        END IF; END $$""",
    ]
)


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS notification_preferences"))
    op.execute(sa.text("DROP TABLE IF EXISTS churn_reasons"))
    op.execute(sa.text("DROP TABLE IF EXISTS churn_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS renewals"))
    op.execute(sa.text("DROP TABLE IF EXISTS subscription_changes"))
    op.execute(
        sa.text(
            "ALTER TABLE subscriptions "
            "DROP COLUMN IF EXISTS renewal_status, "
            "DROP COLUMN IF EXISTS cancellation_status, "
            "DROP COLUMN IF EXISTS cancellation_requested_at, "
            "DROP COLUMN IF EXISTS ended_at, "
            "DROP COLUMN IF EXISTS end_reason"
        )
    )
