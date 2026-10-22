# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 schema: accounts, contacts, deals (minimal), next steps, Lead Pool, assignment,
transfers, duplicate overrides.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SOURCES = "('Website','Referral','LinkedIn','Event','Cold outbound','Inbound call')"

STATEMENTS = [
    """CREATE TABLE accounts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        name text NOT NULL,
        domain text,
        website text,
        industry text,
        city text,
        account_owner_id uuid REFERENCES users(id),
        merged_into_id uuid REFERENCES accounts(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    "CREATE UNIQUE INDEX accounts_domain_uniq ON accounts (org_id, domain) "
    "WHERE domain IS NOT NULL AND deleted_at IS NULL AND merged_into_id IS NULL",
    "CREATE INDEX accounts_name_idx ON accounts (org_id, lower(name))",
    """CREATE TABLE contacts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        account_id uuid NOT NULL REFERENCES accounts(id),
        name text NOT NULL,
        role text,
        phone_e164 text,
        email text,
        is_decision_maker boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    "CREATE INDEX contacts_account_idx ON contacts (account_id)",
    "CREATE INDEX contacts_email_idx ON contacts (org_id, lower(email)) WHERE email IS NOT NULL",
    "CREATE INDEX contacts_phone_idx ON contacts (org_id, phone_e164) WHERE phone_e164 IS NOT NULL",
    f"""CREATE TABLE deals (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        account_id uuid NOT NULL REFERENCES accounts(id),
        primary_contact_id uuid REFERENCES contacts(id),
        pipeline text NOT NULL DEFAULT 'new_business' CHECK (pipeline IN ('new_business')),
        stage_key text NOT NULL DEFAULT 'lead',
        status text NOT NULL DEFAULT 'pooled' CHECK (status IN ('pooled','open','lost','won')),
        owner_id uuid REFERENCES users(id),
        team_id uuid REFERENCES teams(id),
        lead_source text NOT NULL CHECK (lead_source IN {SOURCES}),
        generated_by uuid,
        generated_source text,
        first_contact_date date,
        assigned_at timestamptz,
        assignment_method text CHECK (assignment_method IN ('rule','round_robin','manual')),
        assignment_rule_id uuid,
        assigned_by uuid,
        version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    "CREATE INDEX deals_owner_idx ON deals (org_id, owner_id, status)",
    "CREATE INDEX deals_team_idx ON deals (org_id, team_id, status)",
    "CREATE INDEX deals_account_idx ON deals (account_id)",
    """CREATE TABLE next_steps (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        deal_id uuid NOT NULL REFERENCES deals(id),
        text text NOT NULL,
        due_date date NOT NULL,
        responsible_id uuid REFERENCES users(id),
        done_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid)""",
    "CREATE INDEX next_steps_deal_idx ON next_steps (deal_id)",
    "CREATE INDEX next_steps_due_idx ON next_steps (responsible_id, due_date) "
    "WHERE done_at IS NULL",
    """CREATE TABLE lead_pool_entries (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        deal_id uuid NOT NULL REFERENCES deals(id),
        entered_at timestamptz NOT NULL DEFAULT now(),
        entered_by uuid,
        entered_source text NOT NULL DEFAULT 'manual entry',
        routed_team_id uuid,
        waiting_reason text CHECK (waiting_reason IN
            ('no_rule_match','all_reps_at_cap','no_active_reps')),
        tl_notified boolean NOT NULL DEFAULT false,
        manual_hold boolean NOT NULL DEFAULT false,
        left_at timestamptz,
        left_reason text CHECK (left_reason IN ('assigned','deleted')))""",
    "CREATE UNIQUE INDEX pool_open_uniq ON lead_pool_entries (deal_id) WHERE left_at IS NULL",
    "CREATE INDEX pool_waiting_idx ON lead_pool_entries (org_id, entered_at) WHERE left_at IS NULL",
    """CREATE TABLE assignment_rules (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        name text NOT NULL,
        priority integer NOT NULL,
        active boolean NOT NULL DEFAULT true,
        match jsonb NOT NULL DEFAULT '{}',
        target_type text NOT NULL CHECK (target_type IN ('team','group','user')),
        target_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    """CREATE TABLE rep_groups (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        name text NOT NULL,
        team_id uuid REFERENCES teams(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    """CREATE TABLE rep_group_members (
        group_id uuid NOT NULL REFERENCES rep_groups(id),
        user_id uuid NOT NULL REFERENCES users(id),
        PRIMARY KEY (group_id, user_id))""",
    """CREATE TABLE rr_pointers (
        org_id uuid NOT NULL REFERENCES orgs(id),
        scope_type text NOT NULL CHECK (scope_type IN ('group','team')),
        scope_id uuid NOT NULL,
        last_assigned_user_id uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, scope_type, scope_id))""",
    """CREATE TABLE rep_caps (
        user_id uuid PRIMARY KEY REFERENCES users(id),
        org_id uuid NOT NULL REFERENCES orgs(id),
        cap integer NOT NULL CHECK (cap >= 0),
        updated_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE assignment_settings (
        org_id uuid PRIMARY KEY REFERENCES orgs(id),
        default_team_id uuid REFERENCES teams(id),
        default_cap integer NOT NULL DEFAULT 25 CHECK (default_cap >= 0),
        updated_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE transfer_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        deal_id uuid NOT NULL REFERENCES deals(id),
        direction text NOT NULL CHECK (direction IN ('give','take')),
        from_team_id uuid NOT NULL,
        to_team_id uuid NOT NULL,
        from_owner_id uuid,
        proposed_owner_id uuid,
        requested_by uuid NOT NULL REFERENCES users(id),
        reason text NOT NULL,
        other_tl_comment text,
        other_tl_comment_by uuid,
        approval_id uuid,
        status text NOT NULL DEFAULT 'open'
            CHECK (status IN ('open','approved','rejected','cancelled','stale')),
        created_at timestamptz NOT NULL DEFAULT now(),
        decided_at timestamptz)""",
    "CREATE UNIQUE INDEX transfer_one_open_uniq ON transfer_requests (deal_id) "
    "WHERE status = 'open'",
    """CREATE TABLE duplicate_overrides (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        deal_id uuid NOT NULL REFERENCES deals(id),
        match_type text NOT NULL,
        matched_account_id uuid NOT NULL,
        in_scope boolean NOT NULL,
        reason text NOT NULL,
        by_user_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""",
    # Fuzzy name search: use pg_trgm when the server has it (the app falls back to an exact
    # Python implementation of the same trigram maths when it does not).
    """DO $$ BEGIN
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        CREATE INDEX accounts_name_trgm ON accounts USING gin (lower(name) gin_trgm_ops);
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pg_trgm not available: fuzzy search falls back to the Python implementation';
    END $$""",
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO relay_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relay_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM relay_app;
        END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    raise NotImplementedError("Not reversible; restore from backup.")
