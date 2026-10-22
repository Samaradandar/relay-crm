# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 base schema: orgs, users, teams, permissions, audit, outbox, notifications,
approvals, files. Seeds the org row and the permission matrix.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op
from app.core.config import get_settings
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_rows

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ROLES = "('user','team_leader','admin','super_admin')"

STATEMENTS = [
    """CREATE TABLE orgs (
        id uuid PRIMARY KEY,
        name text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE teams (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        name text NOT NULL,
        leader_id uuid,
        colour text,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    f"""CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        email text NOT NULL,
        name text NOT NULL,
        role text NOT NULL CHECK (role IN {ROLES}),
        team_id uuid REFERENCES teams(id),
        is_active boolean NOT NULL DEFAULT true,
        auth_sub text,
        last_login_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    "ALTER TABLE teams ADD CONSTRAINT teams_leader_fk FOREIGN KEY (leader_id) REFERENCES users(id)",
    "CREATE UNIQUE INDEX users_email_uniq ON users (org_id, lower(email)) WHERE deleted_at IS NULL",
    "CREATE INDEX users_team_idx ON users (team_id)",
    "CREATE UNIQUE INDEX teams_leader_uniq ON teams (leader_id) "
    "WHERE leader_id IS NOT NULL AND deleted_at IS NULL",
    "CREATE UNIQUE INDEX teams_name_uniq ON teams (org_id, lower(name)) WHERE deleted_at IS NULL",
    """CREATE TABLE team_memberships (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        user_id uuid NOT NULL REFERENCES users(id),
        team_id uuid NOT NULL REFERENCES teams(id),
        started_at timestamptz NOT NULL DEFAULT now(),
        ended_at timestamptz,
        moved_by uuid,
        reason text)""",
    "CREATE UNIQUE INDEX team_memberships_open_uniq ON team_memberships (user_id) "
    "WHERE ended_at IS NULL",
    """CREATE TABLE permission_keys (
        key text PRIMARY KEY,
        action_class text NOT NULL CHECK (action_class IN ('important','routine','view')),
        description text NOT NULL DEFAULT '')""",
    f"""CREATE TABLE role_permissions (
        role text NOT NULL CHECK (role IN {ROLES}),
        key text NOT NULL REFERENCES permission_keys(key),
        scope text NOT NULL CHECK (scope IN ('none','own','team','all')),
        mode text NOT NULL CHECK (mode IN ('allow','needs_approval','request_only')),
        own_deals_only boolean NOT NULL DEFAULT false,
        not_own_deals boolean NOT NULL DEFAULT false,
        routed_to text,
        PRIMARY KEY (role, key))""",
    """CREATE TABLE audit_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
        org_id uuid NOT NULL REFERENCES orgs(id),
        occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        actor_id uuid,
        actor_role text NOT NULL,
        on_behalf_of uuid,
        action_key text NOT NULL,
        entity_type text NOT NULL,
        entity_id text NOT NULL,
        outcome text NOT NULL DEFAULT 'ok' CHECK (outcome IN ('ok','denied','approval_requested')),
        old_value jsonb,
        new_value jsonb,
        reason text,
        approval_id uuid,
        request_id text,
        ip text,
        user_agent text,
        origin text NOT NULL DEFAULT 'screen'
            CHECK (origin IN ('screen','import','integration','system_job')),
        note text,
        prev_hash text NOT NULL,
        row_hash text NOT NULL)""",
    "CREATE INDEX audit_entity_idx ON audit_log (org_id, entity_type, entity_id, seq DESC)",
    "CREATE INDEX audit_actor_idx ON audit_log (org_id, actor_id, seq DESC)",
    """CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$""",
    """CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()""",
    """CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable()""",
    """CREATE TABLE outbox_events (
        id bigserial PRIMARY KEY,
        org_id uuid NOT NULL REFERENCES orgs(id),
        type text NOT NULL,
        payload jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now(),
        available_at timestamptz NOT NULL DEFAULT now(),
        processed_at timestamptz,
        attempts integer NOT NULL DEFAULT 0,
        last_error text,
        status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed')))""",
    "CREATE INDEX outbox_pending_idx ON outbox_events (available_at) WHERE status = 'pending'",
    """CREATE TABLE notifications (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        user_id uuid NOT NULL REFERENCES users(id),
        type text NOT NULL,
        title text NOT NULL,
        body text NOT NULL DEFAULT '',
        link text,
        dedupe_key text,
        read_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE INDEX notifications_user_idx ON notifications (user_id, created_at DESC)",
    "CREATE UNIQUE INDEX notifications_dedupe_uniq ON notifications (user_id, dedupe_key) "
    "WHERE dedupe_key IS NOT NULL",
    """CREATE TABLE notification_settings (
        user_id uuid NOT NULL REFERENCES users(id),
        type text NOT NULL,
        in_app boolean NOT NULL DEFAULT true,
        email boolean NOT NULL DEFAULT true,
        PRIMARY KEY (user_id, type))""",
    """CREATE TABLE approval_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        type text NOT NULL,
        action_key text NOT NULL,
        target_entity_type text,
        target_entity_id text,
        record_owner_id uuid,
        payload jsonb NOT NULL DEFAULT '{}',
        requested_by uuid NOT NULL REFERENCES users(id),
        request_reason text NOT NULL,
        routed_to text NOT NULL CHECK (routed_to IN ('team_leader','admin_pool')),
        team_id uuid,
        status text NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','approved','rejected','cancelled')),
        decided_by uuid REFERENCES users(id),
        decision_reason text,
        created_at timestamptz NOT NULL DEFAULT now(),
        decided_at timestamptz)""",
    "CREATE INDEX approvals_status_idx ON approval_requests (org_id, status, created_at DESC)",
    """CREATE TABLE files (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES orgs(id),
        owner_entity_type text NOT NULL,
        owner_entity_id text NOT NULL,
        key text NOT NULL UNIQUE,
        filename text NOT NULL,
        content_type text NOT NULL,
        size bigint NOT NULL,
        status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','uploaded')),
        uploaded_by uuid NOT NULL REFERENCES users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    "CREATE INDEX files_owner_idx ON files (owner_entity_type, owner_entity_id)",
]

# The running app uses the restricted `relay_app` role when it exists (local compose creates it,
# RDS gets it from Terraform). It can never update or delete audit rows.
GRANTS = [
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
        GRANT USAGE ON SCHEMA public TO relay_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO relay_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relay_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM relay_app;
        END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))

    settings = get_settings()
    op.execute(
        sa.text("INSERT INTO orgs (id, name) VALUES (:id, 'Relay')").bindparams(id=settings.org_id)
    )
    for key in KEYS.values():
        op.execute(
            sa.text(
                "INSERT INTO permission_keys (key, action_class, description) VALUES (:k, :c, :d)"
            ).bindparams(k=key.key, c=key.action_class.value, d=key.description)
        )
    for role, key_name, rule in seed_rows():
        op.execute(
            sa.text(
                "INSERT INTO role_permissions "
                "(role, key, scope, mode, own_deals_only, not_own_deals, routed_to) "
                "VALUES (:r, :k, :s, :m, :o, :n, :t)"
            ).bindparams(
                r=role.value,
                k=key_name,
                s=rule.scope.value,
                m=rule.mode.value,
                o=rule.own_deals_only,
                n=rule.not_own_deals,
                t=rule.routed_to,
            )
        )
    for stmt in GRANTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    raise NotImplementedError("Phase 1 base schema is not reversible; restore from backup.")
