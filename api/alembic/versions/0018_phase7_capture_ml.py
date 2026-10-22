# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 7 schema (first PR): capture (website form, CSV upload, email-to-lead) and the ML
registry (training data, models, scores). This PR is schema only -- no service/router writes
yet, mirroring how Phase 5 and 6 opened with a schema-only PR.

capture_lead() (capture/service.py, next PR) is the only writer of capture_events/capture_reviews
and the only caller of leads.service.create_lead(); nothing else may create a captured lead.

Live infra not yet available in this environment (confirmed with Samar): the real website form,
a live inbox for email-to-lead, and the ML history/ship-margin thresholds. Those PRs are scoped
down accordingly (see capture/service.py and the Phase 7 status notes) but the schema below is
still built in full so later PRs don't need another migration.
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

ORG = "uuid NOT NULL REFERENCES orgs(id)"

STATEMENTS = [
    # ------------------------------------------------------------------------------- deals
    """ALTER TABLE deals
    ADD COLUMN source_kind text CHECK (source_kind IN ('rep','website','csv','email')),
    ADD COLUMN capture_source_id uuid""",
    "UPDATE deals SET source_kind = 'rep' WHERE source_kind IS NULL",
    # ------------------------------------------------------------------------- capture_sources
    f"""CREATE TABLE capture_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    kind text NOT NULL CHECK (kind IN ('website', 'email', 'csv_template')),
    name text NOT NULL,
    source_key text UNIQUE,
    allowed_origins text[] NOT NULL DEFAULT '{{}}',
    mapping jsonb NOT NULL DEFAULT '{{}}',
    default_next_step text,
    default_lead_source text,
    active boolean NOT NULL DEFAULT true,
    created_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE UNIQUE INDEX capture_sources_key_uniq ON capture_sources (source_key) "
    "WHERE source_key IS NOT NULL",
    # -------------------------------------------------------------------------- capture_events
    f"""CREATE TABLE capture_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    source_id uuid REFERENCES capture_sources(id),
    kind text NOT NULL CHECK (kind IN ('website', 'csv', 'email')),
    idempotency_key text,
    payload_s3_key text,
    payload_preview jsonb NOT NULL DEFAULT '{{}}',
    outcome text NOT NULL CHECK (outcome IN
        ('created', 'duplicate', 'existing_customer', 'needs_review', 'rejected_spam', 'invalid',
         'test')),
    deal_id uuid REFERENCES deals(id),
    matched_account_id uuid REFERENCES accounts(id),
    error text,
    created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE UNIQUE INDEX capture_events_idem_uniq ON capture_events (source_id, idempotency_key) "
    "WHERE idempotency_key IS NOT NULL",
    "CREATE INDEX capture_events_outcome_idx ON capture_events (org_id, source_id, outcome, "
    "created_at DESC)",
    # ------------------------------------------------------------------------- capture_reviews
    f"""CREATE TABLE capture_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    capture_event_id uuid NOT NULL REFERENCES capture_events(id),
    matched_deal_id uuid REFERENCES deals(id),
    routed_team_id uuid REFERENCES teams(id),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'created', 'merged',
        'discarded')),
    decided_by uuid REFERENCES users(id),
    decided_at timestamptz,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE INDEX capture_reviews_open_idx ON capture_reviews (org_id, routed_team_id, status)",
    # ----------------------------------------------------------------------------- csv_imports
    f"""CREATE TABLE csv_imports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    file_s3_key text NOT NULL,
    filename text NOT NULL,
    mapping jsonb NOT NULL DEFAULT '{{}}',
    default_team_id uuid REFERENCES teams(id),
    status text NOT NULL DEFAULT 'uploaded' CHECK (status IN
        ('uploaded', 'mapped', 'previewed', 'running', 'stopped', 'done', 'failed')),
    counts jsonb NOT NULL DEFAULT '{{}}',
    created_by uuid NOT NULL REFERENCES users(id),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz)""",
    "CREATE INDEX csv_imports_org_idx ON csv_imports (org_id, created_at DESC)",
    # ------------------------------------------------------------------------- csv_import_rows
    f"""CREATE TABLE csv_import_rows (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    import_id uuid NOT NULL REFERENCES csv_imports(id),
    row_number integer NOT NULL,
    raw jsonb NOT NULL DEFAULT '{{}}',
    outcome text CHECK (outcome IN
        ('ready', 'duplicate', 'needs_review', 'existing_customer', 'invalid', 'created')),
    deal_id uuid REFERENCES deals(id),
    errors text[] NOT NULL DEFAULT '{{}}',
    UNIQUE (import_id, row_number))""",
    "CREATE INDEX csv_import_rows_import_idx ON csv_import_rows (import_id, outcome)",
    # ------------------------------------------------------------------------- inbound_emails
    f"""CREATE TABLE inbound_emails (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    message_id text NOT NULL,
    from_email text NOT NULL,
    from_name text,
    subject text,
    s3_key text,
    spam_verdict text,
    outcome text NOT NULL CHECK (outcome IN
        ('created', 'duplicate', 'existing_customer', 'needs_review', 'ignored', 'invalid')),
    deal_id uuid REFERENCES deals(id),
    forwarded_by_user_id uuid REFERENCES users(id),
    error text,
    created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE UNIQUE INDEX inbound_emails_message_id_uniq ON inbound_emails (org_id, message_id)",
    # --------------------------------------------------------------------------- ml_datasets
    f"""CREATE TABLE ml_datasets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    as_of timestamptz NOT NULL,
    s3_key text,
    rows integer NOT NULL DEFAULT 0,
    won integer NOT NULL DEFAULT 0,
    lost integer NOT NULL DEFAULT 0,
    feature_list text[] NOT NULL DEFAULT '{{}}',
    built_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now())""",
    # ----------------------------------------------------------------------------- ml_models
    f"""CREATE TABLE ml_models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    version integer NOT NULL,
    dataset_id uuid NOT NULL REFERENCES ml_datasets(id),
    metrics jsonb NOT NULL DEFAULT '{{}}',
    baseline_metrics jsonb NOT NULL DEFAULT '{{}}',
    status text NOT NULL DEFAULT 'candidate' CHECK (status IN
        ('candidate', 'shipped', 'retired', 'not_shipped')),
    calibration_s3_key text,
    trained_by uuid REFERENCES users(id),
    shipped_by uuid REFERENCES users(id),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    shipped_at timestamptz,
    retired_at timestamptz,
    UNIQUE (org_id, version))""",
    "CREATE INDEX ml_models_status_idx ON ml_models (org_id, status)",
    # ---------------------------------------------------------------------------- deal_scores
    f"""CREATE TABLE deal_scores (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    deal_id uuid NOT NULL REFERENCES deals(id),
    model_id uuid NOT NULL REFERENCES ml_models(id),
    probability_bp integer NOT NULL CHECK (probability_bp >= 0 AND probability_bp <= 10000),
    factors jsonb NOT NULL DEFAULT '[]',
    scored_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE INDEX deal_scores_deal_idx ON deal_scores (deal_id, scored_at DESC)",
    # ------------------------------------------------------------------------------- grants
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON capture_sources, capture_events, capture_reviews,
        csv_imports, csv_import_rows, inbound_emails, ml_datasets, ml_models, deal_scores
        TO relay_app;
    REVOKE DELETE ON capture_events, inbound_emails FROM relay_app;
    END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    for table in (
        "deal_scores", "ml_models", "ml_datasets", "inbound_emails", "csv_import_rows",
        "csv_imports", "capture_reviews", "capture_events", "capture_sources",
    ):  # fmt: skip
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
    op.execute(
        sa.text(
            "ALTER TABLE deals DROP COLUMN IF EXISTS source_kind, "
            "DROP COLUMN IF EXISTS capture_source_id"
        )
    )
